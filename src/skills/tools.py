"""
Skills 工具 - 为 Agent 提供访问 skills 的工具
"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import SkillManager

logger = logging.getLogger(__name__)


def create_get_skills_tool(skill_manager: "SkillManager"):
    """
    创建 get_skills 工具

    这个工具允许 agent 访问 skills 系统：
    1. 无参数：列出所有可用 skills
    2. skill_id：获取 skill 的完整内容和资源列表
    3. skill_id + resource_path：获取捆绑资源的内容
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class GetSkillsInput(BaseModel):
        skill_id: str | None = Field(
            default=None,
            description="Skill ID。不提供则列出所有可用 skills；提供则获取该 skill 的完整内容"
        )
        resource_path: str | None = Field(
            default=None,
            description="资源文件路径（相对于 skill 目录），如 'scripts/extract.py'。需要同时提供 skill_id"
        )

    def get_skills(skill_id: str | None = None, resource_path: str | None = None) -> str:
        """
        访问 skills 系统。

        用法：
        1. get_skills() - 列出所有可用 skills
        2. get_skills(skill_id="python-best-practices") - 获取 skill 完整内容
        3. get_skills(skill_id="my-skill", resource_path="scripts/process.py") - 获取资源文件

        Args:
            skill_id: Skill ID（可选）
            resource_path: 资源文件路径（可选，需要 skill_id）

        Returns:
            Skills 信息或资源内容
        """
        try:
            # 模式 1: 列出所有可用 skills
            if skill_id is None:
                skills = skill_manager.list_all(enabled_only=True)
                if not skills:
                    return "当前没有可用的 skills。"

                parts = ["# Available Skills\n"]
                parts.append(f"你有 {len(skills)} 个可用的 skills：\n")

                for skill in skills:
                    parts.append(f"## {skill.name}")
                    parts.append(f"**ID**: `{skill.id}`")
                    parts.append(f"**分类**: {skill.category.value}")
                    if skill.agent_types:
                        parts.append(f"**适用**: {', '.join(skill.agent_types)}")
                    else:
                        parts.append(f"**适用**: 所有 agent 类型")
                    parts.append(f"**描述**: {skill.description}")
                    parts.append(f"**优先级**: {skill.priority}")
                    # 展示所属分组
                    if skill_manager.config_manager:
                        group_ids = skill_manager.config_manager.get_skill_group_ids(skill.id)
                        if group_ids:
                            group_labels = []
                            for gid in group_ids:
                                g = skill_manager.config_manager.get_group(gid)
                                group_labels.append(g["name"] if g else gid)
                            parts.append(f"**分组**: {', '.join(group_labels)}")
                    parts.append("")

                parts.append("---\n")
                parts.append("💡 **提示**：")
                parts.append("- 使用 get_skills(skill_id=\"skill-id\") 获取完整指令")
                parts.append("- 每个 skill 包含详细的步骤、示例和最佳实践")
                parts.append("- 根据任务需要选择合适的 skills 参考")

                return "\n".join(parts)

            # 模式 2 & 3: 获取 skill 详情或资源
            skill = skill_manager.get_skill(skill_id)
            if not skill:
                return f"错误：未找到 skill '{skill_id}'。使用 get_skills() 查看所有可用 skills。"

            # 模式 3: 获取资源文件
            if resource_path:
                content = skill_manager.get_skill_file(skill_id, resource_path)
                if content is None:
                    return f"错误：未找到资源文件 '{resource_path}'。"
                return content

            # 模式 2: 获取 skill 完整内容
            skill_dir = Path(skill.metadata.get("skill_dir", ""))

            # 收集资源文件列表
            resources = []
            if skill_dir.exists():
                for subdir in ["scripts", "references", "assets"]:
                    subdir_path = skill_dir / subdir
                    if subdir_path.exists() and subdir_path.is_dir():
                        for file in subdir_path.rglob("*"):
                            if file.is_file():
                                rel_path = file.relative_to(skill_dir)
                                resources.append(str(rel_path))

            # 构建结构化返回
            parts = [f'<skill_content name="{skill.id}">']
            parts.append(skill.content)
            parts.append("")
            parts.append(f"Skill directory: {skill_dir}")
            parts.append("Relative paths in this skill are relative to the skill directory.")

            if resources:
                parts.append("")
                parts.append("<skill_resources>")
                for res in resources:
                    parts.append(f"  <file>{res}</file>")
                parts.append("</skill_resources>")

            parts.append("</skill_content>")

            return "\n".join(parts)

        except Exception as e:
            logger.error(f"get_skills 工具执行失败: {e}", exc_info=True)
            return f"错误：{str(e)}"

    return StructuredTool(
        name="get_skills",
        description=(
            "访问 skills 系统获取知识和最佳实践。"
            "不带参数时列出所有可用 skills；"
            "提供 skill_id 获取完整指令；"
            "提供 skill_id 和 resource_path 获取捆绑资源。"
        ),
        func=get_skills,
        args_schema=GetSkillsInput,
    )


