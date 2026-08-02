"""
ReactiveCompact策略 - 渐进式丢弃压缩

兜底策略，仅在API返回413 Request Too Large或context overflow错误时触发。
解决问题：无法预知的上下文超限错误。
"""
import logging
from typing import List, Dict, Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage, ToolMessage

from ..config import get_compression_config_manager

logger = logging.getLogger(__name__)


class ReactiveCompactStrategy:
    """
    ReactiveCompact策略实现

    设计定位：
    兜底策略，仅在API返回413 Request Too Large或context overflow错误时触发。
    解决问题：无法预知的上下文超限错误。

    触发条件：
    API调用返回413状态码或模型返回上下文超限错误。
    由API调用层的错误处理器捕获并启动。

    执行逻辑（渐进式丢弃，不包含重试调用）：
    1. 从消息队列的头部（跳过 system prompt）开始，找到最旧的完整轮次边界
    2. 移除该完整轮次（user→assistant 及其后续 tool 消息）
    3. 如果剩余消息仍然过多，重复步骤1-2
    4. 最多丢弃 maxRetryCount 个完整轮次
    5. 返回裁剪后的消息列表，由调用方（scheduler）决定是否重试 API 调用
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()

    async def execute(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        执行ReactiveCompact压缩

        Args:
            messages: 当前消息列表

        Returns:
            压缩后的消息列表
        """
        # 获取配置
        reactive_config = self.config_manager.get_reactive_compact_config()
        max_retry_count = reactive_config.get("maxRetryCount", 5)

        # 执行渐进式丢弃
        compressed_messages = self._progressive_discard(messages, max_retry_count)

        logger.info(f"ReactiveCompact完成: {len(messages)} -> {len(compressed_messages)} 条消息")
        return compressed_messages

    def _progressive_discard(
        self,
        messages: List[BaseMessage],
        max_discard_rounds: int
    ) -> List[BaseMessage]:
        """
        渐进式丢弃消息

        Args:
            messages: 消息列表
            max_discard_rounds: 最大丢弃轮次

        Returns:
            压缩后的消息列表
        """
        # 找到system prompt的位置
        system_index = -1
        for i, msg in enumerate(messages):
            if isinstance(msg, SystemMessage):
                system_index = i
                break

        # 如果没有system prompt，从第一条消息开始
        start_index = system_index + 1 if system_index >= 0 else 0

        # 检查消息数量是否足够丢弃
        if len(messages) - start_index < 3:
            logger.warning("消息数量过少，无法进行丢弃")
            return messages

        # 执行丢弃（使用原地删除避免每轮创建新列表）
        current_messages = list(messages)
        discard_count = 0

        for round_num in range(max_discard_rounds):
            # 找到最旧的完整轮次
            round_start = self._find_oldest_round_start(current_messages, start_index)

            if round_start is None:
                logger.info(f"第 {round_num + 1} 轮: 没有找到完整轮次，停止丢弃")
                break

            # 找到该轮次的结束位置
            round_end = self._find_round_end(current_messages, round_start)

            if round_end is None:
                logger.info(f"第 {round_num + 1} 轮: 轮次结束位置无效，停止丢弃")
                break

            # 丢弃该轮次（原地删除，避免 list[:start]+list[end+1:] 创建中间列表）
            num_discarded = round_end - round_start + 1
            del current_messages[round_start:round_end + 1]

            discard_count += num_discarded
            logger.info(f"第 {round_num + 1} 轮: 丢弃了 {num_discarded} 条消息 "
                       f"(索引 {round_start} 到 {round_end})")

            # 检查是否还有足够的消息
            if len(current_messages) - start_index < 3:
                logger.info("剩余消息过少，停止丢弃")
                break

        if discard_count > 0:
            logger.info(f"ReactiveCompact丢弃完成: 共丢弃 {discard_count} 条消息")

        return current_messages

    def _find_oldest_round_start(
        self,
        messages: List[BaseMessage],
        start_index: int
    ) -> Optional[int]:
        """
        找到最旧的完整轮次的起始位置

        完整轮次定义：一组 [user_message → assistant_response] 的配对

        Args:
            messages: 消息列表
            start_index: 开始搜索的索引

        Returns:
            轮次起始索引，未找到返回None
        """
        # 从start_index开始，找到第一个user消息
        for i in range(start_index, len(messages)):
            if isinstance(messages[i], HumanMessage):
                # 检查后面是否有assistant消息
                for j in range(i + 1, len(messages)):
                    if isinstance(messages[j], AIMessage):
                        return i
                    elif isinstance(messages[j], HumanMessage):
                        # 遇到下一个user消息，说明当前轮次不完整
                        break

        return None

    def _find_round_end(
        self,
        messages: List[BaseMessage],
        round_start: int
    ) -> Optional[int]:
        """
        找到轮次的结束位置

        轮次结束位置定义：
        - 从round_start开始，找到对应的assistant消息
        - 如果assistant消息后面有tool消息，也包含在内
        - 直到遇到下一个user消息或消息结束

        Args:
            messages: 消息列表
            round_start: 轮次起始索引

        Returns:
            轮次结束索引，未找到返回None
        """
        # 从round_start开始，找到assistant消息
        assistant_index = None
        for i in range(round_start + 1, len(messages)):
            if isinstance(messages[i], AIMessage):
                assistant_index = i
                break
            elif isinstance(messages[i], HumanMessage):
                # 遇到下一个user消息，说明轮次不完整
                return None

        if assistant_index is None:
            return None

        # 从assistant消息开始，找到轮次结束位置
        end_index = assistant_index

        # 检查assistant消息后面是否有tool消息
        for i in range(assistant_index + 1, len(messages)):
            if isinstance(messages[i], ToolMessage):
                end_index = i
            elif isinstance(messages[i], AIMessage):
                # 如果是连续的assistant消息（可能有多个tool_calls）
                end_index = i
            elif isinstance(messages[i], HumanMessage):
                # 遇到下一个user消息，轮次结束
                break
            else:
                # 其他类型消息，轮次结束
                break

        return end_index

    def get_compression_stats(
        self,
        original_messages: List[BaseMessage],
        compressed_messages: List[BaseMessage]
    ) -> Dict[str, Any]:
        """获取压缩统计信息"""
        from src.compression.utils import calc_compression_stats
        return calc_compression_stats(original_messages, compressed_messages)


# 全局实例
_reactive_compact_strategy: Optional[ReactiveCompactStrategy] = None


def get_reactive_compact_strategy() -> ReactiveCompactStrategy:
    """获取全局 ReactiveCompactStrategy 实例"""
    global _reactive_compact_strategy
    if _reactive_compact_strategy is None:
        _reactive_compact_strategy = ReactiveCompactStrategy()
    return _reactive_compact_strategy