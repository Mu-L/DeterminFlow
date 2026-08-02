"""
PromptOrchestrator - 提示词编排运行时

重构后简化：
- 配置源统一为 PromptManager
- 移除 SectionsConfigManager 依赖
- 始终使用 sections 组装
"""
import logging
from typing import TYPE_CHECKING

from .sub_agent_prompts import build_sub_agent_sections
from .placeholders import build_tools_section, render_prompt_template, should_skip_section

from src.core.utils import estimate_tokens
from src.agent.definition import get_agent_definition

if TYPE_CHECKING:
    from src.prompts.manager import PromptManager
    from src.skills.manager import SkillManager
    from src.rules.manager import RuleManager

logger = logging.getLogger(__name__)


def _get_visible_rule_group_ids(agent_type: str) -> list[str] | None:
    """获取 agent 的可见规则组 ID 列表"""
    agent_def = get_agent_definition(agent_type)
    if agent_def and agent_def.visible_rule_group_ids is not None:
        return agent_def.visible_rule_group_ids
    return None


def _get_visible_skill_group_ids(agent_type: str) -> list[str] | None:
    """获取 agent 的可见技能组；空列表明确表示不可见任何技能。"""
    agent_def = get_agent_definition(agent_type)
    if agent_def and agent_def.visible_skill_group_ids is not None:
        return agent_def.visible_skill_group_ids
    return None


