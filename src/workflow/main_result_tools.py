"""供 Main 使用的 Workflow 结果、产物与节点消息工具。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool

from .tools import (
    GetNodeMessagesArgs,
    GetTaskResultArgs,
    ReadTaskArtifactArgs,
    _ensure_task_owned,
    _ensure_tool_execution_allowed,
    _fail,
    _ok,
    _resolve_task_ref,
    _wait_for_task_snapshot,
)

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager
    from src.workflow.manager import WorkflowManager


logger = logging.getLogger(__name__)


def _authorize_result_read(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
    workflow_id: str,
    task_id: str,
    action_desc: str,
) -> tuple[str, str, str | None]:
    workflow_id, task_id, error = _resolve_task_ref(
        session_manager, workflow_id, task_id, action_desc,
    )
    if error:
        return "", "", error
    error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
    if error:
        return "", "", error
    _, error = _ensure_task_owned(workflow_manager, workflow_id, task_id)
    return workflow_id, task_id, error


def create_get_task_result_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    async def _get_task_result(
        workflow_id: str = "",
        task_id: str = "",
        wait_for: str = "none",
        timeout_seconds: float | None = 0,
    ) -> str:
        workflow_id, task_id, error = _authorize_result_read(
            workflow_manager, session_manager, workflow_id, task_id, "获取结果",
        )
        if error:
            return error
        try:
            task, ownership_error = _ensure_task_owned(
                workflow_manager, workflow_id, task_id,
            )
            if ownership_error:
                return ownership_error
            assert task is not None
            task, wait_metadata, wait_error = await _wait_for_task_snapshot(
                workflow_manager,
                workflow_id,
                task_id,
                task,
                wait_for=wait_for,
                timeout_seconds=timeout_seconds,
            )
            if wait_error:
                return wait_error
            result = workflow_manager.get_task_result(workflow_id, task_id)
            if result is None:
                return _fail(f"任务 {task_id} 不存在", error="task_not_found")
            return _ok(**{**result, **wait_metadata})
        except Exception as exc:
            logger.exception("get_task_result 失败")
            return _fail(f"查询结果失败: {exc}")

    return StructuredTool(
        name="get_task_result",
        description=(
            "获取当前 Main 所拥有任务的结果摘要、节点输出和工作空间内产物描述符。"
            "可在同一次调用中等待终态或需要 Main 介入的状态；"
            "terminal=false 表示返回的是当前快照。"
        ),
        args_schema=GetTaskResultArgs,
        func=lambda **kw: None,
        coroutine=_get_task_result,
    )


def create_read_task_artifact_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    async def _read_task_artifact(
        artifact_ref: str,
        offset: int = 0,
        limit: int = 20_000,
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        workflow_id, task_id, error = _authorize_result_read(
            workflow_manager, session_manager, workflow_id, task_id, "读取产物",
        )
        if error:
            return error
        try:
            result = workflow_manager.read_task_artifact(
                workflow_id,
                task_id,
                artifact_ref,
                offset=offset,
                limit=limit,
            )
            if result is None:
                return _fail(f"任务 {task_id} 不存在", error="task_not_found")
            if result.get("success"):
                return _ok(**{
                    key: value for key, value in result.items()
                    if key != "success"
                })
            return _fail(
                result.get("message", "读取产物失败"),
                **{
                    key: value for key, value in result.items()
                    if key not in {"success", "message"}
                },
            )
        except Exception as exc:
            logger.exception("read_task_artifact 失败")
            return _fail(f"读取产物失败: {exc}")

    return StructuredTool(
        name="read_task_artifact",
        description=(
            "读取 get_task_result 返回的 UTF-8 文本产物。"
            "只能访问当前 Main 所拥有任务的工作空间内文件，并按字符分页返回。"
        ),
        args_schema=ReadTaskArtifactArgs,
        func=lambda **kw: None,
        coroutine=_read_task_artifact,
    )


def create_get_node_messages_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    async def _get_node_messages(
        node_id: str,
        workflow_id: str = "",
        task_id: str = "",
    ) -> str:
        workflow_id, task_id, error = _authorize_result_read(
            workflow_manager,
            session_manager,
            workflow_id,
            task_id,
            "查询节点消息",
        )
        if error:
            return error
        try:
            result = workflow_manager.get_node_messages(
                workflow_id, task_id, node_id,
            )
            if result is None:
                return _fail(f"任务 {task_id} 不存在", error="task_not_found")
            return _ok(workflow_id=workflow_id, task_id=task_id, **result)
        except Exception as exc:
            logger.exception("get_node_messages 失败")
            return _fail(f"查询节点消息失败: {exc}")

    return StructuredTool(
        name="get_node_messages",
        description=(
            "获取当前 Main 所拥有任务中指定节点的可见 Agent 消息、摘要和错误。"
            "用于审查节点细节；不要用它代替 get_task_status 轮询。"
        ),
        args_schema=GetNodeMessagesArgs,
        func=lambda **kw: None,
        coroutine=_get_node_messages,
    )
