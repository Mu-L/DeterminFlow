"""
Rule 管理器 - 管理 rules 的加载、查询和注入
"""
import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from src.extension_api.registrar import OwnedPath

from .models import Rule
from .loader import RuleLoader

if TYPE_CHECKING:
    from .config_manager import RuleConfigManager

logger = logging.getLogger(__name__)


def _slugify_name(name: str) -> str:
    """从可读名称生成安全的文件系统 ID（slug）。

    保留 ASCII 字母数字和连字符，空格转连字符，中文等非 ASCII 字符用哈希替代。
    若输入为空或无法提取有效字符，返回基于内容哈希的 ID。
    """
    if not name:
        return f"rule-{hashlib.sha256(b'unnamed').hexdigest()[:8]}"

    # 保留 ASCII 字母数字和连字符，空格转连字符
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    # 压缩连续连字符
    slug = re.sub(r"-{2,}", "-", slug)

    if slug and len(slug) >= 2:
        return slug[:64]  # 限制长度

    # fallback: 用名称哈希生成稳定 ID
    return f"rule-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"


class RuleManager:
    """
    Rule 管理器

    负责：
    1. 加载和管理 rules
    2. 根据 agent 类型筛选 rules
    3. 构建注入到 system prompt 的 rules section
    """

    def __init__(
        self,
        rules_dir: Path,
        config_manager: "RuleConfigManager | None" = None,
        *,
        resource_roots: list[OwnedPath] | None = None,
        owner_enabled: Callable[[str], bool] | None = None,
    ):
        self.rules_dir = rules_dir
        self.loader = RuleLoader(
            rules_dir,
            resource_roots=resource_roots,
            owner_enabled=owner_enabled,
        )
        self.config_manager = config_manager
        self._rules: dict[str, Rule] = {}
        self._load_all_rules()

    def _load_all_rules(self):
        """加载所有 rules"""
        rules = self.loader.load_all()
        self._rules = {r.id: r for r in rules}
        logger.info(f"RuleManager 已加载 {len(self._rules)} 个 rules")

        # 同步配置文件
        if self.config_manager:
            rule_ids = list(self._rules.keys())
            self.config_manager.sync_with_directory(rule_ids)

    def reload(self):
        """重新加载所有 rules"""
        if self.config_manager:
            self.config_manager.reload()
        self._load_all_rules()
        logger.info("Rules 已重新加载")

    @staticmethod
    def from_raw_content(raw_content: str) -> Rule | None:
        """
        从 LLM 产出的完整 RULE.md 文本（含 YAML frontmatter）解析为 Rule 对象。

        这是 LLM 创建规则的统一入口：LLM 产出完整的 RULE.md 文本，
        此方法解析并返回 Rule 对象，后续由 create_rule 保存到磁盘。

        Args:
            raw_content: LLM 产出的完整 RULE.md 内容（frontmatter + body）

        Returns:
            Rule 对象，解析失败返回 None
        """
        # 复用 RuleLoader 的 frontmatter 解析逻辑，避免重复实现
        frontmatter, body = RuleLoader._parse_frontmatter(raw_content)
        if not frontmatter:
            logger.warning("from_raw_content: 缺少 YAML frontmatter 或解析失败")
            return None

        try:
            name = frontmatter.get("name", "")
            description = frontmatter.get("description", "")
            summary = frontmatter.get("summary", "")
            metadata = frontmatter.get("metadata", {})

            version = metadata.get("version", "1.0")
            author = metadata.get("author", "")

            # 生成安全的 slug ID：优先用 frontmatter 中的 id，否则从 name 推导
            rule_id = frontmatter.get("id") or _slugify_name(name)

            return Rule(
                id=rule_id,
                name=name,
                description=description,
                summary=summary,
                content=body.strip(),
                version=version,
                author=author,
            )

        except Exception as e:
            logger.error(f"from_raw_content 解析失败: {e}")
            return None

    def get_rule(self, rule_id: str) -> Rule | None:
        """获取指定 rule"""
        return self._rules.get(rule_id)

    def list_all(self) -> list[Rule]:
        """获取所有 rules"""
        return list(self._rules.values())

    def get_rule_file(self, rule_id: str, file_path: str) -> str | None:
        """Read a supporting file without allowing escape from the Rule bundle."""
        rule = self._rules.get(rule_id)
        if rule is None:
            return None
        raw_rule_dir = rule.metadata.get("rule_dir")
        if not raw_rule_dir:
            return None
        rule_dir = Path(raw_rule_dir).resolve()
        target = (rule_dir / file_path).resolve()
        if not target.is_relative_to(rule_dir) or not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def list_by_agent_type(self, agent_type: str, workflow_context: bool = False,
                           visible_rule_group_ids: list[str] | None = None) -> list[Rule]:
        """
        获取适用于指定 agent 类型的 rules（所有过滤条件从 config 读取）。

        Args:
            agent_type: agent 类型（如 "coder", "researcher"）
            workflow_context: 是否为工作流上下文（影响 workflow_only 规则可见性）
            visible_rule_group_ids: agent 可见的规则组 ID 列表。
                - None：不做组过滤（向后兼容，如 API 查询场景）
                - 空列表：不返回任何 rule
                - 非空：只返回 group_ids 与该列表有交集的 rule

        Returns:
            按名称排序的 rule 列表
        """
        rules = [
            rule for rule in self._rules.values()
            if self._passes_filters(rule, workflow_context, visible_rule_group_ids)
        ]
        return sorted(rules, key=lambda r: r.name)

    def _passes_filters(
        self,
        rule,
        workflow_context: bool,
        visible_rule_group_ids: list[str] | None,
    ) -> bool:
        """检查 rule 是否通过 enabled / workflow_only / 组可见性过滤。"""
        # 1. 从 config 读取配置
        enabled = True
        wf_only = False
        rule_group_ids: list[str] = []

        if self.config_manager:
            rule_config = self.config_manager.get_rule_configs().get(rule.id, {})
            enabled = rule_config.get("enabled", True)
            wf_only = self.config_manager.get_workflow_only(rule.id)
            if wf_only is None:
                wf_only = rule_config.get("workflow_only", False)
            rule_group_ids = self.config_manager.get_rule_group_ids(rule.id)

        # 2. enabled 过滤
        if not enabled:
            return False

        # 3. workflow_only 过滤
        if wf_only and not workflow_context:
            return False

        # 4. 组可见性过滤：agent 可见组 ∩ rule 所属组
        if visible_rule_group_ids is not None:
            if not rule_group_ids:
                rule_group_ids = ["default"]
            if not set(visible_rule_group_ids) & set(rule_group_ids):
                return False

        return True

    def build_rules_section(self, agent_type: str, preamble: str = "",
                            workflow_context: bool = False,
                            visible_rule_group_ids: list[str] | None = None) -> str:
        """
        为指定 agent 类型构建 rules section 内容

        Args:
            agent_type: agent 类型
            preamble: 引导语（可配置）
            workflow_context: 是否为工作流上下文
            visible_rule_group_ids: agent 可见的规则组 ID 列表（透传给 list_by_agent_type）

        Returns:
            格式化的 markdown 文本
        """
        rules = self.list_by_agent_type(agent_type, workflow_context=workflow_context,
                                        visible_rule_group_ids=visible_rule_group_ids)
        if not rules:
            return ""

        parts = ["<rules>"]

        # 添加引导语
        if preamble:
            parts.append(preamble)
        else:
            parts.append("⚠️ **CRITICAL: You MUST strictly follow these rules. Violations are not acceptable.**")

        parts.append("")

        # 添加每个 rule
        for rule in rules:
            parts.append(f"## Rule: {rule.name}")
            parts.append(f"**描述**: {rule.description}")
            parts.append("")
            parts.append(rule.content)
            parts.append("")

        parts.append("</rules>")

        return "\n".join(parts)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total": len(self._rules),
        }

    def get_rules_summary(self, agent_type: str | None = None) -> list[dict]:
        """
        获取 rules 摘要信息（用于 API 和前端展示）

        Args:
            agent_type: 可选，筛选适用于指定 agent 类型的 rules

        Returns:
            rule 摘要列表
        """
        if agent_type:
            rules = self.list_by_agent_type(agent_type)
        else:
            rules = self.list_all()

        result = []
        for r in rules:
            summary = {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "version": r.version,
                "author": r.author,
                "content_length": len(r.content),
                "resource_owner": r.metadata.get("resource_owner", "user"),
                "resource_read_only": r.metadata.get(
                    "resource_read_only",
                    False,
                ),
            }

            # 添加配置信息（所有配置字段从 config 读取）
            if self.config_manager:
                config = self.config_manager.get_config(r.id)
                summary["config"] = config
                summary["group_ids"] = self.config_manager.get_rule_group_ids(r.id)
                summary["agent_types"] = self.config_manager.get_agent_types(r.id)

                wf_only = self.config_manager.get_workflow_only(r.id)
                if wf_only is not None:
                    summary["workflow_only"] = wf_only
                else:
                    rule_config = self.config_manager.get_rule_configs().get(r.id, {})
                    summary["workflow_only"] = rule_config.get("workflow_only", False)

                rule_config = self.config_manager.get_rule_configs().get(r.id, {})
                summary["enabled"] = rule_config.get("enabled", True)
            else:
                summary["config"] = {}
                summary["group_ids"] = []
                summary["agent_types"] = []
                summary["workflow_only"] = False
                summary["enabled"] = True

            result.append(summary)

        return result

    def create_rule(self, rule_data: dict) -> Rule:
        """
        创建新 rule

        Args:
            rule_data: rule 数据

        Returns:
            创建的 Rule 对象
        """
        rule = Rule.from_dict(rule_data)

        if self.loader.save_rule(rule):
            self._rules[rule.id] = rule
            logger.info(f"成功创建 rule: {rule.id}")
            return rule
        else:
            raise Exception(f"保存 rule 失败: {rule.id}")

    def update_rule(self, rule_id: str, updates: dict) -> Rule:
        """
        更新 rule

        Args:
            rule_id: rule ID
            updates: 要更新的字段

        Returns:
            更新后的 Rule 对象
        """
        rule = self.get_rule(rule_id)
        if not rule:
            raise Exception(f"Rule 不存在: {rule_id}")
        if rule.metadata.get("resource_read_only"):
            raise PermissionError(
                f"Plugin Rule 是只读资源，不能直接修改: {rule_id}"
            )

        # 更新字段（白名单限制，防止修改 id、metadata 等不可变字段）
        ALLOWED_FIELDS = {"name", "description", "summary", "content", "version", "author"}
        for key, value in updates.items():
            if key not in ALLOWED_FIELDS:
                logger.warning(f"update_rule: 忽略不允许更新的字段 '{key}'")
                continue
            if not hasattr(rule, key):
                logger.warning(f"update_rule: Rule 对象不存在字段 '{key}'")
                continue
            setattr(rule, key, value)

        if self.loader.save_rule(rule):
            self._rules[rule_id] = rule
            logger.info(f"成功更新 rule: {rule_id}")
            return rule
        else:
            raise Exception(f"保存 rule 失败: {rule_id}")

    def delete_rule(self, rule_id: str) -> bool:
        """
        删除 rule

        Args:
            rule_id: rule ID

        Returns:
            True 如果删除成功
        """
        if rule_id not in self._rules:
            return False
        if self._rules[rule_id].metadata.get("resource_read_only"):
            raise PermissionError(
                f"Plugin Rule 是只读资源，不能直接删除: {rule_id}"
            )

        if self.loader.delete_rule(rule_id):
            del self._rules[rule_id]

            # 同时删除配置
            if self.config_manager:
                self.config_manager.remove_rule(rule_id)

            logger.info(f"成功删除 rule: {rule_id}")
            return True

        return False

    def initialize_if_empty(self):
        """如果 rules 目录为空，创建示例 rules"""
        if len(self._rules) > 0:
            return

        logger.info("Rules 目录为空，创建示例 rules...")

        # 示例 rule 1: 禁止破坏性操作
        rule1 = Rule(
            id="no-destructive-operations",
            name="禁止破坏性操作",
            description="禁止执行可能导致数据丢失或系统损坏的操作",
            content=self._get_example_rule1_content(),
            version="1.0",
            author="system",
        )

        # 示例 rule 2: 代码审查标准
        rule2 = Rule(
            id="code-review-standards",
            name="代码审查标准",
            description="代码提交前必须满足的质量标准",
            content=self._get_example_rule2_content(),
            version="1.0",
            author="system",
        )

        for rule in [rule1, rule2]:
            try:
                self.loader.save_rule(rule)
                self._rules[rule.id] = rule
                logger.info(f"创建示例 rule: {rule.id}")
            except Exception as e:
                logger.error(f"创建示例 rule 失败: {e}")

    def _get_example_rule1_content(self) -> str:
        return """## 规则说明

在执行任何操作前，必须评估其潜在风险。以下操作被严格禁止：

### 禁止的操作

1. **删除文件或目录**
   - 不得使用 `rm`、`del`、`shutil.rmtree` 等删除命令
   - 不得清空文件内容
   - 例外：明确标记为临时文件的目录（如 `/tmp`）

2. **修改系统配置**
   - 不得修改系统级配置文件
   - 不得更改环境变量
   - 不得安装或卸载系统软件

3. **数据库操作**
   - 不得执行 `DROP TABLE` 或 `TRUNCATE` 命令
   - 不得删除生产数据
   - 不得修改数据库架构

4. **网络操作**
   - 不得向外部服务发送敏感数据
   - 不得执行未经授权的网络请求

### 安全操作流程

如果任务确实需要执行潜在危险的操作：

1. **明确告知用户**风险和后果
2. **请求明确授权**
3. **提供回滚方案**
4. **记录操作日志**

### 违规后果

违反此规则将导致：
- 任务立即终止
- 记录违规行为
- 可能的系统回滚
"""

    def _get_example_rule2_content(self) -> str:
        return """## 代码质量标准

所有代码在提交前必须满足以下标准：

### 1. 代码风格

- 遵循项目的代码风格指南（如 PEP 8、ESLint 配置）
- 使用一致的命名规范
- 保持适当的代码缩进和格式

### 2. 文档要求

- 所有公共函数必须有文档字符串
- 复杂逻辑必须有注释说明
- README 文件必须保持更新

### 3. 测试覆盖

- 新功能必须包含单元测试
- 测试覆盖率不得低于 80%
- 所有测试必须通过

### 4. 错误处理

- 必须处理可预见的异常
- 错误信息必须清晰明确
- 不得使用空的 except 块

### 5. 安全性

- 不得硬编码敏感信息（密码、API 密钥）
- 必须验证用户输入
- 使用参数化查询防止 SQL 注入

### 6. 性能考虑

- 避免不必要的循环嵌套
- 合理使用缓存
- 注意内存泄漏

### 审查流程

1. 自我审查：提交前检查以上所有项
2. 代码审查：至少一位同事审查
3. 自动化检查：通过 linter 和测试
4. 最终确认：确保符合所有标准
"""
