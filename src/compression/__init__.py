"""
上下文压缩模块

实现三种压缩策略：
- MicroCompact: 工具结果微压缩
- FullCompact: 全量摘要压缩
- ReactiveCompact: 渐进式丢弃压缩
"""

from .config import CompressionConfigManager, get_compression_config_manager
from .checker import CompressionChecker, CompressionStrategy, CompressionDecision, get_compression_checker
from .scheduler import CompressionScheduler, get_compression_scheduler
from .post_processor import PostProcessor, get_post_processor
from .transcript import TranscriptSaver, get_transcript_saver

__all__ = [
    "CompressionConfigManager",
    "get_compression_config_manager",
    "CompressionChecker",
    "CompressionStrategy",
    "CompressionDecision",
    "get_compression_checker",
    "CompressionScheduler",
    "get_compression_scheduler",
    "PostProcessor",
    "get_post_processor",
    "TranscriptSaver",
    "get_transcript_saver",
]