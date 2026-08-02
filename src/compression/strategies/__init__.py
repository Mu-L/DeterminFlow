"""
压缩策略模块

实现三种压缩策略：
- MicroCompact: 工具结果微压缩
- FullCompact: 全量摘要压缩
- ReactiveCompact: 渐进式丢弃压缩
"""

from .micro import MicroCompactStrategy
from .full import FullCompactStrategy
from .reactive import ReactiveCompactStrategy

__all__ = [
    "MicroCompactStrategy",
    "FullCompactStrategy",
    "ReactiveCompactStrategy",
]