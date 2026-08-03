"""
MicroCompact策略 - 工具结果微压缩

最轻量的压缩方式，纯本地操作，零API调用成本。
解决的问题是：工具调用密集场景下，tool_result内容快速膨胀占满上下文。
"""
import logging
from typing import List, Dict, Any

from langchain_core.messages import BaseMessage, AIMessage, ToolMessage

from ..config import get_compression_config_manager

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

    async def execute(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        执行MicroCompact压缩

        Args:
            messages: 当前消息列表

        Returns:
            压缩后的消息列表
        """
        # 获取配置
        micro_config = self.config_manager.get_micro_compact_config()
        keep_recent = micro_config.get("keepRecentToolResults", 5)
        placeholder = micro_config.get("placeholder", "[Content compacted]")

        # 找出所有ToolMessage的位置
        tool_message_indices = []
        for i, msg in enumerate(messages):
            if isinstance(msg, ToolMessage):
                tool_message_indices.append(i)

        # 如果工具结果数量不超过保留数量，不进行压缩
        if len(tool_message_indices) <= keep_recent:
            return messages

        # 确定需要压缩的工具结果
        # 保留最近的keep_recent个工具结果
        indices_to_compress = tool_message_indices[:-keep_recent]

        # 创建消息副本
        compressed_messages = list(messages)

        # 压缩工具结果
        compressed_count = 0
        for idx in indices_to_compress:
            msg = compressed_messages[idx]
            if isinstance(msg, ToolMessage):
                # 替换内容为占位符
                compressed_messages[idx] = ToolMessage(
                    content=placeholder,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name if hasattr(msg, "name") else None,
                    status=getattr(msg, "status", "success"),
                    additional_kwargs=dict(getattr(msg, "additional_kwargs", {})),
                )
                compressed_count += 1
                logger.debug(f"压缩工具结果: index={idx}, tool_call_id={msg.tool_call_id}")

        logger.info(f"MicroCompact完成: 压缩了 {compressed_count} 个工具结果，"
                    f"保留了最近 {keep_recent} 个")

        return compressed_messages

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
