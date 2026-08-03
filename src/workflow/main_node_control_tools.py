"""供 Main 使用的 Workflow 失败节点恢复工具。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool

from .tools import (
    NodeControlArgs,
    _ensure_task_owned,
    _ensure_tool_execution_allowed,
    _fail,
    _resolve_task_ref,
)

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager
    from src.workflow.manager import WorkflowManager


logger = logging.getLogger(__name__)


def _create_node_control_tool(
    *,
    name: str,
    action_desc: str,
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    async def _control_node(
        node_id: str,
        expected_attempt_count: int,
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        workflow_id, task_id, ref_error = _resolve_task_ref(
            session_manager, workflow_id, task_id, action_desc,
        )
        if ref_error:
            return ref_error
        policy_error = _ensure_tool_execution_allowed(
            workflow_manager, workflow_id,
        )
        if policy_error:
            return policy_error
        _, ownership_error = _ensure_task_owned(
            workflow_manager, workflow_id, task_id,
        )
        if ownership_error:
            return ownership_error
        method = getattr(workflow_manager, name)
        try:
            result = await method(
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
                expected_attempt_count=expected_attempt_count,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logger.exception("%s 失败", name)
            return _fail(f"{action_desc}失败: {exc}")

    descriptions = {
        "retry_node": (
            "手动重试当前 Main 所拥有任务的失败节点。必须携带最新 attempt_count；"
            "过期请求会返回 node_control_stale。"
        ),
        "skip_node": (
            "跳过当前 Main 所拥有任务的失败节点并继续原任务。必须携带最新 attempt_count；"
            "跳过会清除该失败节点的无效产出。"
        ),
    }
    return StructuredTool(
        name=name,
        description=descriptions[name],
        args_schema=NodeControlArgs,
        func=lambda **kw: None,
        coroutine=_control_node,
    )


def create_retry_node_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    return _create_node_control_tool(
        name="retry_node",
        action_desc="重试节点",
        workflow_manager=workflow_manager,
        session_manager=session_manager,
    )


def create_skip_node_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    return _create_node_control_tool(
        name="skip_node",
        action_desc="跳过节点",
        workflow_manager=workflow_manager,
        session_manager=session_manager,
    )
