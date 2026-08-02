"""
PromptBuilder — 统一提示词构建管理器

支持 main / subagent / compressor 三种 agent 类型的系统提示词构建：
- main: 通过 PromptOrchestrator.build_effective_prompt() 组装
- subagent: 通过 build_sub_agent_prompt() 组装
- compressor: 通过 build_compressor_prompt() 组装

可选上下文由 Extension Host 预取后通过 extension_context 注入。
session_meta 在 session 对象传入后通过 render_prompt_template() 内联替换。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.prompts import create_orchestrator, PromptManager
from src.prompts.compressor_prompts import build_compressor_prompt
from src.prompts.placeholders import build_session_meta_text, render_prompt_template

if TYPE_CHECKING:
    from src.agent.session import AgentSession
    from src.skills.manager import SkillManager
    from src.rules.manager import RuleManager

logger = logging.getLogger(__name__)


class PromptBuilder:
    """提示词构建管理器。

    构造时注入依赖，build() 方法统一入口。
    """

    def __init__(
        self,
        prompt_manager: PromptManager,
        skill_manager: "SkillManager | None" = None,
        rule_manager: "RuleManager | None" = None,
    ):
        self._pm = prompt_manager
        self._skill_mgr = skill_manager
        self._rule_mgr = rule_manager

    @property
    def prompt_manager(self) -> PromptManager:
        """Expose the layered prompt source for execution-identity checks."""
        return self._pm

    def _resolve_prompt_template(self, agent_type: str) -> str:
        """解析 agent_type 对应的 prompt_template。

        从 AgentDefinition 读取 prompt_template 字段，用于后续 build 路由。
        """
        from src.agent.definition import get_agent_definition
        agent_def = get_agent_definition(agent_type)
        if agent_def:
            template = agent_def.prompt_template
            logger.debug(f"agent_type={agent_type} → prompt_template={template}")
            return template
        # 未找到 agent 定义时，回退到 subagent
        logger.warning(f"未找到 agent_type={agent_type} 的定义，回退 prompt_template=subagent")
        return "subagent"

    @staticmethod
    def _resolve_injection_flags(agent_type: str) -> tuple[bool, bool]:
        """Resolve explicit Agent-level Skill/Rule prompt injection policy."""
        from src.agent.definition import get_agent_definition

        agent_def = get_agent_definition(agent_type)
        if agent_def is None:
            return True, True
        return agent_def.include_skills, agent_def.include_rules

    def build(self, agent_type: str, session: "AgentSession | None" = None, **kwargs) -> str:
        """构建指定 agent_type 的系统提示词。通过 AgentDefinition.prompt_template 路由。

        Args:
            agent_type: Agent 类型（如 "main", "coder", "researcher" 等）
            session: AgentSession 对象（用于生成 session_meta）
            **kwargs:
                is_workflow: bool            # main/subagent — 是否工作流上下文
                upstream_summary: str         # subagent — 上游节点摘要
                custom_append: str            # subagent — 自定义追加内容
                extra_tool_names: list        # subagent — 额外工具名
                tools: list                   # main — 工具列表（用于构建 tools_section）
                extension_context: str        # main — 可选扩展提供的 Prompt 片段
                include_skills: bool          # main — 默认 True
                skills_mode: str              # main — 默认 "auto_inject"
                include_rules: bool           # main — 默认 True
                workflow_definition: object   # main — 工作流定义对象
                template_vars: dict           # subagent — 自定义变量块的值 {key: value}

        Returns:
            完整渲染好的系统提示词文本（含 session_meta）
        """
        prompt_template = self._resolve_prompt_template(agent_type)

        if prompt_template == "main":
            prompt = self._build_main(agent_type, session, **kwargs)
        elif prompt_template == "subagent":
            prompt = self._build_subagent(
                agent_type, session, prompt_template="subagent", **kwargs
            )
        elif prompt_template == "compressor":
            prompt = self._build_compressor(**kwargs)
        else:
            # 非内置模板名（例如 Extension 模板）透传给 orchestrator
            logger.debug(f"使用自定义 prompt_template: {prompt_template}")
            prompt = self._build_subagent(
                agent_type, session, prompt_template=prompt_template, **kwargs
            )

        return prompt

    def _build_main(self, agent_type: str, session: "AgentSession | None", **kwargs) -> str:
        """构建 main 模式提示词（含 Extension 上下文 + session_meta）。

        Args:
            agent_type: 实际 agent 类型（用于 skills/rules 过滤）
        """
        orche = create_orchestrator(
            prompt_manager=self._pm,
            skill_manager=self._skill_mgr,
            rule_manager=self._rule_mgr,
        )

        is_workflow = kwargs.get("is_workflow", False)
        default_skills, default_rules = self._resolve_injection_flags(agent_type)

        # 工作流上下文注入
        workflow_definition = kwargs.get("workflow_definition")
        if workflow_definition:
            from src.workflow.prompt_injector import (
                build_workflow_overview,
                build_workflow_structure,
                build_workflow_definition_json,
            )
            workflow_overview = build_workflow_overview(workflow_definition)
            workflow_structure = build_workflow_structure(workflow_definition)
            workflow_definition_json = build_workflow_definition_json(workflow_definition)
        else:
            workflow_overview = ""
            workflow_structure = ""
            workflow_definition_json = ""

        prompt = orche.build_effective_prompt(
            agent_type=agent_type,
            include_skills=kwargs.get("include_skills", default_skills),
            skills_mode=kwargs.get("skills_mode", "auto_inject"),
            include_rules=kwargs.get("include_rules", default_rules),
            tools=kwargs.get("tools"),
            is_workflow=is_workflow,
            workflow_overview=workflow_overview,
            workflow_structure=workflow_structure,
            workflow_definition_json=workflow_definition_json,
        )

        # 内联替换 {{session_meta}} 占位符
        if session:
            prompt = render_prompt_template(prompt, {"session_meta": build_session_meta_text(session)})

        extension_context = kwargs.get("extension_context", "").strip()
        if extension_context:
            prompt = f"{prompt}\n\n{extension_context}"

        return prompt

    def _build_subagent(
        self,
        agent_type: str,
        session: "AgentSession | None",
        prompt_template: str = "subagent",
        **kwargs,
    ) -> str:
        """构建 subagent 模式提示词（含 session_meta）。

        Args:
            prompt_template: 提示词模板名，路由到 prompts_config.json 中的 sections 配置。
                             默认 "subagent"，Extension 可提供其他模板。
        """
        orche = create_orchestrator(
            prompt_manager=self._pm,
            skill_manager=self._skill_mgr,
            rule_manager=self._rule_mgr,
        )

        is_workflow = kwargs.get("is_workflow", False)
        default_skills, default_rules = self._resolve_injection_flags(agent_type)

        prompt = orche.build_sub_agent_prompt(
            custom_append=kwargs.get("custom_append", ""),
            agent_type=agent_type,
            include_skills=kwargs.get("include_skills", default_skills),
            skills_mode=kwargs.get("skills_mode", "discovery"),
            include_rules=kwargs.get("include_rules", default_rules),
            is_workflow_node=is_workflow,
            upstream_summary=kwargs.get("upstream_summary", ""),
            prompt_template=prompt_template,
            template_vars=kwargs.get("template_vars"),
        )

        # 内联替换 {{session_meta}} 占位符
        if session:
            prompt = render_prompt_template(prompt, {"session_meta": build_session_meta_text(session)})

        return prompt

    def build_static_workflow_base(self, agent_type: str) -> str:
        """Render the non-task-specific Workflow system prompt for identity.

        Session metadata, upstream output, node template values and other Task
        values are intentionally absent. Agent prompt sections, the Agent-level
        append, and the currently injected Skill/Rule text use the exact normal
        renderer and are therefore covered by the returned bytes.
        """
        from src.agent.definition import get_agent_definition

        agent_def = get_agent_definition(agent_type)
        if agent_def is None:
            raise RuntimeError(f"Workflow Agent 定义不存在: {agent_type}")
        return self.build(
            agent_type,
            session=None,
            custom_append=agent_def.system_prompt_template,
            is_workflow=True,
            upstream_summary="",
            template_vars=None,
        )

    def _build_compressor(self, **kwargs) -> str:
        """构建 compressor 模式提示词（不含 session_meta，compressor 为临时会话）。"""
        config_sections = self._pm.get_sections("compressor")
        return build_compressor_prompt(config_sections=config_sections)
