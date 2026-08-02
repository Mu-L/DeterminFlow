"""
压缩调度器 - 调度引擎：执行所选策略

根据CompressionChecker的决策，调度执行相应的压缩策略。
"""
import logging
from typing import Dict, Any, Optional, List

from langchain_core.messages import BaseMessage

from .checker import CompressionStrategy, CompressionDecision
from .config import get_compression_config_manager
from .strategies.micro import MicroCompactStrategy
from .strategies.full import FullCompactStrategy
from .strategies.reactive import ReactiveCompactStrategy
from .post_processor import PostProcessor, get_post_processor
from .transcript import TranscriptSaver, get_transcript_saver
from .utils import calc_compression_stats

logger = logging.getLogger(__name__)


class CompressionScheduler:
    """
    压缩调度器 - 调度引擎

    职责：
    1. 根据CompressionChecker的决策执行相应的压缩策略
    2. 协调压缩后处理
    3. 管理历史快照
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        self.micro_strategy = MicroCompactStrategy()
        self.full_strategy = FullCompactStrategy()
        self.reactive_strategy = ReactiveCompactStrategy()
        self.post_processor = get_post_processor()
        self.transcript_saver = get_transcript_saver()

    async def execute(
        self,
        decision: CompressionDecision,
        messages: List[BaseMessage],
        session_id: str = "",
        agent_id: str = "",
        model_override: str | None = None
    ) -> List[BaseMessage]:
        """
        执行压缩策略

        Args:
            decision: 压缩决策
            messages: 当前消息列表
            session_id: 会话ID
            agent_id: Agent ID
            model_override: 模型覆盖

        Returns:
            压缩后的消息列表
        """
        if decision.strategy == CompressionStrategy.NONE:
            return messages

        logger.info(f"执行压缩策略: {decision.strategy.value}, 原因: {decision.reason}")

        try:
            # 执行压缩策略
            if decision.strategy == CompressionStrategy.MICRO:
                compressed_messages = await self.execute_micro_compact(
                    messages, session_id=session_id, agent_id=agent_id
                )
            elif decision.strategy == CompressionStrategy.FULL:
                compressed_messages = await self.execute_full_compact(
                    messages, session_id=session_id, agent_id=agent_id,
                    model_override=model_override
                )
            elif decision.strategy == CompressionStrategy.REACTIVE:
                compressed_messages = await self.execute_reactive_compact(
                    messages, session_id=session_id, agent_id=agent_id
                )
            else:
                logger.warning(f"未知的压缩策略: {decision.strategy}")
                return messages

            logger.info(f"压缩完成: {len(messages)} -> {len(compressed_messages)} 条消息")
            return compressed_messages

        except Exception as e:
            logger.error(f"压缩执行失败: {e}", exc_info=True)
            # 压缩失败时返回原始消息
            return messages

    async def _execute_with_snapshots(
        self,
        strategy_fn,
        messages: List[BaseMessage],
        compression_type: str,
        session_id: str = "",
        agent_id: str = "",
        post_process_fn=None,
    ) -> List[BaseMessage]:
        """通用执行流程：pre 快照 → 执行策略 → 可选后处理 → post 快照。"""
        await self.transcript_saver.save_snapshot(
            messages=messages, session_id=session_id, agent_id=agent_id,
            compression_type=compression_type, snapshot_type="pre-compact"
        )
        compressed_messages = await strategy_fn(messages)
        if post_process_fn is not None:
            compressed_messages = await post_process_fn(compressed_messages)
        await self.transcript_saver.save_snapshot(
            messages=compressed_messages, session_id=session_id, agent_id=agent_id,
            compression_type=compression_type, snapshot_type="post-compact"
        )
        return compressed_messages

    async def execute_micro_compact(
        self,
        messages: List[BaseMessage],
        session_id: str = "",
        agent_id: str = "",
    ) -> List[BaseMessage]:
        """执行MicroCompact策略"""
        return await self._execute_with_snapshots(
            self.micro_strategy.execute, messages, "micro", session_id, agent_id
        )

    async def execute_full_compact(
        self,
        messages: List[BaseMessage],
        session_id: str = "",
        agent_id: str = "",
        model_override: str | None = None
    ) -> List[BaseMessage]:
        """执行FullCompact策略"""
        async def _run(msgs):
            return await self.full_strategy.execute(msgs, model_override=model_override)
        return await self._execute_with_snapshots(
            _run, messages, "full", session_id, agent_id,
            post_process_fn=lambda msgs: self.post_processor.process(msgs, session_id=session_id),
        )

    async def execute_reactive_compact(
        self,
        messages: List[BaseMessage],
        session_id: str = "",
        agent_id: str = "",
    ) -> List[BaseMessage]:
        """执行ReactiveCompact策略"""
        return await self._execute_with_snapshots(
            self.reactive_strategy.execute, messages, "reactive", session_id, agent_id
        )

    def get_compression_stats(
        self,
        original_messages: List[BaseMessage],
        compressed_messages: List[BaseMessage]
    ) -> Dict[str, Any]:
        """获取压缩统计信息"""
        stats = calc_compression_stats(original_messages, compressed_messages)
        # 兼容 scheduler 的 reduction_ratio 字段（= 1 - compression_ratio）
        stats["reduction_ratio"] = 1 - stats["compression_ratio"]
        return stats


# 全局实例
_compression_scheduler: Optional[CompressionScheduler] = None


def get_compression_scheduler() -> CompressionScheduler:
    """获取全局 CompressionScheduler 实例"""
    global _compression_scheduler
    if _compression_scheduler is None:
        _compression_scheduler = CompressionScheduler()
    return _compression_scheduler