def create_skill_manage_tool(skill_manager: "SkillManager"):
    """
    创建 skill_manage 工具 — LLM 自主创建、编辑、修补、删除技能。

    支持 6 个 action：
    - create: 创建新技能（完整 SKILL.md 内容）
    - edit: 完整重写已有技能
    - patch: 精确字符串查找替换
    - delete: 删除技能
    - write_file: 写入捆绑资源文件（scripts/references/assets）
    - remove_file: 删除捆绑资源文件

    每个 action 在执行前都会经过安全校验层。
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class SkillManageInput(BaseModel):
        action: str = Field(
            description=(
                "操作类型。"
                "create: 创建新技能（需要 content）; "
                "edit: 完整重写已有技能（需要 content）; "
                "patch: 精确查找替换（需要 old_string 和 new_string）; "
                "delete: 删除技能; "
                "write_file: 写入捆绑资源文件（需要 file_path 和 file_content）; "
                "remove_file: 删除捆绑资源文件（需要 file_path）"
            )
        )
        name: str | None = Field(
            default=None,
            description="技能 ID（create/edit/patch/delete/write_file/remove_file 都需要）"
        )
        content: str | None = Field(
            default=None,
            description="完整的 SKILL.md 内容（YAML frontmatter + Markdown body）。create 和 edit 必需。"
        )
        category: str | None = Field(
            default=None,
            description="可选分类，如 'coding', 'research'。仅 create 时使用，默认 'general'。"
        )
        old_string: str | None = Field(
            default=None,
            description="要查找的精确文本（patch 必需）。包含足够的上下文以确保唯一性。"
        )
        new_string: str | None = Field(
            default=None,
            description="替换文本（patch 必需）。使用空字符串删除匹配的文本。"
        )
        file_path: str | None = Field(
            default=None,
            description="捆绑资源文件路径，如 'references/api-guide.md'。write_file/remove_file 必需，patch 可选（默认 SKILL.md）。"
        )
        file_content: str | None = Field(
            default=None,
            description="文件内容。write_file 必需。"
        )

    def skill_manage(
        action: str,
        name: str | None = None,
        content: str | None = None,
        category: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        file_path: str | None = None,
        file_content: str | None = None,
    ) -> str:
        """
        管理技能：创建、编辑、修补、删除技能及捆绑资源。

        技能是你的程序性记忆——将成功的做法固化为可复用的知识。
        当复杂任务成功完成、克服了错误、用户纠正了方法后取得成效时，
        主动提议创建技能。
        """
        from .manager import SkillManager
        from .validation import (
            validate_skill_name,
            validate_frontmatter,
            validate_content_size,
            validate_file_size,
            validate_supporting_file_path,
            security_scan,
        )
        from pathlib import Path

        # ── 参数校验 ──
        if not action or action not in ("create", "edit", "patch", "delete", "write_file", "remove_file"):
            return f"错误：无效的 action '{action}'。可用: create, edit, patch, delete, write_file, remove_file"

        if not name:
            return "错误：name 参数是必需的。"

        try:
            # ── action: create ──
            if action == "create":
                if not content:
                    return "错误：创建技能需要 content（完整的 SKILL.md 文本，含 YAML frontmatter）。"

                # 校验名称
                ok, err = validate_skill_name(name)
                if not ok:
                    return f"错误：技能名称校验失败 - {err}"

                # 校验 name 与 frontmatter 中的 name 一致
                import re, yaml
                match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
                if not match:
                    return "错误：content 必须以 YAML frontmatter (---) 开头。"
                try:
                    fm = yaml.safe_load(match.group(1))
                    fm_name = fm.get("name", "") if isinstance(fm, dict) else ""
                    if fm_name != name:
                        return f"错误：frontmatter 中的 name '{fm_name}' 与参数 name '{name}' 不一致。"
                except yaml.YAMLError as e:
                    return f"错误：YAML frontmatter 解析失败 - {e}"

                # 校验 frontmatter
                ok, err = validate_frontmatter(content)
                if not ok:
                    return f"错误：{err}"

                # 校验内容大小
                ok, err = validate_content_size(content)
                if not ok:
                    return f"错误：{err}"

                # 安全扫描
                ok, err = security_scan(content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                # 检查名称冲突
                existing = skill_manager.get_skill(name)
                if existing:
                    return f"错误：技能 '{name}' 已存在。请使用 edit 修改或 delete 删除后重建。"

                # 解析并保存
                skill = SkillManager.from_raw_content(content)
                if not skill:
                    return "错误：无法解析 content 为有效的技能。请检查 YAML frontmatter 格式。"

                if category:
                    try:
                        from .models import SkillCategory as SC
                        skill.category = SC(category)
                    except ValueError:
                        pass

                skill_manager.create_skill(skill.to_dict())
                # 同步配置文件
                if skill_manager.config_manager:
                    skill_manager.config_manager.sync_with_directory([name])
                    skill_manager.config_manager.set_enabled(name, True)
                    skill_manager.config_manager.set_auto_inject(name, False)

                return (
                    f"✅ 技能 '{name}' 创建成功。\n"
                    f"路径: data/skills/{name}/\n"
                    f"💡 提示：使用 write_file action 添加捆绑资源文件（scripts/, references/, assets/）。"
                )

            # ── action: edit ──
            elif action == "edit":
                if not content:
                    return "错误：编辑技能需要 content（完整的更新后 SKILL.md 文本）。"

                existing = skill_manager.get_skill(name)
                if not existing:
                    return f"错误：技能 '{name}' 不存在。使用 get_skills() 查看可用技能列表。"

                # 校验
                ok, err = validate_frontmatter(content)
                if not ok:
                    return f"错误：{err}"
                ok, err = validate_content_size(content)
                if not ok:
                    return f"错误：{err}"
                ok, err = security_scan(content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                # 解析并更新
                skill = SkillManager.from_raw_content(content)
                if not skill:
                    return "错误：无法解析 content 为有效的技能。"

                skill_manager.update_skill(name, skill.to_dict())
                return f"✅ 技能 '{name}' 已更新。"

            # ── action: patch ──
            elif action == "patch":
                if not old_string:
                    return "错误：patch 需要 old_string（要查找的精确文本）。"
                if new_string is None:
                    return "错误：patch 需要 new_string（替换文本，空字符串可删除匹配内容）。"

                existing = skill_manager.get_skill(name)
                if not existing:
                    return f"错误：技能 '{name}' 不存在。"
                if existing.metadata.get("resource_read_only"):
                    return f"错误：Plugin 技能 '{name}' 是只读资源，不能直接修补。"

                # 确定目标文件
                skill_dir = Path(existing.metadata.get("skill_dir", ""))
                if not skill_dir.exists():
                    return f"错误：技能目录不存在: {skill_dir}"

                if file_path:
                    ok, err = validate_supporting_file_path(file_path)
                    if not ok:
                        return f"错误：{err}"
                    target_file = skill_dir / file_path
                else:
                    target_file = skill_dir / "SKILL.md"

                if not target_file.exists():
                    return f"错误：文件不存在: {target_file}"

                current_content = target_file.read_text(encoding="utf-8")

                # 精确查找替换
                count = current_content.count(old_string)
                if count == 0:
                    # 尝试宽松匹配（去除首尾空白）
                    stripped = old_string.strip()
                    count_stripped = current_content.count(stripped)
                    if count_stripped > 0:
                        old_string = stripped
                        count = count_stripped
                    else:
                        preview = current_content[:400] + ("..." if len(current_content) > 400 else "")
                        return (
                            f"错误：在文件中未找到 old_string。\n"
                            f"请检查 old_string 是否与文件内容精确匹配。\n"
                            f"文件预览（前 400 字符）:\n{preview}"
                        )
                if count > 1:
                    return (
                        f"错误：old_string 匹配到 {count} 处。请包含更多上下文以确保唯一性，"
                        f"或确保 old_string 精确且唯一。"
                    )

                new_content = current_content.replace(old_string, new_string)

                # 如果修改的是 SKILL.md，重新校验
                if not file_path:
                    ok, err = validate_frontmatter(new_content)
                    if not ok:
                        return f"错误：patch 会破坏 SKILL.md 结构 - {err}"
                    ok, err = validate_content_size(new_content)
                    if not ok:
                        return f"错误：{err}"
                    ok, err = security_scan(new_content)
                    if not ok:
                        return f"错误：安全扫描未通过。\n{err}"

                # 回滚式写入：备份原内容
                original_content = current_content
                try:
                    target_file.write_text(new_content, encoding="utf-8")
                except Exception:
                    target_file.write_text(original_content, encoding="utf-8")
                    return "错误：写入文件失败，已回滚。"

                # 更新内存中的技能对象
                if not file_path:
                    updated_skill = SkillManager.from_raw_content(new_content)
                    if updated_skill:
                        # from_raw_content 不保留运行时元数据，需从旧对象恢复
                        for key in ("skill_dir", "has_scripts", "has_references", "has_assets"):
                            if key in existing.metadata:
                                updated_skill.metadata[key] = existing.metadata[key]
                        skill_manager._skills[name] = updated_skill

                target_label = f"skill '{name}' 中的 SKILL.md" if not file_path else f"skill '{name}' 中的 {file_path}"
                return f"✅ 已修补 {target_label}（1 处替换）。"

            # ── action: delete ──
            elif action == "delete":
                existing = skill_manager.get_skill(name)
                if not existing:
                    return f"错误：技能 '{name}' 不存在。"

                success = skill_manager.delete_skill(name)
                if success:
                    # 同步配置文件
                    if skill_manager.config_manager:
                        skill_manager.config_manager.remove_skill_config(name)
                    return f"✅ 技能 '{name}' 已删除。"
                else:
                    return f"错误：删除技能 '{name}' 失败。"

            # ── action: write_file ──
            elif action == "write_file":
                if not file_path:
                    return "错误：write_file 需要 file_path（如 'references/api-guide.md'）。"
                if file_content is None:
                    return "错误：write_file 需要 file_content。"

                existing = skill_manager.get_skill(name)
                if not existing:
                    return f"错误：技能 '{name}' 不存在。请先使用 create action 创建。"
                if existing.metadata.get("resource_read_only"):
                    return f"错误：Plugin 技能 '{name}' 是只读资源，不能写入附属文件。"

                # 校验文件路径
                ok, err = validate_supporting_file_path(file_path)
                if not ok:
                    return f"错误：{err}"

                # 校验文件大小
                ok, err = validate_file_size(file_content)
                if not ok:
                    return f"错误：{err}"

                # 安全扫描
                ok, err = security_scan(file_content)
                if not ok:
                    return f"错误：安全扫描未通过。\n{err}"

                success, msg = skill_manager.loader.write_supporting_file(name, file_path, file_content)
                if success:
                    # 更新元数据标记
                    subdir = file_path.split("/")[0] if "/" in file_path else file_path
                    if subdir == "scripts":
                        existing.metadata["has_scripts"] = True
                    elif subdir == "references":
                        existing.metadata["has_references"] = True
                    elif subdir == "assets":
                        existing.metadata["has_assets"] = True
                    return f"✅ {msg}"
                else:
                    return f"错误：{msg}"

            # ── action: remove_file ──
            elif action == "remove_file":
                if not file_path:
                    return "错误：remove_file 需要 file_path。"

                existing = skill_manager.get_skill(name)
                if not existing:
                    return f"错误：技能 '{name}' 不存在。"
                if existing.metadata.get("resource_read_only"):
                    return f"错误：Plugin 技能 '{name}' 是只读资源，不能删除附属文件。"

                ok, err = validate_supporting_file_path(file_path)
                if not ok:
                    return f"错误：{err}"

                success, msg = skill_manager.loader.remove_supporting_file(name, file_path)
                if success:
                    # 更新元数据标记
                    skill_dir = Path(existing.metadata.get("skill_dir", ""))
                    subdir = file_path.split("/")[0] if "/" in file_path else file_path
                    if subdir == "scripts":
                        existing.metadata["has_scripts"] = (skill_dir / "scripts").exists()
                    elif subdir == "references":
                        existing.metadata["has_references"] = (skill_dir / "references").exists()
                    elif subdir == "assets":
                        existing.metadata["has_assets"] = (skill_dir / "assets").exists()
                    return f"✅ {msg}"
                else:
                    return f"错误：{msg}"

        except Exception as e:
            logger.error(f"skill_manage 工具执行失败 (action={action}, name={name}): {e}", exc_info=True)
            return f"错误：操作失败 - {str(e)}"

    return StructuredTool(
        name="skill_manage",
        description=(
            "管理技能库：创建、编辑、修补、删除技能及捆绑资源。\n"
            "技能是你的程序性记忆——将经过验证的方法固化为可复用知识。\n\n"
            "何时创建：复杂任务成功完成（5+ 次工具调用）、克服了非平凡错误、"
            "用户纠正后找到了正确方法、发现了可复用的工作流程、"
            "用户要求记住某个操作流程。\n"
            "何时更新：技能指令过时或有误、发现了缺失的步骤或陷阱。\n"
            "在完成困难的迭代式任务后，主动提议保存为技能。跳过简单的一次性任务。\n"
            "创建/删除前与用户确认。\n\n"
            "可用 actions：\n"
            "- create: 创建新技能（需要 name + content）\n"
            "- edit: 完整重写已有技能（需要 name + content）\n"
            "- patch: 精确查找替换（需要 name + old_string + new_string）\n"
            "- delete: 删除技能（需要 name）\n"
            "- write_file: 写入捆绑资源文件（需要 name + file_path + file_content）\n"
            "- remove_file: 删除捆绑资源文件（需要 name + file_path）"
        ),
        func=skill_manage,
        args_schema=SkillManageInput,
    )


def create_skill_group_manage_tool(skill_manager: "SkillManager"):
    """
    创建 skill_group_manage 工具 — LLM 自主管理技能分组和属组关系。

    支持 6 个 action：
    - list_groups: 列出所有分组及其包含的技能数量
    - create_group: 创建新分组
    - update_group: 更新分组名称或描述
    - delete_group: 删除分组（自动清理属组引用）
    - add_to_group: 将技能加入指定分组
    - remove_from_group: 将技能移出指定分组
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class SkillGroupManageInput(BaseModel):
        action: str = Field(
            description=(
                "操作类型。"
                "list_groups: 列出所有技能分组; "
                "create_group: 创建新分组（需要 group_id + name）; "
                "update_group: 更新分组信息（需要 group_id）; "
                "delete_group: 删除分组（需要 group_id）; "
                "add_to_group: 将技能加入分组（需要 skill_id + group_id）; "
                "remove_from_group: 将技能移出分组（需要 skill_id + group_id）"
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
        skill_id: str | None = Field(
            default=None,
            description="技能ID（add_to_group/remove_from_group 需要）"
        )

    def skill_group_manage(
        action: str,
        group_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        skill_id: str | None = None,
    ) -> str:
        """
        管理技能分组和属组关系。

        分组用于将技能按主题或用途分类，Agent 可以通过分组来筛选可见的技能范围。
        使用 add_to_group / remove_from_group 为技能分配分类。
        """
        from .validation import validate_skill_name

        valid_actions = ("list_groups", "create_group", "update_group", "delete_group", "add_to_group", "remove_from_group")
        if action not in valid_actions:
            return f"错误：无效的 action '{action}'。可用: {', '.join(valid_actions)}"

        config = skill_manager.config_manager
        if not config:
            return "错误：技能配置管理器不可用。"

        try:
            # ── action: list_groups ──
            if action == "list_groups":
                groups = config.get_groups()
                if not groups:
                    return "当前没有技能分组。"

                parts = ["# 技能分组列表\n"]
                for g in groups:
                    gid = g.get("id", "")
                    gname = g.get("name", gid)
                    gdesc = g.get("description", "")
                    # 统计组内技能数
                    skill_ids = config.get_skills_in_group(gid)
                    count = len(skill_ids)
                    parts.append(f"## {gname} (`{gid}`)")
                    if gdesc:
                        parts.append(f"  描述: {gdesc}")
                    parts.append(f"  包含技能数: {count}")
                    if skill_ids:
                        parts.append(f"  技能: {', '.join(skill_ids)}")
                    parts.append("")
                return "\n".join(parts)

            # ── 以下 action 都需要 group_id ──
            if not group_id:
                return f"错误：{action} 需要 group_id 参数。"

            # ── action: create_group ──
            if action == "create_group":
                if not name:
                    return "错误：创建分组需要 name（分组显示名称）。"

                # 校验 group_id 名称格式（复用 skill name 校验规则）
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
                    return f"✅ 技能分组 '{name}' (`{group_id}`) 已创建。"
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
                # 统计组内技能
                skill_ids = config.get_skills_in_group(group_id)

                ok = config.delete_group(group_id)
                if ok:
                    msg = f"✅ 分组 '{gname}' (`{group_id}`) 已删除。"
                    if skill_ids:
                        msg += f"\n已从 {len(skill_ids)} 个技能中移除此分组引用。"
                    return msg
                return f"错误：删除分组 '{group_id}' 失败。"

            # ── action: add_to_group ──
            elif action == "add_to_group":
                if not skill_id:
                    return "错误：add_to_group 需要 skill_id 参数。"

                # 检查技能是否存在
                sk = skill_manager.get_skill(skill_id)
                if not sk:
                    return f"错误：技能 '{skill_id}' 不存在。使用 get_skills() 查看可用技能。"

                # 检查分组是否存在
                group = config.get_group(group_id)
                if not group:
                    return f"错误：分组 '{group_id}' 不存在。使用 skill_group_manage(action='list_groups') 查看可用分组。"

                # 获取当前属组
                current_groups = config.get_skill_group_ids(skill_id)
                if group_id in current_groups:
                    return f"技能 '{skill_id}' 已在分组 '{group_id}' 中，无需重复添加。"

                current_groups.append(group_id)
                ok = config.set_skill_group_ids(skill_id, current_groups)
                if ok:
                    gname = group.get("name", group_id)
                    return f"✅ 已将技能 '{skill_id}' 加入分组 '{gname}' (`{group_id}`)。"
                return f"错误：添加技能 '{skill_id}' 到分组 '{group_id}' 失败。"

            # ── action: remove_from_group ──
            elif action == "remove_from_group":
                if not skill_id:
                    return "错误：remove_from_group 需要 skill_id 参数。"

                # 检查技能是否存在
                sk = skill_manager.get_skill(skill_id)
                if not sk:
                    return f"错误：技能 '{skill_id}' 不存在。"

                # 检查分组是否存在
                group = config.get_group(group_id)
                if not group:
                    return f"错误：分组 '{group_id}' 不存在。"

                # 获取当前属组
                current_groups = config.get_skill_group_ids(skill_id)
                if group_id not in current_groups:
                    return f"技能 '{skill_id}' 不在分组 '{group_id}' 中。"

                current_groups = [g for g in current_groups if g != group_id]
                ok = config.set_skill_group_ids(skill_id, current_groups)
                if ok:
                    gname = group.get("name", group_id)
                    return f"✅ 已将技能 '{skill_id}' 移出分组 '{gname}' (`{group_id}`)。"
                return f"错误：移除技能 '{skill_id}' 从分组 '{group_id}' 失败。"

        except Exception as e:
            logger.error(f"skill_group_manage 工具执行失败 (action={action}, group_id={group_id}, skill_id={skill_id}): {e}", exc_info=True)
            return f"错误：操作失败 - {str(e)}"

    return StructuredTool(
        name="skill_group_manage",
        description=(
            "管理技能分组及技能与分组的属组关系。\n"
            "分组用于将技能按主题或用途分类，Agent 可据此筛选可见的技能范围。\n\n"
            "可用 actions：\n"
            "- list_groups: 列出所有技能分组及其包含的技能\n"
            "- create_group: 创建新分组（需要 group_id + name，可选 description）\n"
            "- update_group: 更新分组名称或描述（需要 group_id，至少提供 name 或 description 之一）\n"
            "- delete_group: 删除分组（需要 group_id，自动清理属组引用）\n"
            "- add_to_group: 将技能加入分组（需要 skill_id + group_id）\n"
            "- remove_from_group: 将技能移出分组（需要 skill_id + group_id）"
        ),
        func=skill_group_manage,
        args_schema=SkillGroupManageInput,
    )
