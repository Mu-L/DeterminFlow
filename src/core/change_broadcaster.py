"""进程内状态变化广播器。

事件只负责唤醒等待者；调用方被唤醒后必须重新读取权威状态。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class ChangeBroadcaster:
    """按稳定 key 向所有等待者广播一次状态变化。"""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)

    async def wait(self, key: str, timeout_seconds: float | None) -> bool:
        """等待一次变化；超时返回 False，取消会原样向上传播。"""
        event = asyncio.Event()
        self._waiters[key].add(event)
        try:
            if timeout_seconds is None:
                await event.wait()
                return True
            if timeout_seconds <= 0:
                return False
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
                return True
            except asyncio.TimeoutError:
                return False
        finally:
            waiters = self._waiters.get(key)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    self._waiters.pop(key, None)

    def publish(self, key: str) -> None:
        """唤醒当前订阅该 key 的全部等待者。"""
        for event in self._waiters.pop(key, set()):
            event.set()

    def waiter_count(self, key: str | None = None) -> int:
        """返回等待者数量，供生命周期检查和测试使用。"""
        if key is not None:
            return len(self._waiters.get(key, ()))
        return sum(len(waiters) for waiters in self._waiters.values())
