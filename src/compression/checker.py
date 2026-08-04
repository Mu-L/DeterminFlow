"""
压缩检查器 - 决策引擎：是否压缩、选什么策略

根据设计文档，压缩检查有三个嵌入点：
1. 调用前：每次 API 调用前检查
2. 调用后（无错）：当前不触发压缩（保持惰性）
3. 错误捕获：API 返回 413 / 上下文超限
"""
import logging
from typing import Dict, Any, Optional, List
from enum import Enum

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, ToolMessage

from .config import get_compression_config_manager
from .utils import estimate_messages_tokens
from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS, get_model_manager
from src.core.utils import estimate_tokens

logger = logging.getLogger(__name__)


class CompressionStrategy(Enum):
    """压缩策略枚举"""
    NONE = "none"  # 不压缩
    MICRO = "micro"  # MicroCompact - 工具结果微压缩
    FULL = "full"  # FullCompact - 全量摘要压缩
    REACTIVE = "reactive"  # ReactiveCompact - 渐进式丢弃压缩


class CompressionDecision:
    """压缩决策结果"""

    def __init__(
        self,
        strategy: CompressionStrategy,
        reason: str,
        details: Dict[str, Any] = None
    ):
        self.strategy = strategy
        self.reason = reason
        self.details = details or {}

    def __repr__(self):
        return f"CompressionDecision(strategy={self.strategy.value}, reason={self.reason})"


class CompressionChecker:
    """
    压缩检查器 - 决策引擎

    职责：
    1. 检查是否需要压缩
    2. 选择压缩策略
    3. 提供决策详情
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        self.model_manager = get_model_manager()

    def pre_check(
        self,
        messages: List[BaseMessage],
        model_override: str | None = None
    ) -> CompressionDecision:
        """
        API调用前检查

        Args:
            messages: 当前消息列表
            model_override: 模型覆盖，格式 "provider_id:model_name"

        Returns:
            压缩决策结果
        """
        # 检查压缩是否启用
        if not self.config_manager.is_enabled():
            return CompressionDecision(
                strategy=CompressionStrategy.NONE,
                reason="压缩功能已禁用"
            )

        # 获取模型配置
        model_info = self.model_manager.get_model_info(model_override)
        max_context_tokens = model_info.get(
            "maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS
        )

        # 计算当前上下文token数
        current_tokens = self._estimate_messages_tokens(messages)
        usage_ratio = current_tokens / max_context_tokens if max_context_tokens > 0 else 0

        # 获取配置
        general_config = self.config_manager.get_general_config()
        micro_config = self.config_manager.get_micro_compact_config()

        compaction_threshold = general_config.get("compactionThreshold", 0.80)

        # 检查是否需要FullCompact
        if usage_ratio > compaction_threshold:
            return CompressionDecision(
                strategy=CompressionStrategy.FULL,
                reason=f"上下文占用率 {usage_ratio:.2%} 超过阈值 {compaction_threshold:.2%}",
                details={
                    "current_tokens": current_tokens,
                    "max_tokens": max_context_tokens,
                    "usage_ratio": usage_ratio,
                    "threshold": compaction_threshold,
                }
            )

        # 检查是否需要MicroCompact
        tool_result_count = self._count_tool_results(messages)
        tool_result_tokens = self._estimate_tool_result_tokens(messages)
        tool_result_ratio = tool_result_tokens / max_context_tokens if max_context_tokens > 0 else 0

        max_tool_results = micro_config.get("maxToolResults", 15)
        tool_result_token_ratio = micro_config.get("toolResultTokenRatio", 0.40)

        if (tool_result_count > max_tool_results and
            tool_result_ratio > tool_result_token_ratio):
            return CompressionDecision(
                strategy=CompressionStrategy.MICRO,
                reason=f"工具结果数量 {tool_result_count} 超过阈值 {max_tool_results}，"
                       f"且token占比 {tool_result_ratio:.2%} 超过阈值 {tool_result_token_ratio:.2%}",
                details={
                    "tool_result_count": tool_result_count,
                    "max_tool_results": max_tool_results,
                    "tool_result_tokens": tool_result_tokens,
                    "tool_result_ratio": tool_result_ratio,
                    "tool_result_token_ratio": tool_result_token_ratio,
                }
            )

        # 不需要压缩
        return CompressionDecision(
            strategy=CompressionStrategy.NONE,
            reason="未达到压缩阈值",
            details={
                "current_tokens": current_tokens,
                "max_tokens": max_context_tokens,
                "usage_ratio": usage_ratio,
                "tool_result_count": tool_result_count,
            }
        )

    def error_check(
        self,
        error: Exception,
        messages: List[BaseMessage]
    ) -> CompressionDecision:
        """
        错误捕获检查

        Args:
            error: API错误
            messages: 当前消息列表

        Returns:
            压缩决策结果
        """
        error_str = str(error).lower()

        # 检查是否是上下文超限错误
        if any(keyword in error_str for keyword in ["413", "context overflow", "too large", "token limit"]):
            return CompressionDecision(
                strategy=CompressionStrategy.REACTIVE,
                reason=f"API错误触发ReactiveCompact: {error}",
                details={
                    "error": str(error),
                    "message_count": len(messages),
                }
            )

        # 其他错误不触发压缩
        return CompressionDecision(
            strategy=CompressionStrategy.NONE,
            reason=f"非上下文超限错误: {error}",
            details={"error": str(error)}
        )

    def _estimate_messages_tokens(self, messages: List[BaseMessage]) -> int:
        """估算消息列表的token数（不含 SystemMessage，含 tool_calls）"""
        return estimate_messages_tokens(messages, include_system=False, include_tool_calls=True)

    def _count_tool_results(self, messages: List[BaseMessage]) -> int:
        """统计工具结果数量"""
        count = 0
        for msg in messages:
            if isinstance(msg, ToolMessage):
                count += 1
        return count

    def _estimate_tool_result_tokens(self, messages: List[BaseMessage]) -> int:
        """估算工具结果的token数"""
        total_tokens = 0
        for msg in messages:
            if isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                total_tokens += estimate_tokens(content)
        return total_tokens

    def get_context_stats(
        self,
        messages: List[BaseMessage],
        model_override: str | None = None
    ) -> Dict[str, Any]:
        """
        获取上下文统计信息

        Args:
            messages: 当前消息列表
            model_override: 模型覆盖

        Returns:
            统计信息字典
        """
        model_info = self.model_manager.get_model_info(model_override)
        max_context_tokens = model_info.get(
            "maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS
        )

        current_tokens = self._estimate_messages_tokens(messages)
        usage_ratio = current_tokens / max_context_tokens if max_context_tokens > 0 else 0

        tool_result_count = self._count_tool_results(messages)
        tool_result_tokens = self._estimate_tool_result_tokens(messages)

        return {
            "current_tokens": current_tokens,
            "max_tokens": max_context_tokens,
            "usage_ratio": usage_ratio,
            "message_count": len(messages),
            "tool_result_count": tool_result_count,
            "tool_result_tokens": tool_result_tokens,
            "model_info": model_info,
        }


# 全局实例
_compression_checker: Optional[CompressionChecker] = None


def get_compression_checker() -> CompressionChecker:
    """获取全局 CompressionChecker 实例"""
    global _compression_checker
    if _compression_checker is None:
        _compression_checker = CompressionChecker()
    return _compression_checker
