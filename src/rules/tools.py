"""
Rules 工具 — 为 Agent 提供访问和管理 rules 的工具

参照 skills/tools.py 架构：
- get_rules: 查看规则（列表 + 详情）
- rule_manage: CRUD 规则（create/edit/patch/delete）

复用 skills/validation.py 的校验函数，仅 frontmatter 校验需要适配 RULE.md 格式。
"""
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.rules.manager import RuleManager

logger = logging.getLogger(__name__)


def create_get_rules_tool(rule_manager: "RuleManager"):
    """
    创建 get_rules 工具

    支持两种模式：
    1. 无参数：列出所有可用规则（名称 + 描述 + 严重程度）
    2. rule_id：返回规则完整内容
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class GetRulesInput(BaseModel):
        rule_id: str | None = Field(
            default=None,
            description="规则 ID。不提供则列出所有可用规则；提供则获取该规则的完整内容"
        )

    def get_rules(rule_id: str | None = None) -> str:
        """
        访问规则系统。

        用法：
        1. get_rules() - 列出所有可用规则
        2. get_rules(rule_id="no-destructive-operations") - 获取规则完整内容
        """
        try:
            # 模式 1: 列出所有可用规则
            if rule_id is None:
                rules = rule_manager.list_all()
                if not rules:
                    return "当前没有可用的规则。"

                parts = ["# Available Rules\n"]
                parts.append(f"当前有 {len(rules)} 条规则：\n")

                for rule in rules:
                    parts.append(f"## {rule.name} ({rule.id})")
                    parts.append(f"**描述**: {rule.description}")

                    # 从 config 获取 agent_types 和 workflow_only
                    if rule_manager.config_manager:
                        agent_types = rule_manager.config_manager.get_agent_types(rule.id)
                        if agent_types:
                            parts.append(f"**适用**: {', '.join(agent_types)}")
                        else:
                            parts.append(f"**适用**: 所有 agent 类型")

                        wf_only = rule_manager.config_manager.get_workflow_only(rule.id)
                        if wf_only:
                            parts.append("**作用域**: 仅工作流")

                        # 展示所属分组
                        group_ids = rule_manager.config_manager.get_rule_group_ids(rule.id)
                        if group_ids:
                            group_labels = []
                            for gid in group_ids:
                                g = rule_manager.config_manager.get_group(gid)
                                group_labels.append(g["name"] if g else gid)
                            parts.append(f"**分组**: {', '.join(group_labels)}")

                    parts.append("")

                parts.append("---\n")
                parts.append("💡 **提示**：")
                parts.append("- 使用 get_rules(rule_id=\"rule-id\") 获取完整规则内容")
                parts.append("- 这些规则是强制性的，你必须严格遵守")

                return "\n".join(parts)

            # 模式 2: 获取规则详情
            rule = rule_manager.get_rule(rule_id)
            if not rule:
                return f"错误：未找到规则 '{rule_id}'。使用 get_rules() 查看所有可用规则。"

            parts = [f'<rule_content id="{rule.id}">']
            parts.append(f"# {rule.name}")
            parts.append(f"**描述**: {rule.description}")
            parts.append("")
            parts.append(rule.content)
            parts.append("</rule_content>")

            return "\n".join(parts)

        except Exception as e:
            logger.error(f"get_rules 工具执行失败: {e}", exc_info=True)
            return f"错误：{str(e)}"

    return StructuredTool(
        name="get_rules",
        description=(
            "查看规则系统获取强制性行为准则。"
            "不带参数时列出所有可用规则及其严重程度；"
            "提供 rule_id 获取完整规则内容。"
        ),
        func=get_rules,
        args_schema=GetRulesInput,
    )


def create_rule_manage_tool(rule_manager: "RuleManager"):
    """
    创建 rule_manage 工具 — LLM 自主创建、编辑、修补、删除规则。

    支持 4 个 action：
    - create: 创建新规则（完整 RULE.md 内容）
    - edit: 完整重写已有规则
    - patch: 精确字符串查找替换
    - delete: 删除规则

    每个 action 在执行前都会经过安全校验层。
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class RuleManageInput(BaseModel):
        action: str = Field(
            description=(
                "操作类型。"
                "create: 创建新规则（需要 content）; "
                "edit: 完整重写已有规则（需要 content）; "
                "patch: 精确查找替换（需要 old_string 和 new_string）; "
                "delete: 删除规则"
            )
        )
        name: str | None = Field(
            default=None,
            description="规则 ID（create/edit/patch/delete 都需要）"
        )
        content: str | None = Field(
            default=None,
            description="完整的 RULE.md 内容（YAML frontmatter + Markdown body）。create 和 edit 必需。"
        )
        old_string: str | None = Field(
            default=None,
            description="要查找的精确文本（patch 必需）。包含足够的上下文以确保唯一性。"
        )
        new_string: str | None = Field(
            default=None,
            description="替换文本（patch 必需）。使用空字符串删除匹配的文本。"
        )

    def rule_manage(
        action: str,
        name: str | None = None,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
    ) -> str:
        """
        管理规则：创建、编辑、修补、删除规则。

        规则是你的强制性行为准则——当发现某个行为模式或约束值得固化为规则时，
        主动使用此工具创建它。规则与技能不同：规则是"你必须"，技能是"你可以参考"。
        """
        from .manager import RuleManager
        from src.skills.validation import (
            validate_skill_name,
            validate_content_size,
            security_scan,
        )
        from pathlib import Path

        # ── 参数校验 ──
        if not action or action not in ("create", "edit", "patch", "delete"):
            return "错误：无效的 action。可用: create, edit, patch, delete"

        if not name:
            return "错误：name 参数是必需的。"

        try:
            # ── action: create ──
            if action == "create":
                if not content:
                    return "错误：创建规则需要 content（完整的 RULE.md 文本，含 YAML frontmatter）。"

                # 校验名称
                ok, err = validate_skill_name(name)
                if not ok:
                    return f"错误：规则名称校验失败 - {err}"

                # 校验 frontmatter（适配 RULE.md 格式）
                ok, err = _validate_rule_frontmatter(content)
                if not ok:
                    return f"错误：{err}"

                # 校验 name 与 frontmatter 中的 name 一致
                match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
                if match:
                    try:
                        fm = yaml.safe_load(match.group(1))
                        fm_name = fm.get("name", "") if isinstance(fm, dict) else ""
                        if fm_name and fm_name != name:
                            return f"错误：frontmatter 中的 name '{fm_name}' 与参数 name '{name}' 不一致。"
                    except yaml.YAMLError as e:
                        return f"错误：YAML frontmatter 解析失败 - {e}"

                # 校验内容大小
                ok, err = validate_content_size(content)
                if not ok:
                    return f"错误：{err}"

                # 安全扫描
                ok, err = security_scan(content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                # 检查名称冲突
                existing = rule_manager.get_rule(name)
                if existing:
                    return f"错误：规则 '{name}' 已存在。请使用 edit 修改或 delete 删除后重建。"

                # 解析并保存
                rule = RuleManager.from_raw_content(content)
                if not rule:
                    return "错误：无法解析 content 为有效的规则。请检查 YAML frontmatter 格式。"

                try:
                    rule_manager.create_rule(rule.to_dict())
                except Exception as e:
                    return f"错误：创建规则失败 - {str(e)}"

                # 同步配置文件
                if rule_manager.config_manager:
                    rule_manager.config_manager.sync_with_directory([name])

                rule_path = rule_manager.rules_dir / name
                return (
                    f"✅ 规则 '{name}' 创建成功。\n"
                    f"路径: {rule_path}/"
                )

            # ── action: edit ──
            elif action == "edit":
                if not content:
                    return "错误：编辑规则需要 content（完整的更新后 RULE.md 文本）。"

                existing = rule_manager.get_rule(name)
                if not existing:
                    return f"错误：规则 '{name}' 不存在。使用 get_rules() 查看可用规则列表。"

                # 校验
                ok, err = _validate_rule_frontmatter(content)
                if not ok:
                    return f"错误：{err}"
                ok, err = validate_content_size(content)
                if not ok:
                    return f"错误：{err}"
                ok, err = security_scan(content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                # 解析并更新
                rule = RuleManager.from_raw_content(content)
                if not rule:
                    return "错误：无法解析 content 为有效的规则。"

                try:
                    rule_manager.update_rule(name, rule.to_dict())
                except Exception as e:
                    return f"错误：更新规则失败 - {str(e)}"

                return f"✅ 规则 '{name}' 已更新。"

            # ── action: patch ──
            elif action == "patch":
                if not old_string:
                    return "错误：patch 需要 old_string（要查找的精确文本）。"
                if new_string is None:
                    return "错误：patch 需要 new_string（替换文本，空字符串可删除匹配内容）。"

                existing = rule_manager.get_rule(name)
                if not existing:
                    return f"错误：规则 '{name}' 不存在。"
                if existing.metadata.get("resource_read_only"):
                    return f"错误：Plugin 规则 '{name}' 是只读资源，不能直接修补。"

                # 确定目标文件
                rule_dir = Path(existing.metadata.get("rule_dir", ""))
                if not rule_dir.exists():
                    return f"错误：规则目录不存在: {rule_dir}"

                target_file = rule_dir / "RULE.md"
                if not target_file.exists():
                    return f"错误：RULE.md 不存在: {target_file}"

                current_content = target_file.read_text(encoding="utf-8")

                # 精确查找替换
                count = current_content.count(old_string)
                if count == 0:
                    stripped = old_string.strip()
                    count_stripped = current_content.count(stripped)
                    if count_stripped > 0:
                        old_string = stripped
                        count = count_stripped
                    else:
                        preview = current_content[:400] + ("..." if len(current_content) > 400 else "")
                        return (
                            f"错误：在 RULE.md 中未找到 old_string。\n"
                            f"请检查 old_string 是否与文件内容精确匹配。\n"
                            f"文件预览（前 400 字符）:\n{preview}"
                        )
                if count > 1:
                    return (
                        f"错误：old_string 匹配到 {count} 处。请包含更多上下文以确保唯一性。"
                    )

                new_content = current_content.replace(old_string, new_string)

                # 重新校验
                ok, err = _validate_rule_frontmatter(new_content)
                if not ok:
                    return f"错误：patch 会破坏 RULE.md 结构 - {err}"
                ok, err = validate_content_size(new_content)
                if not ok:
                    return f"错误：{err}"
                ok, err = security_scan(new_content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                # 原子写入（通过 loader.save_rule 使用 tmp+os.replace），
                # 并通过 loader.load_rule 重新加载以同步内存状态，
                # 避免直接读写 RULE.md 文件和访问私有属性 _rules。
                from src.rules.loader import RuleLoader as _RL
                _fm, body = _RL._parse_frontmatter(new_content)
                rule_id = rule_dir.name  # 目录名是规则的规范 ID
                from src.rules.models import Rule as _Rule
                tmp_rule = _Rule(
                    id=rule_id,
                    name=existing.name,
                    description=existing.description,
                    summary=getattr(existing, "summary", ""),
                    content=body,
                    version=existing.version,
                    author=existing.author,
                    metadata=dict(existing.metadata),
                )
                if not rule_manager.loader.save_rule(tmp_rule):
                    return f"错误：保存规则 '{name}' 失败（原子写入未成功）。"

                # 从磁盘重新加载以同步内存状态（替代直接 _rules[name] 赋值）
                reloaded = rule_manager.loader.load_rule(rule_id)
                if reloaded:
                    rule_manager._rules[rule_id] = reloaded

                return f"✅ 已修补规则 '{name}' 的 RULE.md（1 处替换）。"

            # ── action: delete ──
            elif action == "delete":
                existing = rule_manager.get_rule(name)
                if not existing:
                    return f"错误：规则 '{name}' 不存在。"

                success = rule_manager.delete_rule(name)
                if success:
                    return f"✅ 规则 '{name}' 已删除。"
                else:
                    return f"错误：删除规则 '{name}' 失败。"

        except Exception as e:
            logger.error(f"rule_manage 工具执行失败 (action={action}, name={name}): {e}", exc_info=True)
            return f"错误：操作失败 - {str(e)}"

    return StructuredTool(
        name="rule_manage",
        description=(
            "管理规则库：创建、编辑、修补、删除强制性行为准则。\n"
            "规则是你必须严格遵守的约束（与技能不同：规则是\"你必须\"，技能是\"你可以参考\"）。\n\n"
            "何时创建：发现某个行为模式或约束值得固化为规则时、用户要求设定规则时。\n"
            "何时更新：规则描述有误、发现遗漏的约束或边界条件。\n"
            "创建/删除前与用户确认。\n\n"
            "可用 actions：\n"
            "- create: 创建新规则（需要 name + content）\n"
            "- edit: 完整重写已有规则（需要 name + content）\n"
            "- patch: 精确查找替换（需要 name + old_string + new_string）\n"
            "- delete: 删除规则（需要 name）"
        ),
        func=rule_manage,
        args_schema=RuleManageInput,
    )


# =============================================================================
# RULE.md 专用 frontmatter 校验（内联，避免创建独立模块）
# =============================================================================

def _validate_rule_frontmatter(content: str) -> tuple[bool, str]:
    """校验 RULE.md 的 YAML frontmatter 完整性。

    适配 RULE.md 格式：
    - 必须有 name 和 description
    - metadata 中 severity 有效
    - body 不能为空

    Returns:
        (is_valid, error_message)
    """
    if not content or not content.strip():
        return False, "RULE.md 内容不能为空。"

    if not content.startswith("---"):
        return False, "RULE.md 必须以 YAML frontmatter (---) 开头。请参考现有 rules 的格式。"

    # 查找闭合的 ---
    end_match = re.search(r'\n---\s*\n', content[3:])
    if not end_match:
        return False, "RULE.md 的 YAML frontmatter 缺少闭合的 '---' 行。"

    yaml_content = content[3:end_match.start() + 3]

    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        return False, f"YAML frontmatter 解析错误: {e}"

    if not isinstance(parsed, dict):
        return False, "Frontmatter 必须是 YAML 字典格式。"

    if "name" not in parsed:
        return False, "Frontmatter 必须包含 'name' 字段。"

    if "description" not in parsed:
        return False, "Frontmatter 必须包含 'description' 字段。"

    # 检查 body 不为空
    body_start = end_match.end() + 3
    body = content[body_start:].strip()
    if not body:
        return False, "RULE.md 的 frontmatter 之后必须有内容（规则说明等）。"

    return True, ""


def create_rule_group_manage_tool(rule_manager: "RuleManager"):
    """
    创建 rule_group_manage 工具 — LLM 自主管理规则分组和属组关系。

    支持 6 个 action：
    - list_groups: 列出所有分组及其包含的规则数量
    - create_group: 创建新分组
    - update_group: 更新分组名称或描述
    - delete_group: 删除分组（自动清理属组引用）
    - add_to_group: 将规则加入指定分组
    - remove_from_group: 将规则移出指定分组
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class RuleGroupManageInput(BaseModel):
        action: str = Field(
            description=(
                "操作类型。"
                "list_groups: 列出所有规则分组; "
                "create_group: 创建新分组（需要 group_id + name）; "
                "update_group: 更新分组信息（需要 group_id）; "
                "delete_group: 删除分组（需要 group_id）; "
                "add_to_group: 将规则加入分组（需要 rule_id + group_id）; "
                "remove_from_group: 将规则移出分组（需要 rule_id + group_id）"
            )
        )
        group_id: str | None = Field(
            default=None,
            description="分组ID（create_group/update_group/delete_group/add_to_group/remove_from_group 需要）"
        )
        name: str | None = Field(
            default=None,
            description="分组显示名称（create_group 需要，update_group 可选）"
        )
        description: str | None = Field(
            default=None,
            description="分组描述（create_group/update_group 可选）"
        )
        rule_id: str | None = Field(
            default=None,
            description="规则ID（add_to_group/remove_from_group 需要）"
        )

    def rule_group_manage(
        action: str,
        group_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        rule_id: str | None = None,
    ) -> str:
        """
        管理规则分组及规则与分组的属组关系。

        分组用于将规则按主题或用途分类，Agent 可据此筛选可见的规则范围。
        使用 add_to_group / remove_from_group 为规则分配分类。
        """
        from src.skills.validation import validate_skill_name

        valid_actions = ("list_groups", "create_group", "update_group", "delete_group", "add_to_group", "remove_from_group")
        if action not in valid_actions:
            return f"错误：无效的 action '{action}'。可用: {', '.join(valid_actions)}"

        config = rule_manager.config_manager
        if not config:
            return "错误：规则配置管理器不可用。"

        try:
            # ── action: list_groups ──
            if action == "list_groups":
                groups = config.get_groups()
                if not groups:
                    return "当前没有规则分组。"

                parts = ["# 规则分组列表\n"]
                for g in groups:
                    gid = g.get("id", "")
                    gname = g.get("name", gid)
                    gdesc = g.get("description", "")
                    # 统计组内规则数
                    rule_ids = config.get_rules_in_group(gid) if hasattr(config, "get_rules_in_group") else []
                    count = len(rule_ids)
                    parts.append(f"## {gname} (`{gid}`)")
                    if gdesc:
                        parts.append(f"  描述: {gdesc}")
                    parts.append(f"  包含规则数: {count}")
                    if rule_ids:
                        parts.append(f"  规则: {', '.join(rule_ids)}")
                    parts.append("")
                return "\n".join(parts)

            # ── 以下 action 都需要 group_id ──
            if not group_id:
                return f"错误：{action} 需要 group_id 参数。"

            # ── action: create_group ──
            if action == "create_group":
                if not name:
                    return "错误：创建分组需要 name（分组显示名称）。"

                ok, err = validate_skill_name(group_id)
                if not ok:
                    return f"错误：分组ID格式无效 - {err}"

                existing = config.get_group(group_id)
                if existing:
                    return f"错误：分组 '{group_id}' 已存在。请使用 update_group 修改或 delete_group 删除。"

                result = config.create_group({
                    "id": group_id,
                    "name": name,
                    "description": description or "",
                })
                if result:
                    return f"✅ 规则分组 '{name}' (`{group_id}`) 已创建。"
                return f"错误：创建分组 '{group_id}' 失败。"

            # ── action: update_group ──
            elif action == "update_group":
                existing = config.get_group(group_id)
                if not existing:
                    return f"错误：分组 '{group_id}' 不存在。使用 list_groups 查看所有分组。"

                if not name and not description:
                    return "错误：update_group 需要至少提供 name 或 description 之一。"

                updates = {}
                if name is not None:
                    updates["name"] = name
                if description is not None:
                    updates["description"] = description

                ok = config.update_group(group_id, updates)
                if ok:
                    return f"✅ 分组 '{group_id}' 已更新。"
                return f"错误：更新分组 '{group_id}' 失败。"

            # ── action: delete_group ──
            elif action == "delete_group":
                existing = config.get_group(group_id)
                if not existing:
                    return f"错误：分组 '{group_id}' 不存在。"

                gname = existing.get("name", group_id)
                rule_ids = config.get_rules_in_group(group_id) if hasattr(config, "get_rules_in_group") else []

                ok = config.delete_group(group_id)
                if ok:
                    msg = f"✅ 分组 '{gname}' (`{group_id}`) 已删除。"
                    if rule_ids:
                        msg += f"\n已从 {len(rule_ids)} 个规则中移除此分组引用。"
                    return msg
                return f"错误：删除分组 '{group_id}' 失败。"

            # ── action: add_to_group ──
            elif action == "add_to_group":
                if not rule_id:
                    return "错误：add_to_group 需要 rule_id 参数。"

                r = rule_manager.get_rule(rule_id)
                if not r:
                    return f"错误：规则 '{rule_id}' 不存在。使用 get_rules() 查看可用规则。"

                group = config.get_group(group_id)
                if not group:
                    return f"错误：分组 '{group_id}' 不存在。使用 rule_group_manage(action='list_groups') 查看可用分组。"

                current_groups = config.get_rule_group_ids(rule_id)
                if group_id in current_groups:
                    return f"规则 '{rule_id}' 已在分组 '{group_id}' 中，无需重复添加。"

                # 先拷贝再修改，避免 get_rule_group_ids 返回的内部引用被原地修改
                new_groups = list(current_groups)
                new_groups.append(group_id)
                ok = config.set_rule_group_ids(rule_id, new_groups)
                if ok:
                    gname = group.get("name", group_id)
                    return f"✅ 已将规则 '{rule_id}' 加入分组 '{gname}' (`{group_id}`)。"
                return f"错误：添加规则 '{rule_id}' 到分组 '{group_id}' 失败。"

            # ── action: remove_from_group ──
            elif action == "remove_from_group":
                if not rule_id:
                    return "错误：remove_from_group 需要 rule_id 参数。"

                r = rule_manager.get_rule(rule_id)
                if not r:
                    return f"错误：规则 '{rule_id}' 不存在。"

                group = config.get_group(group_id)
                if not group:
                    return f"错误：分组 '{group_id}' 不存在。"

                current_groups = config.get_rule_group_ids(rule_id)
                if group_id not in current_groups:
                    return f"规则 '{rule_id}' 不在分组 '{group_id}' 中。"

                current_groups = [g for g in current_groups if g != group_id]
                ok = config.set_rule_group_ids(rule_id, current_groups)
                if ok:
                    gname = group.get("name", group_id)
                    return f"✅ 已将规则 '{rule_id}' 移出分组 '{gname}' (`{group_id}`)。"
                return f"错误：移除规则 '{rule_id}' 从分组 '{group_id}' 失败。"

        except Exception as e:
            logger.error(f"rule_group_manage 工具执行失败 (action={action}, group_id={group_id}, rule_id={rule_id}): {e}", exc_info=True)
            return f"错误：操作失败 - {str(e)}"

    return StructuredTool(
        name="rule_group_manage",
        description=(
            "管理规则分组及规则与分组的属组关系。\n"
            "分组用于将规则按主题或用途分类，Agent 可据此筛选可见的规则范围。\n\n"
            "可用 actions：\n"
            "- list_groups: 列出所有规则分组及其包含的规则\n"
            "- create_group: 创建新分组（需要 group_id + name，可选 description）\n"
            "- update_group: 更新分组名称或描述（需要 group_id，至少提供 name 或 description 之一）\n"
            "- delete_group: 删除分组（需要 group_id，自动清理属组引用）\n"
            "- add_to_group: 将规则加入分组（需要 rule_id + group_id）\n"
            "- remove_from_group: 将规则移出分组（需要 rule_id + group_id）"
        ),
        func=rule_group_manage,
        args_schema=RuleGroupManageInput,
    )
