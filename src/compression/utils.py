"""
压缩模块公共工具函数
"""

from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage, ToolMessage

from src.core.utils import estimate_tokens


def get_message_role(msg: BaseMessage) -> str:
    """获取消息角色字符串，供序列化和统计使用。

    Args:
        msg: LangChain 消息对象

    Returns:
        角色字符串：system / user / assistant / tool / msg.type / unknown
    """
    if isinstance(msg, SystemMessage):
        return "system"
    elif isinstance(msg, HumanMessage):
        return "user"
    elif isinstance(msg, AIMessage):
        return "assistant"
    elif isinstance(msg, ToolMessage):
        return "tool"
    else:
        return msg.type if hasattr(msg, "type") else "unknown"


def _msg_content_tokens(msg: BaseMessage) -> int:
    """计算单条消息的 token 数"""
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    return estimate_tokens(content)


def estimate_messages_tokens(
    messages: List[BaseMessage],
    *,
    include_system: bool = True,
    include_tool_calls: bool = True,
) -> int:
    """统一估算消息列表的 token 数。

    Args:
        messages: 消息列表
        include_system: 是否包含 SystemMessage 的 token（checker 跳过，transcript 包含）
        include_tool_calls: 是否计算 AIMessage.tool_calls 的 token（checker 计算，transcript 不计算）

    Returns:
        总 token 数
    """
    total = 0
    for msg in messages:
        if not include_system and isinstance(msg, SystemMessage):
            continue
        total += _msg_content_tokens(msg)
        if include_tool_calls and isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                total += estimate_tokens(str(tc.get("args", "")))
    return total


def calc_compression_stats(
    original_messages: List[BaseMessage],
    compressed_messages: List[BaseMessage],
) -> Dict[str, Any]:
    """
    计算压缩前后通用统计信息。

    Args:
        original_messages: 原始消息列表
        compressed_messages: 压缩后的消息列表

    Returns:
        包含消息数、token 数、tokens_saved、messages_removed、compression_ratio 等字段的字典
    """
    original_count = len(original_messages)
    compressed_count = len(compressed_messages)

    original_tokens = sum(_msg_content_tokens(msg) for msg in original_messages)
    compressed_tokens = sum(_msg_content_tokens(msg) for msg in compressed_messages)

    return {
        "original_message_count": original_count,
        "compressed_message_count": compressed_count,
        "messages_removed": original_count - compressed_count,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "tokens_saved": original_tokens - compressed_tokens,
        "compression_ratio": compressed_tokens / original_tokens if original_tokens > 0 else 1,
    }


def calc_tool_message_stats(
    original_messages: List[BaseMessage],
    compressed_messages: List[BaseMessage],
) -> Dict[str, Any]:
    """
    计算 ToolMessage 相关的额外统计信息，供 micro 策略使用。

    Returns:
        包含工具消息数、token 数等额外字段的字典
    """
    original_tool_count = sum(1 for msg in original_messages if isinstance(msg, ToolMessage))
    compressed_tool_count = sum(1 for msg in compressed_messages if isinstance(msg, ToolMessage))

    original_tool_tokens = sum(
        _msg_content_tokens(msg) for msg in original_messages if isinstance(msg, ToolMessage)
    )
    compressed_tool_tokens = sum(
        _msg_content_tokens(msg) for msg in compressed_messages if isinstance(msg, ToolMessage)
    )

    return {
        "original_tool_count": original_tool_count,
        "compressed_tool_count": compressed_tool_count,
        "tools_compressed": original_tool_count - compressed_tool_count,
        "original_tool_tokens": original_tool_tokens,
        "compressed_tool_tokens": compressed_tool_tokens,
        "tool_tokens_saved": original_tool_tokens - compressed_tool_tokens,
    }
