"""
MicroCompact策略 - 工具结果微压缩

最轻量的压缩方式，纯本地操作，零API调用成本。
解决的问题是：工具调用密集场景下，tool_result内容快速膨胀占满上下文。
"""
import logging
from typing import List, Dict, Any

from langchain_core.messages import BaseMessage, ToolMessage

from ..config import get_compression_config_manager
from src.core.model_manager import DEFAULT_MAX_CONTEXT_TOKENS, get_model_manager
from src.core.utils import estimate_tokens

logger = logging.getLogger(__name__)


class MicroCompactStrategy:
    """
    MicroCompact策略实现

    设计定位：
    最轻量的压缩方式，纯本地操作，零API调用成本。
    解决的问题是：工具调用密集场景下，tool_result内容快速膨胀占满上下文。

    触发条件：
    必须同时满足以下两个条件：
    - 条件A: 历史工具结果数量 > microCompact.maxToolResults
    - 条件B: 工具结果占用的token数 > modelMaxTokens × microCompact.toolResultTokenRatio

    执行逻辑：
    1. 从messages尾部向前扫描（最近的结果优先保留）
    2. 标记出所有tool_role消息
    3. 保留最近的N个工具结果原文（keepRecentToolResults，默认5）
    4. 其余tool_result的content替换为占位符
    5. 不修改tool_use块（输入参数保留）
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        self.model_manager = get_model_manager()

    async def execute(
        self,
        messages: List[BaseMessage],
        model_override: str | None = None,
        max_context_tokens: int | None = None,
    ) -> List[BaseMessage]:
        """
        执行MicroCompact压缩

        Args:
            messages: 当前消息列表

        Returns:
            压缩后的消息列表
        """
        micro_config = self.config_manager.get_micro_compact_config()
        keep_recent = micro_config.get("keepRecentToolResults", 5)
        max_tool_results = micro_config.get("maxToolResults", 15)
        tool_result_ratio = micro_config.get("toolResultTokenRatio", 0.40)
        placeholder = micro_config.get("placeholder", "[Content compacted]")

        if max_context_tokens is None:
            model_info = self.model_manager.get_model_info(model_override)
            max_context_tokens = model_info.get(
                "maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS
            )

        tool_message_indices = self._tool_message_indices(messages)
        if len(tool_message_indices) <= keep_recent:
            return messages

        indices_to_compress = (
            tool_message_indices[:-keep_recent]
            if keep_recent > 0
            else tool_message_indices
        )
        compressed_messages = list(messages)
        tool_tokens = sum(
            self._message_tokens(messages[index]) for index in tool_message_indices
        )
        tool_budget = max(1, int(max_context_tokens * tool_result_ratio))
        dense_history = (
            len(tool_message_indices) > max_tool_results
            and tool_tokens > tool_budget
        )

        compressed_count = 0
        for idx in indices_to_compress:
            msg = compressed_messages[idx]
            if not isinstance(msg, ToolMessage):
                continue
            if dense_history:
                new_content = placeholder
            elif self._message_tokens(msg) > tool_budget:
                new_content = self._clip_content(msg.content, tool_budget)
            else:
                continue
            compressed_messages[idx] = self._copy_tool_message(msg, new_content)
            compressed_count += 1

        logger.info(
            "MicroCompact完成: 压缩了 %s 个历史工具结果，保留了最近 %s 个",
            compressed_count,
            keep_recent,
        )
        return compressed_messages

    def compact_for_request(
        self,
        messages: List[BaseMessage],
        *,
        max_context_tokens: int,
    ) -> List[BaseMessage]:
        """为单次超限请求裁剪工具结果，不改动持久化的完整 record。

        常规 MicroCompact 始终保护最近 N 条工具结果。只有整个请求已经超过
        模型硬窗口时，调用方才会进入这里，并按“历史优先、最新最后”的顺序
        对工具结果做保头保尾裁剪。
        """
        micro_config = self.config_manager.get_micro_compact_config()
        keep_recent = micro_config.get("keepRecentToolResults", 5)
        tool_result_ratio = micro_config.get("toolResultTokenRatio", 0.40)
        tool_budget = max(1, int(max_context_tokens * tool_result_ratio))

        compacted = list(messages)
        tool_indices = self._tool_message_indices(compacted)
        if not tool_indices:
            return compacted

        protected_start = max(0, len(tool_indices) - keep_recent)
        candidates = tool_indices[:protected_start] + tool_indices[protected_start:]
        total_tokens = sum(
            self._message_tokens(compacted[index]) for index in tool_indices
        )
        excess = total_tokens - tool_budget
        if excess <= 0:
            return compacted

        clipped_count = 0
        for index in candidates:
            if excess <= 0:
                break
            msg = compacted[index]
            if not isinstance(msg, ToolMessage):
                continue
            current_tokens = self._message_tokens(msg)
            minimum_tokens = min(64, current_tokens)
            target_tokens = max(minimum_tokens, current_tokens - excess)
            if target_tokens >= current_tokens:
                continue
            clipped_content = self._clip_content(msg.content, target_tokens)
            compacted[index] = self._copy_tool_message(msg, clipped_content)
            reduced = max(0, current_tokens - self._message_tokens(compacted[index]))
            excess -= reduced
            clipped_count += 1

        if clipped_count:
            logger.warning(
                "请求超过硬窗口，保头保尾裁剪了 %s 条工具结果",
                clipped_count,
            )
        return compacted

    @staticmethod
    def _tool_message_indices(messages: List[BaseMessage]) -> list[int]:
        return [
            index for index, msg in enumerate(messages)
            if isinstance(msg, ToolMessage)
        ]

    @staticmethod
    def _message_tokens(msg: BaseMessage) -> int:
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        return estimate_tokens(content)

    @staticmethod
    def _copy_tool_message(msg: ToolMessage, content: str) -> ToolMessage:
        """只替换模型可见 content，保留 ToolMessage 的全部协议元数据。"""
        return msg.model_copy(update={"content": content})

    @staticmethod
    def _clip_content(content: Any, target_tokens: int) -> str:
        text = content if isinstance(content, str) else str(content or "")
        original_tokens = estimate_tokens(text)
        if original_tokens <= target_tokens:
            return text

        marker = (
            f"\n...[Tool result clipped: original ~{original_tokens} tokens]...\n"
        )
        target_tokens = max(64, target_tokens)
        # 使用现有启发式做二分，不引入模型 tokenizer 或“有效预算”概念。
        # 这样中英文混合的工具结果也能稳定落到目标范围内。
        low = 0
        high = len(text)
        best = marker
        while low <= high:
            keep_chars = (low + high) // 2
            head_chars = keep_chars // 2
            tail_chars = keep_chars - head_chars
            tail = text[-tail_chars:] if tail_chars else ""
            candidate = f"{text[:head_chars]}{marker}{tail}"
            if estimate_tokens(candidate) <= target_tokens:
                best = candidate
                low = keep_chars + 1
            else:
                high = keep_chars - 1
        return best

    def get_compression_stats(
        self,
        original_messages: List[BaseMessage],
        compressed_messages: List[BaseMessage]
    ) -> Dict[str, Any]:
        """获取压缩统计信息"""
        from src.compression.utils import calc_compression_stats, calc_tool_message_stats
        stats = calc_compression_stats(original_messages, compressed_messages)
        stats.update(calc_tool_message_stats(original_messages, compressed_messages))
        return stats