class PromptOrchestrator:
    """提示词编排运行时，实现 5 级优先级覆盖链和可观测性接口。"""

    def __init__(
        self,
        prompt_manager: "PromptManager",
        skill_manager: "SkillManager | None" = None,
        rule_manager: "RuleManager | None" = None,
    ):
        self._pm = prompt_manager
        self._skill_manager = skill_manager
        self._rule_manager = rule_manager

    def build_effective_prompt(
        self,
        override: str | None = None,
        coordinator: str | None = None,
        agent: str | None = None,
        custom: str | None = None,
        append: str | None = None,
        agent_type: str = "default",
        prompt_template: str = "main",
        include_skills: bool = True,
        skills_mode: str = "discovery",
        include_rules: bool = True,
        tools: list | None = None,
        is_workflow: bool = False,
        workflow_overview: str = "",
        workflow_structure: str = "",
        workflow_definition_json: str = "",
    ) -> str:
        """构建有效的 system prompt（5 级优先级覆盖链 + skills + rules 注入）。

        Args:
            override: 完全覆盖模式
            coordinator: 协调者模式
            agent: agent 特定 prompt
            custom: 自定义 prompt
            append: 追加内容
            agent_type: agent 类型（用于筛选适用的 skills 和 rules）
            prompt_template: 提示词模板名（从 prompts_config.json 加载 sections）
            include_skills: 是否注入 skills
            skills_mode: skills 披露模式
            include_rules: 是否注入 rules
            is_workflow: 是否工作流上下文（跳过 workflow_only=false 的 section）
        """
        if override:
            result = override
            if append:
                result = result + "\n\n" + append
            logger.debug("使用 override prompt（完全替代模式）")
            return result

        if coordinator:
            result = coordinator
            if append:
                result = result + "\n\n" + append
            logger.debug("使用 coordinator prompt")
            return result

        if agent:
            base_sections_text = agent
            logger.debug("使用 agent prompt（替换 default）")
        elif custom:
            base_sections_text = custom
            logger.debug("使用 custom prompt（替换 default）")
        else:
            # 始终使用 sections 组装（workflow_only 过滤由 _assemble_default_sections 处理）
            # 先构建 skills/rules，再组装 sections，避免 should_skip_section 因空值跳过 skills_guidance/rules_guidance
            skills_section = self._build_skills_section(agent_type, include_skills, skills_mode, is_workflow)
            rules_section, rules_reminder = self._build_rules_section(agent_type, include_rules, is_workflow)

            dynamic_values = self._build_dynamic_values(
                agent_type=agent_type,
                include_skills=include_skills,
                skills_mode=skills_mode,
                include_rules=include_rules,
                tools=tools,
                is_workflow=is_workflow,
                workflow_overview=workflow_overview,
                workflow_structure=workflow_structure,
                workflow_definition_json=workflow_definition_json,
            )
            dynamic_values["skills_section"] = skills_section
            dynamic_values["rules_section"] = rules_section
            dynamic_values["rules_reminder"] = rules_reminder
            base_sections_text = self._assemble_default_sections(dynamic_values, is_workflow=is_workflow, prompt_template=prompt_template)
            logger.debug("使用 sections 组装 prompt")

        parts = [base_sections_text]

        if append and append.strip():
            parts.append(append.strip())

        result = "\n\n".join(parts)
        return result

    def _build_skills_section(self, agent_type: str, include_skills: bool,
                               skills_mode: str, is_workflow: bool) -> str:
        """构建 skills 注入文本（单一职责）"""
        if not (include_skills and self._skill_manager):
            return ""
        section = self._skill_manager.build_skills_section(
            agent_type,
            mode=skills_mode,
            workflow_context=is_workflow,
            visible_skill_group_ids=_get_visible_skill_group_ids(agent_type),
        )
        if section:
            logger.debug(f"已注入 skills section (agent_type={agent_type}, mode={skills_mode})")
        return section

    def _build_rules_section(self, agent_type: str, include_rules: bool,
                              is_workflow: bool) -> tuple[str, str]:
        """构建 rules 注入文本（返回 (rules_section, rules_reminder)）。

        rules_reminder 是精简版规则摘要，在长上下文末尾重复注入，
        防止完整规则被遗忘。当规则较短时直接复用全文。
        """
        if not (include_rules and self._rule_manager):
            return "", ""
        visible_ids = _get_visible_rule_group_ids(agent_type)
        section = self._rule_manager.build_rules_section(
            agent_type, preamble="", workflow_context=is_workflow,
            visible_rule_group_ids=visible_ids,
        )
        if not section:
            return "", ""
        logger.debug(f"已注入 rules section (agent_type={agent_type})")
        # 精简版 reminder：仅列出规则名称 + 描述，不包含完整正文
        rules = self._rule_manager.list_by_agent_type(
            agent_type, workflow_context=is_workflow,
            visible_rule_group_ids=visible_ids,
        )
        if len(rules) <= 1:
            reminder = section
        else:
            reminder_lines = ["<rules_reminder>"]
            for rule in rules:
                reminder_lines.append(f"- **{rule.name}**: {rule.description}")
            reminder_lines.append("\n</rules_reminder>")
            reminder = "\n".join(reminder_lines)
        return section, reminder

    def _build_dynamic_values(
        self,
        agent_type: str,
        include_skills: bool,
        skills_mode: str,
        include_rules: bool,
        tools: list | None,
        is_workflow: bool = False,
        workflow_overview: str = "",
        workflow_structure: str = "",
        workflow_definition_json: str = "",
    ) -> dict[str, str]:
        """构建 dynamic_values 用于 sections 组装（不含 skills/rules，由调用方追加）"""
        return {
            "tools_section": build_tools_section(tools),
            "workflow_overview": workflow_overview,
            "workflow_structure": workflow_structure,
            "workflow_definition_json": workflow_definition_json,
        }

    def _assemble_default_sections(self, dynamic_values: dict[str, str] | None = None, is_workflow: bool = False, prompt_template: str = "main") -> str:
        """组装默认 sections（实时从 JSON 读取，按 order 排序，过滤 disabled 和 workflow_only 不匹配的 section，替换动态占位符）"""
        dynamic_values = dynamic_values or {}
        config_sections = self._pm.get_sections(prompt_template)

        parts = []
        for sec in config_sections:
            if not sec.get("enabled", True):
                continue
            # workflow_only 过滤：非工作流上下文跳过 workflow_only=true 的 section
            if sec.get("workflow_only", False) and not is_workflow:
                continue
            # chat_only 过滤：工作流上下文跳过 chat_only=true 的 section
            if sec.get("chat_only", False) and is_workflow:
                continue
            name = sec.get("name", "")
            content = sec.get("content", "")
            if should_skip_section(name, dynamic_values):
                continue
            parts.append(render_prompt_template(content, dynamic_values))
        return "\n\n".join(parts)

    def build_sub_agent_prompt(self, custom_append: str = "", agent_type: str = "default", include_skills: bool = True, skills_mode: str = "discovery", include_rules: bool = True, is_workflow_node: bool = False, upstream_summary: str = "", prompt_template: str = "subagent", template_vars: dict[str, str] | None = None) -> str:
        """构建 Sub Agent 的 system prompt（从 JSON 配置加载）。

        Args:
            custom_append: 自定义追加内容
            agent_type: agent 类型（用于 skills/rules 过滤）
            include_skills: 是否注入 skills
            skills_mode: skills 披露模式（"discovery" 或 "full"）
            include_rules: 是否注入 rules
            is_workflow_node: 是否为工作流节点
            upstream_summary: 上游节点产出摘要
            prompt_template: 提示词模板名（从 prompts_config.json 加载 sections）
            template_vars: 自定义变量块的值 {key: value}
        """
        config_sections = self._pm.get_sections(prompt_template)

        skills_section = self._build_skills_section(agent_type, include_skills, skills_mode, is_workflow_node)
        rules_section, _ = self._build_rules_section(agent_type, include_rules, is_workflow_node)

        sections = build_sub_agent_sections(
            config_sections=config_sections,
            custom_append=custom_append,
            skills_section=skills_section,
            rules_section=rules_section,
            is_workflow_node=is_workflow_node,
            upstream_summary=upstream_summary,
            template_vars=template_vars,
        )
        parts = [s.content for s in sections]
        return "\n\n".join(parts)

    def dump_sections(self) -> list[dict]:
        """导出所有 section 的详细信息（可观测性）。"""
        config_sections = self._pm.get_sections()
        result = []
        for sec in config_sections:
            content = sec.get("content", "")
            result.append({
                "name": sec.get("name", ""),
                "content": content,
                "token_estimate": estimate_tokens(content),
                "cache_break": sec.get("cache_break", False),
                "cache_break_reason": sec.get("cache_break_reason", ""),
                "enabled": sec.get("enabled", True),
                "workflow_only": sec.get("workflow_only", False),
                "order": sec.get("order", 0),
            })
        return result

    def get_sections_summary(self) -> list[dict]:
        """获取各 section 的摘要统计（不含完整内容）。"""
        config_sections = self._pm.get_sections()
        result = []
        for sec in config_sections:
            content = sec.get("content", "")
            preview = content[:80] + "..." if len(content) > 80 else content
            result.append({
                "name": sec.get("name", ""),
                "token_estimate": estimate_tokens(content),
                "cache_break": sec.get("cache_break", False),
                "cache_break_reason": sec.get("cache_break_reason", ""),
                "workflow_only": sec.get("workflow_only", False),
                "content_preview": preview,
            })
        return result

    def get_total_token_estimate(self) -> int:
        return sum(
            estimate_tokens(s.get("content", ""))
            for s in self._pm.get_sections()
        )

    def get_effective_prompt_info(
        self,
        override: str | None = None,
        coordinator: str | None = None,
        agent: str | None = None,
        custom: str | None = None,
        append: str | None = None,
        agent_type: str = "default",
        include_skills: bool = True,
        skills_mode: str = "discovery",
    ) -> dict:
        effective = self.build_effective_prompt(
            override=override, coordinator=coordinator, agent=agent, custom=custom, append=append,
            agent_type=agent_type, include_skills=include_skills, skills_mode=skills_mode,
        )

        if override:
            priority_used = "override"
        elif coordinator:
            priority_used = "coordinator"
        elif agent:
            priority_used = "agent"
        elif custom:
            priority_used = "custom"
        else:
            priority_used = "sections"

        return {
            "effective_prompt": effective,
            "token_estimate": estimate_tokens(effective),
            "priority_used": priority_used,
            "has_append": bool(append),
            "sections_count": len(self._pm.get_sections()),
            "skills_mode": skills_mode if include_skills else None,
        }
