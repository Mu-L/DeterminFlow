"""
工具调用鉴权层 - 通过 ToolNode 的 awrap_tool_call 机制实现统一前置检查

设计思路：
- 每种限制对应一个 ToolGuard 子类
- make_guarded_wrapper() 生成 awrap_tool_call 回调，传给 ToolNode
- 新增限制只需新建 Guard 子类并加入 guards 列表，无需修改其他代码

扩展示例：
    class RateLimitGuard(ToolGuard): ...
    class PermissionGuard(ToolGuard): ...
    class CostGuard(ToolGuard): ...
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from langchain_core.messages import ToolMessage

from src.core.types import GuardResult

logger = logging.getLogger(__name__)


class ToolGuard(ABC):
    """鉴权检查基类，每种限制继承它"""

    @abstractmethod
    def check(self, state: dict, tool_call: dict) -> GuardResult:
        """
        检查单个 tool_call 是否允许执行。

        Args:
            state: 当前 AgentState（dict 形式）
            tool_call: 包含 name, args, id 的工具调用字典

        Returns:
            GuardResult: allowed=True 通过，allowed=False 拒绝并附带 reason
        """
        ...


class RoundsGuard(ToolGuard):
    """工具调用轮次限制：remaining_rounds <= 0 时拒绝"""

    def check(self, state: dict, tool_call: dict) -> GuardResult:
        remaining = state.get("remaining_rounds")
        if remaining is None:
            remaining = 0
        if remaining <= 0:
            # 提取追踪信息用于日志
            session_id = state.get("session_id", "unknown")
            agent_type = state.get("agent_type", "unknown")
            tool_name = tool_call.get("name", "unknown")
            metadata = state.get("metadata", {})
            max_rounds = metadata.get("max_rounds", "unknown")

            logger.warning(
                f"工具调用被轮次限制拒绝 | "
                f"session_id={session_id} | "
                f"agent_type={agent_type} | "
                f"tool={tool_name} | "
                f"max_rounds={max_rounds} | "
                f"remaining_rounds={remaining}"
            )

            return GuardResult(
                allowed=False,
                reason="工具调用次数已达本轮上限，无法继续执行。请根据已有信息直接回复用户。",
            )
        return GuardResult(allowed=True)


def make_guarded_wrapper(guards: list[ToolGuard] | None = None):
    """
    生成 awrap_tool_call 回调，供 ToolNode 使用。

    Args:
        guards: 鉴权检查链列表，按顺序执行，任一拒绝即短路返回错误

    Returns:
        异步拦截器函数，签名符合 ToolNode.awrap_tool_call 要求
    """
    _guards = guards or [RoundsGuard()]

    async def _awrap(request, execute):
        state = request.state
        tool_call = request.tool_call

        # 提取追踪信息
        session_id = state.get("session_id", "unknown")
        agent_type = state.get("agent_type", "unknown")
        tool_name = tool_call.get("name", "unknown")

        for guard in _guards:
            result = guard.check(state, tool_call)
            if not result.allowed:
                logger.warning(
                    f"工具调用被拒绝 | "
                    f"session_id={session_id} | "
                    f"agent_type={agent_type} | "
                    f"tool={tool_name} | "
                    f"guard={guard.__class__.__name__} | "
                    f"reason={result.reason}"
                )
                return ToolMessage(
                    content=f"[错误] {result.reason}",
                    tool_call_id=tool_call.get("id", ""),
                    name=tool_name,
                    status="error",
                )

        # 所有 guard 通过，正常执行
        logger.debug(
            f"工具调用通过鉴权 | "
            f"session_id={session_id} | "
            f"agent_type={agent_type} | "
            f"tool={tool_name}"
        )
        return await execute(request)

    return _awrap
