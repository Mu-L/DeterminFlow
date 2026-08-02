"""
Skill 管理器 - 支持渐进式披露（Progressive Disclosure）

根据 Agent Skills 标准，skills 分三个阶段加载：
1. Discovery（发现）：启动时只加载 name 和 description（~100 tokens）
2. Activation（激活）：任务匹配时读取完整 SKILL.md（< 5000 tokens）
3. Execution（执行）：按需加载 scripts/references/assets 文件
"""
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.extension_api.registrar import OwnedPath

from .models import Skill, SkillCategory
from .loader import SkillLoader
from .file_watcher import SkillRuleWatcher

logger = logging.getLogger(__name__)


class SkillManager:
    """
    Skill 管理器 - 实现渐进式披露

    负责：
    - 加载和缓存所有 skills
    - 根据 agent 类型筛选适用的 skills
    - 生成注入到 prompt 的 skill 内容（支持不同披露级别）
    - 提供 CRUD 接口
    """

    def __init__(
        self,
        skills_dir: Path,
        config_manager: "SkillConfigManager | None" = None,
        rules_dir: Path | None = None,
        rule_manager: "RuleManager | None" = None,
        *,
        resource_roots: list[OwnedPath] | None = None,
        owner_enabled: Callable[[str], bool] | None = None,
    ):
        self.skills_dir = skills_dir
        self.loader = SkillLoader(
            skills_dir,
            resource_roots=resource_roots,
            owner_enabled=owner_enabled,
        )
        self.config_manager = config_manager
        self._skills: dict[str, Skill] = {}
        self._skills_lock = threading.Lock()
        self._load_all_skills()

        # 文件监控器
        self._file_watcher: SkillRuleWatcher | None = None
        self._rules_dir = rules_dir
        self._rule_manager = rule_manager

    def _load_all_skills(self):
        """加载所有 skills 到内存。

        在锁内完成新字典的构建和引用替换，防止文件监控线程
        与主线程并发读写 _skills 导致不一致。
        """
        skills = self.loader.load_all()
        new_skills = {skill.id: skill for skill in skills}
        logger.info(f"SkillManager 已加载 {len(new_skills)} 个 skills")

        # 同步配置文件
        if self.config_manager:
            skill_ids = list(new_skills.keys())
            self.config_manager.sync_with_directory(skill_ids)
            # 从配置恢复 enabled 状态（SKILL.md 不持久化 enabled）
            for skill_id, skill in new_skills.items():
                skill.enabled = self.config_manager.get_enabled(skill_id)

        with self._skills_lock:
            self._skills = new_skills

    def reload(self):
        """重新加载所有 skills（用于运行时刷新）"""
        # 先重新加载配置管理器（获取最新配置），再加载技能（依赖配置恢复 enabled 状态）
        if self.config_manager:
            self.config_manager.reload()

        self._load_all_skills()

        logger.info("Skills 已重新加载")

    def start_file_watcher(self, rules_dir: Path | None = None,
                           rule_manager: "RuleManager | None" = None,
                           debounce_seconds: float = 1.0) -> bool:
        """
        启动文件监控器

        Args:
            rules_dir: rules目录路径（可选）
            rule_manager: RuleManager实例（可选）
            debounce_seconds: 防抖时间（秒）

        Returns:
            True 如果启动成功
        """
        try:
            # 如果已有监控器，先停止
            if self._file_watcher:
                self._file_watcher.stop()

            # 使用传入的参数或初始化时的参数
            rules_dir = rules_dir or self._rules_dir
            rule_manager = rule_manager or self._rule_manager

            if not rules_dir:
                logger.warning("未提供rules目录，跳过rules监控")
                return False

            # 创建文件监控器
            self._file_watcher = SkillRuleWatcher(
                skills_dir=self.skills_dir,
                rules_dir=rules_dir,
                skills_callback=self._on_skills_changed,
                rules_callback=self._on_rules_changed if rule_manager else lambda: None,
                debounce_seconds=debounce_seconds
            )

            # 启动监控
            success = self._file_watcher.start()
            if success:
                logger.info("文件监控器已启动")
            return success

        except Exception as e:
            logger.error(f"启动文件监控器失败: {e}")
            return False

    def stop_file_watcher(self):
        """停止文件监控器"""
        if self._file_watcher:
            self._file_watcher.stop()
            self._file_watcher = None
            logger.info("文件监控器已停止")

    def _on_skills_changed(self):
        """skills目录变化时的回调"""
        logger.info("检测到skills目录变化，重新加载...")
        self.reload()

    def _on_rules_changed(self):
        """rules目录变化时的回调"""
        logger.info("检测到rules目录变化，重新加载...")
        if self._rule_manager:
            self._rule_manager.reload()

    def get_file_watcher_status(self) -> dict:
        """获取文件监控器状态"""
        if self._file_watcher:
            return self._file_watcher.get_status()
        return {"running": False}

    def get_skill(self, skill_id: str) -> Skill | None:
        """获取指定 skill"""
        return self._skills.get(skill_id)

    def list_all(self, enabled_only: bool = False) -> list[Skill]:
        """列出所有 skills"""
        skills = list(self._skills.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return sorted(skills, key=lambda s: (-s.priority, s.name))

    def list_by_category(self, category: SkillCategory, enabled_only: bool = False) -> list[Skill]:
        """按分类列出 skills"""
        skills = [s for s in self._skills.values() if s.category == category]
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return sorted(skills, key=lambda s: (-s.priority, s.name))

    def list_by_agent_type(
        self,
        agent_type: str,
        enabled_only: bool = True,
        auto_inject_only: bool = False,
        workflow_context: bool = False,
        visible_skill_group_ids: list[str] | None = None,
    ) -> list[Skill]:
        """
        获取适用于指定 agent 类型的 skills

        Args:
            agent_type: agent 类型（如 "coder", "researcher"）
            enabled_only: 是否只返回启用的 skills
            auto_inject_only: 是否只返回标记为自动注入的 skills
            workflow_context: 是否为工作流上下文（影响 workflow_only skills 可见性）
            visible_skill_group_ids: None 不限制；空列表不返回任何 skill；
                非空时只返回属组相交的 skill

        Returns:
            按优先级排序的 skill 列表
        """
        skills = [
            s for s in self._skills.values()
            if s.matches_agent_type(agent_type)
        ]
        if enabled_only:
            skills = [s for s in skills if s.enabled]

        # workflow_only 过滤：仅在 workflow_context=True 时可见
        if not workflow_context:
            skills = [s for s in skills if not s.workflow_only]

        if visible_skill_group_ids is not None:
            if not visible_skill_group_ids:
                skills = []
            else:
                visible = set(visible_skill_group_ids)
                skills = [
                    skill
                    for skill in skills
                    if visible
                    & set(
                        self.config_manager.get_skill_group_ids(skill.id)
                        if self.config_manager
                        else ["default"]
                    )
                ]

        if auto_inject_only and self.config_manager:
            skills = [s for s in skills if self.config_manager.should_auto_inject(s.id)]

        return sorted(skills, key=lambda s: (-s.priority, s.name))

    def build_discovery_section(
        self,
        agent_type: str,
        workflow_context: bool = False,
        visible_skill_group_ids: list[str] | None = None,
    ) -> str:
        """
        构建 Discovery 阶段的 skills 列表（仅 name + description）

        这是最轻量级的形式，用于让 agent 知道有哪些 skills 可用。
        预计每个 skill 约 50-100 tokens。

        Args:
            agent_type: agent 类型
            workflow_context: 是否为工作流上下文

        Returns:
            格式化的 markdown 文本
        """
        skills = self.list_by_agent_type(
            agent_type,
            enabled_only=True,
            workflow_context=workflow_context,
            visible_skill_group_ids=visible_skill_group_ids,
        )
        if not skills:
            return ""

        parts = []

        for skill in skills:
            parts.append(f"## {skill.name}")
            parts.append(f"**ID**: `{skill.id}`")
            parts.append(f"**描述**: {skill.description}\n")

        return "\n".join(parts)

    def build_auto_inject_section(self, skill_ids: list[str]) -> str:
        """
        构建自动注入的 skills 头部元数据（仅 YAML frontmatter）

        与 build_activation_section 不同，此方法只注入 skill 的头部元数据
        （name、description、category、priority、version、author、tags 等），
        不包含 skill 的完整 Markdown body 内容。

        Args:
            skill_ids: 要注入的 skill ID 列表

        Returns:
            格式化的 markdown 文本，无有效 skill 时返回空字符串
        """
        # 过滤出存在且已启用的 skill
        active_skills = []
        for skill_id in skill_ids:
            skill = self._skills.get(skill_id)
            if skill and skill.enabled:
                active_skills.append(skill)

        if not active_skills:
            return ""

        parts = []
        for skill in active_skills:
            parts.append(f"## {skill.name} (`{skill.id}`)\n")
            parts.append(f"**描述**: {skill.description}")
            parts.append(f"- **分类**: {skill.category.value}")
            parts.append(f"- **优先级**: {skill.priority}")
            if skill.version and skill.version != "1.0.0":
                parts.append(f"- **版本**: {skill.version}")
            if skill.author:
                parts.append(f"- **作者**: {skill.author}")
            if skill.tags:
                parts.append(f"- **标签**: {', '.join(skill.tags)}")
            if skill.agent_types:
                parts.append(f"- **适用 Agent**: {', '.join(skill.agent_types)}")
            parts.append("")

        return "\n".join(parts)

    def build_activation_section(self, skill_ids: list[str]) -> str:
        """
        构建 Activation 阶段的 skills 内容（完整 SKILL.md）

        当 agent 决定使用某个 skill 时，加载完整的指令内容。
        建议每个 skill < 5000 tokens（约 500 行）。

        Args:
            skill_ids: 要激活的 skill ID 列表

        Returns:
            格式化的 markdown 文本，无有效 skill 时返回空字符串
        """
        # 过滤出存在且已启用的 skill
        active_skills = []
        for skill_id in skill_ids:
            skill = self._skills.get(skill_id)
            if skill and skill.enabled:
                active_skills.append(skill)

        if not active_skills:
            return ""

        parts = ["# Activated Skills\n"]
        for skill in active_skills:
            parts.append(f"## {skill.name} (`{skill.id}`)\n")
            parts.append(skill.content)
            parts.append("\n---\n")

        return "\n".join(parts)

    def build_skills_section(
        self,
        agent_type: str,
        mode: str = "discovery",
        workflow_context: bool = False,
        visible_skill_group_ids: list[str] | None = None,
    ) -> str:
        """
        为指定 agent 类型构建 skills section 内容

        Args:
            agent_type: agent 类型
            mode: 披露模式
                - "discovery": 仅列出可用 skills（轻量级）
                - "full": 包含所有适用 skills 的完整内容（重量级）
                - "auto_inject": 仅包含标记为自动注入的 skills（完整内容）
            workflow_context: 是否为工作流上下文
            visible_skill_group_ids: Agent 可见技能组；空列表表示不注入

        Returns:
            格式化的 markdown 文本
        """
        if mode == "discovery":
            return self.build_discovery_section(
                agent_type,
                workflow_context=workflow_context,
                visible_skill_group_ids=visible_skill_group_ids,
            )
        elif mode == "full":
            skills = self.list_by_agent_type(
                agent_type,
                enabled_only=True,
                workflow_context=workflow_context,
                visible_skill_group_ids=visible_skill_group_ids,
            )
            skill_ids = [s.id for s in skills]
            return self.build_activation_section(skill_ids)
        elif mode == "auto_inject":
            # 只注入标记为自动注入的 skills（仅头部元数据，不含全文）
            skills = self.list_by_agent_type(
                agent_type,
                enabled_only=True,
                auto_inject_only=True,
                workflow_context=workflow_context,
                visible_skill_group_ids=visible_skill_group_ids,
            )
            skill_ids = [s.id for s in skills]
            return self.build_auto_inject_section(skill_ids)
        else:
            logger.warning(f"未知的 mode: {mode}，使用 discovery")
            return self.build_discovery_section(
                agent_type,
                workflow_context=workflow_context,
                visible_skill_group_ids=visible_skill_group_ids,
            )

    def get_skill_file(self, skill_id: str, file_path: str) -> str | None:
        """
        获取 skill 目录中的文件内容（Execution 阶段）

        用于按需加载 scripts/references/assets 中的文件。

        Args:
            skill_id: skill ID
            file_path: 相对于 skill 目录的文件路径（如 "scripts/extract.py"）

        Returns:
            文件内容，如果文件不存在返回 None
        """
        skill = self._skills.get(skill_id)
        if not skill:
            logger.warning(f"Skill 不存在: {skill_id}")
            return None

        raw_skill_dir = skill.metadata.get("skill_dir")
        if not raw_skill_dir:
            return None
        skill_dir = Path(raw_skill_dir)
        if not skill_dir.exists():
            logger.warning(f"Skill 目录不存在: {skill_dir}")
            return None

        file_full_path = skill_dir / file_path

        # 安全检查：确保文件在 skill 目录内
        try:
            file_full_path = file_full_path.resolve()
            skill_dir = skill_dir.resolve()
            if not file_full_path.is_relative_to(skill_dir):
                logger.error(f"安全错误：文件路径超出 skill 目录: {file_path}")
                return None
        except Exception as e:
            logger.error(f"路径解析错误: {e}")
            return None

        if not file_full_path.exists():
            logger.warning(f"文件不存在: {file_full_path}")
            return None

        try:
            return file_full_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {file_full_path}: {e}")
            return None

    def get_skills_summary(self, agent_type: str | None = None) -> list[dict]:
        """
        获取 skills 摘要信息（用于 API 和前端展示）

        Args:
            agent_type: 可选，筛选适用于指定 agent 类型的 skills

        Returns:
            skill 摘要列表
        """
        if agent_type:
            skills = self.list_by_agent_type(agent_type, enabled_only=False)
        else:
            skills = self.list_all(enabled_only=False)

        result = []
        for s in skills:
            summary = {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "category": s.category.value,
                "agent_types": s.agent_types,
                "workflow_only": s.workflow_only,
                "priority": s.priority,
                "tags": s.tags,
                "enabled": s.enabled,
                "version": s.version,
                "author": s.author,
                "content_length": len(s.content),
                "has_scripts": s.metadata.get("has_scripts", False),
                "has_references": s.metadata.get("has_references", False),
                "has_assets": s.metadata.get("has_assets", False),
                "resource_owner": s.metadata.get("resource_owner", "user"),
                "resource_read_only": s.metadata.get(
                    "resource_read_only",
                    False,
                ),
            }

            # 添加配置信息
            if self.config_manager:
                summary["auto_inject"] = self.config_manager.should_auto_inject(s.id)
                summary["group_ids"] = self.config_manager.get_skill_group_ids(s.id)
                # config 中的 workflow_only 覆盖 frontmatter 默认值
                wf_only = self.config_manager.get_workflow_only(s.id)
                if wf_only is not None:
                    summary["workflow_only"] = wf_only
            else:
                summary["auto_inject"] = False
                summary["group_ids"] = []

            result.append(summary)

        return result

    @staticmethod
    def from_raw_content(raw_content: str) -> Skill | None:
        """
        从 LLM 产出的完整 SKILL.md 文本（含 YAML frontmatter）解析为 Skill 对象。

        这是 LLM 创建技能的统一入口：LLM 产出完整的 SKILL.md 文本，
        此方法解析并返回 Skill 对象，后续由 create_skill 保存到磁盘。

        Skill 对象构建逻辑委托给 SkillLoader._build_skill_from_parsed()，
        保证与 _load_skill_dir 产出结构一致（含 skill_dir / has_scripts 等元数据字段，
        值为空/False —— 这些字段由 save_skill 在保存时填充）。

        Args:
            raw_content: LLM 产出的完整 SKILL.md 内容（frontmatter + body）

        Returns:
            Skill 对象，解析失败返回 None
        """
        try:
            frontmatter, body = SkillLoader._parse_skill_md(raw_content)
        except ValueError as e:
            logger.error(f"from_raw_content: {e}")
            return None

        try:
            return SkillLoader._build_skill_from_parsed(frontmatter, body)
        except Exception as e:
            logger.error(f"from_raw_content 解析失败: {e}")
            return None

    def create_skill(self, skill_data: dict) -> Skill:
        """创建新 skill"""
        skill = Skill.from_dict(skill_data)
        self.loader.save_skill(skill)
        self._skills[skill.id] = skill
        logger.info(f"已创建 skill: {skill.id}")
        return skill

    # 可更新字段白名单，防止意外修改 id、created_at、metadata 等不可变字段
    _ALLOWED_UPDATE_FIELDS = frozenset({
        "name", "description", "summary", "content", "version", "author",
        "category", "enabled", "priority", "workflow_only", "auto_inject",
        "agent_types", "tags", "group_ids",
    })

    def update_skill(self, skill_id: str, updates: dict) -> Skill | None:
        """更新 skill"""
        skill = self._skills.get(skill_id)
        if not skill:
            logger.warning(f"Skill 不存在: {skill_id}")
            return None
        if skill.metadata.get("resource_read_only"):
            raise PermissionError(
                f"Plugin Skill 是只读资源，不能直接修改: {skill_id}"
            )

        # 白名单过滤：仅更新允许的字段
        for key, value in updates.items():
            if key in self._ALLOWED_UPDATE_FIELDS:
                setattr(skill, key, value)
            else:
                logger.warning(f"忽略不允许更新的字段: {key}")

        # 更新时间戳
        skill.updated_at = datetime.now(timezone.utc).isoformat()

        # 保存到文件
        self.loader.save_skill(skill)
        logger.info(f"已更新 skill: {skill_id}")
        return skill

    def delete_skill(self, skill_id: str) -> bool:
        """删除 skill"""
        skill = self._skills.get(skill_id)
        if not skill:
            logger.warning(f"Skill 不存在: {skill_id}")
            return False
        if skill.metadata.get("resource_read_only"):
            raise PermissionError(
                f"Plugin Skill 是只读资源，不能直接删除: {skill_id}"
            )

        success = self.loader.delete_skill(skill_id)
        if success:
            del self._skills[skill_id]
            logger.info(f"已删除 skill: {skill_id}")
        return success

    def toggle_skill(self, skill_id: str, enabled: bool) -> bool:
        """启用/禁用 skill。

        enabled 状态仅存储在 config_manager 中（SKILL.md 不持久化该字段），
        因此无需调用 save_skill 重写 SKILL.md。
        """
        skill = self._skills.get(skill_id)
        if not skill:
            return False

        skill.enabled = enabled
        if self.config_manager:
            self.config_manager.set_enabled(skill_id, enabled)
        logger.info(f"Skill {skill_id} 已{'启用' if enabled else '禁用'}")
        return True

    def get_stats(self) -> dict:
        """获取统计信息"""
        all_skills = list(self._skills.values())
        enabled_count = sum(1 for s in all_skills if s.enabled)

        by_category = {}
        for category in SkillCategory:
            count = sum(1 for s in all_skills if s.category == category)
            if count > 0:
                by_category[category.value] = count

        return {
            "total": len(all_skills),
            "enabled": enabled_count,
            "disabled": len(all_skills) - enabled_count,
            "by_category": by_category,
        }

    def initialize_if_empty(self):
        """如果没有任何 skill，创建示例 skills"""
        if not self._skills:
            logger.info("检测到空 skills 目录，创建示例 skills...")
            self.loader.create_example_skills()
            self._load_all_skills()
