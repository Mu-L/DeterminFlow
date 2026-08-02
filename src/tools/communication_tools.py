"""
统一会话通信工具 - complete_task / send_message / complete_node_task / reject_upstream

工具通过 get_session_context() 运行时获取当前 session 信息，
不再通过闭包预捕获 session_id 等参数。

complete_task 是推荐的上报工具，自动判断上下文（对话/工作流）并路由。
send_message 和 complete_node_task 保留以兼容旧代码。
reject_upstream 用于下游节点拒绝上游节点产出并触发重试。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.session.context import get_session_context


def _sync_stub(**kwargs) -> str:
    """同步调用占位符 — 会话通信工具仅支持异步调用。"""
    raise NotImplementedError("会话通信工具仅支持异步调用，请使用 coroutine 接口")

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)


class CompleteTaskArgs(BaseModel):
    """complete_task 工具参数"""
    summary: str = Field(description="任务结论，一两句话概括完成了什么")
    status: str = Field(default="success", description="'success' | 'failure'")
    artifact_path: str = Field(default="", description="完整产出文件路径（可选。产出较长时写到 workspace 文件，填此字段）")
    error: str = Field(default="", description="失败原因（status='failure' 时填写）")


class SendMessageArgs(BaseModel):
    """send_message 工具参数"""
    session_id: str = Field(description="目标会话ID")
    content: str = Field(description="消息内容")


class CompleteNodeTaskArgs(BaseModel):
    """complete_node_task 工具参数（保留兼容）"""
    summary: str = Field(description="本节点完成的任务摘要，包括改了什么文件、核心产出")
    status: str = Field(default="success", description="'success' | 'failure'")
    branch: str | None = Field(default=None, description="条件分支选择（暂预留）")
    error: str | None = Field(default=None, description="失败原因（status=failure 时）")


def create_complete_task_tool(
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 complete_task 工具 — 自动判断上下文（对话/工作流）并上报。

    对话场景：路由消息到 parent session
    工作流场景：通知 engine（on_node_complete）+ 路由消息到 parent
    parent_id 从 contextvars 获取，子 Agent 无需传目标 ID。
    """

    async def _complete_task(summary: str, status: str = "success",
                              artifact_path: str = "", error: str = "") -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")
        parent_id = ctx.get("parent_id", "")
        on_complete = ctx.get("on_node_complete")

        result: dict = {"success": True, "session_id": session_id, "status": status}

        # 工作流场景：通知 engine
        if on_complete:
            on_complete(session_id, summary, status, error)

        # 路由消息到 parent（仅对话场景；工作流场景由引擎发审批请求）
        if parent_id and not on_complete:
            content_parts = [f"[子Agent 完成] {summary}"]
            if artifact_path:
                content_parts.append(f"\n完整产出: {artifact_path}")
            content = "".join(content_parts)
            await session_manager.route_message(
                from_session_id=session_id,
                to_session_id=parent_id,
                content=content,
            )

        if status == "failure":
            result["message"] = f"任务失败: {error or summary}"
        else:
            result["message"] = "任务已上报"

        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="complete_task",
        description=(
            "完成任务后上报结果。自动判断上下文（对话/工作流）并路由到正确目标。\n"
            "- summary: 一句话结论（如 coder 说明改了什么文件、researcher 说明核心发现）\n"
            "- artifact_path: 产出较长时先写到 workspace 文件再填此字段；产出简短可省略\n"
            "- 完成后必须调用此工具，不要只输出文本了事"
        ),
        args_schema=CompleteTaskArgs,
        func=_sync_stub,
        coroutine=_complete_task,
    )


def create_send_message_tool(
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 send_message 工具（from_session_id 通过 contextvars 运行时获取）。"""

    async def _send_message(session_id: str, content: str) -> str:
        ctx = get_session_context()
        _from = ctx.get("session_id", "")
        if not _from:
            main = session_manager.get_main_session()
            if main:
                _from = main.session_id
        result = await session_manager.route_message(
            from_session_id=_from,
            to_session_id=session_id,
            content=content,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="send_message",
        description="向指定会话发送消息",
        args_schema=SendMessageArgs,
        func=_sync_stub,
        coroutine=_send_message,
    )


def create_complete_node_task_tool(
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 complete_node_task 工具（session_id 和 on_complete 通过 contextvars 运行时获取）。"""

    async def _complete_node_task(summary: str, status: str = "success",
                                   branch: str | None = None, error: str | None = None) -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")
        on_complete = ctx.get("on_node_complete")

        result = {"success": True, "session_id": session_id,
                  "status": status, "summary": summary}

        if on_complete:
            on_complete(session_id, summary, status, error or "")

        if status == "failure":
            result["message"] = f"节点任务失败: {error or summary}"
        else:
            result["message"] = "节点任务已完成，引擎将流转到下一个节点"

        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="complete_node_task",
        description=(
            "完成当前工作流节点的任务并通知引擎流转到下一个节点。"
            "当你完成节点分配的任务后，必须调用此工具汇报产出摘要。"
            "status 设为 'success' 表示成功完成，设为 'failure' 表示任务失败。"
        ),
        args_schema=CompleteNodeTaskArgs,
        func=_sync_stub,
        coroutine=_complete_node_task,
    )


class RejectUpstreamArgs(BaseModel):
    """reject_upstream 工具参数"""
    reason: str = Field(description="拒绝原因，详细说明上游产出为什么不达标")
    target_node_id: str = Field(
        default="",
        description="要拒绝的上游节点ID（可选，默认为直接上游节点）"
    )


def create_reject_upstream_tool(
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 reject_upstream 工具 — 允许下游节点拒绝上游节点产出并触发重试。

    调用此工具后：
    1. 拒绝原因将发送到目标上游节点的 session 中
    2. 引擎将当前节点状态设为 waiting_retry
    3. 上游节点重新完成后，其新产出将追加到当前节点的 session
    """

    async def _reject_upstream(reason: str, target_node_id: str = "") -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")
        on_reject = ctx.get("on_reject_upstream")

        result = {"success": True, "session_id": session_id}

        if on_reject:
            reject_result = on_reject(session_id, reason, target_node_id)
            if isinstance(reject_result, dict):
                result.update(reject_result)
            else:
                result["message"] = f"已拒绝上游产出，等待上游节点重新执行。拒绝原因: {reason}"
        else:
            result["success"] = False
            result["message"] = "当前上下文不支持 reject_upstream 工具（可能不在工作流节点中）"

        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="reject_upstream",
        description=(
            "拒绝上游节点的产出并要求其重新执行。"
            "当你认为上游节点的产出不达标、质量有问题或需要改进时，调用此工具。"
            "调用时必须提供详细的拒绝原因，帮助上游节点理解问题所在。"
            "拒绝后，上游节点将收到你的反馈并重新执行任务。"
            "注意：每个节点有最大拒绝次数限制，超过限制后将无法继续拒绝。"
        ),
        args_schema=RejectUpstreamArgs,
        func=_sync_stub,
        coroutine=_reject_upstream,
    )
