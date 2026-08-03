"""
ToolAssembler — 统一工具组装管理器

根据 agent_type 组装完整的工具列表：
- main: 通过 ToolRegistry 获取全部功能工具
- subagent: 通过 ToolRegistry 获取 available pool，经 create_sub_agent_tools() 过滤
- compressor: 无工具（空列表）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager
    from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolAssembler:
    """工具组装管理器。

    通过 ToolRegistry 获取工具工厂，消除直接 import 工厂函数的冗余。
    """

    def __init__(
        self,
        session_manager: "SessionManager",
        registry: "ToolRegistry",
        approval_manager: Any = None,
        skill_manager: Any = None,
        rule_manager: Any = None,
        llm_client: Any = None,
    ):
        self._session_mgr = session_manager
        self._registry = registry
        self._approval_mgr = approval_manager
        self._skill_mgr = skill_manager
        self._rule_mgr = rule_manager
        self._llm_client = llm_client

    def _resolve_prompt_template(self, agent_type: str) -> str:
        """解析 agent_type 对应的 prompt_template。"""
        from src.agent.definition import get_agent_definition
        agent_def = get_agent_definition(agent_type)
        if agent_def:
            return agent_def.prompt_template
        return "subagent"

    def build(self, agent_type: str, **kwargs) -> list[StructuredTool]:
        """组装指定 agent_type 的完整工具列表。通过 AgentDefinition.prompt_template 路由。

        Args:
            agent_type: Agent 类型（如 "main", "coder", "researcher" 等）
            **kwargs:
                is_workflow_node: bool    # subagent — 是否注入 complete_node_task
                agent_definition: AgentDefinition  # subagent — agent 配置
                llm_client: LLM           # 可覆盖默认 LLM 客户端
                workspace_path: str       # 是否生成 coding tools（subagent 用）
                is_workflow: bool         # main — 是否注入 workflow 工具

        Returns:
            LangChain StructuredTool 列表
        """
        prompt_template = self._resolve_prompt_template(agent_type)

        if prompt_template == "main":
            return self._build_main(**kwargs)
        elif prompt_template == "compressor":
            return []
        else:
            # 未知模板（例如 Extension 提供的自定义 Agent 类型）
            # 统一按 subagent 路由工具，prompt sections 由 prompts_config 单独加载
            return self._build_subagent(**kwargs)

    def _build_main(self, **kwargs) -> list[StructuredTool]:
        """组装 main agent 工具：registry 中的全部功能工具。

        Main agent 始终可访问完整 Workflow Task 工具集。任务工具优先使用显式
        workflow_id/task_id，并依据 main_session_id 校验所有权；两者都省略时才从
        session context 读取最近任务作为兼容默认值。
        """
        llm_client = kwargs.get("llm_client", self._llm_client)

        deps: dict[str, Any] = dict(
            session_manager=self._session_mgr,
            llm_client=llm_client,
            skill_manager=self._skill_mgr,
            rule_manager=self._rule_mgr,
            approval_manager=self._approval_mgr,
            workflow_manager=getattr(self._session_mgr, "_workflow_manager", None),
        )

        all_tools = self._registry.instantiate_all(**deps)

        logger.info(f"Main agent 工具组装完成: {len(all_tools)} 个")
        return all_tools

    def _build_subagent(self, **kwargs) -> list[StructuredTool]:
        """组装 sub agent 工具：registry 中的功能工具 + 通信工具。

        registry 提供 available pool（含 skills 工具），
        经 create_sub_agent_tools() 根据 agent_definition 过滤后，
        再由 resolve_agent_tools() 追加 send_message / complete_node_task。
        """
        from src.mcp.tool_adapter import create_sub_agent_tools

        is_workflow_node = kwargs.get("is_workflow_node", False)
        agent_definition = kwargs.get("agent_definition")
        workspace_path = kwargs.get("workspace_path", "")
        enable_complete_node_task = kwargs.get("enable_complete_node_task", True)
        enable_reject_upstream = kwargs.get("enable_reject_upstream", False)

        # 从 registry 获取全部功能工具作为 available pool
        available = self._registry.instantiate_all(
            session_manager=self._session_mgr,
            approval_manager=self._approval_mgr,
            skill_manager=self._skill_mgr,
            rule_manager=self._rule_mgr,
        )

        # 仅在 workspace_path 为空时剔除 coding 工具
        # （有 workspace → 保留全部，后续由 agent_definition.tools 白名单过滤）
        if not workspace_path:
            coding_names = {
                "read_file", "write_to_file", "replace_in_file",
                "search_files", "search_file", "list_files", "list_code_definitions",
                "apply_diff", "execute_command", "ask_user",
            }
            available = [t for t in available if t.name not in coding_names]

        sub_tools = create_sub_agent_tools(
            self._session_mgr,
            available_mcp_tools=available,
            agent_definition=agent_definition,
            is_workflow_node=is_workflow_node,
            enable_complete_node_task=enable_complete_node_task,
            enable_reject_upstream=enable_reject_upstream,
        )

        logger.info(f"Sub agent 工具组装完成: {len(sub_tools)} 个 (workflow={is_workflow_node})")
        return sub_tools
