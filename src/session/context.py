"""
Session 运行时上下文 — 基于 Python contextvars 的线程/协程安全上下文传递。

工具在执行时通过 get_session_context() 动态获取当前会话信息，
无需在工具创建时通过闭包预捕获 session_id、workspace_path 等参数。
"""
from __future__ import annotations

import contextvars

_session_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("session_ctx", default={})


def set_session_context(**kwargs) -> None:
    """设置当前协程的 session 上下文。

    应在 session._invoke_graph() 开头调用，graph 执行期间工具的
    get_session_context() 即可读取到设置的值。
    """
    _session_ctx.set(kwargs)


def get_session_context() -> dict:
    """获取当前协程的 session 上下文。

    Returns:
        dict，至少包含:
            session_id: str      # 当前 session ID
            workspace_path: str  # 工作空间路径
            parent_id: str | None
            agent_type: str
            on_node_complete: callable | None  # 仅 workflow node 场景
    """
    return _session_ctx.get()
