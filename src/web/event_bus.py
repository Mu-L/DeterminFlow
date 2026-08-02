"""
事件总线 - 管理 WebSocket 客户端连接池，广播系统事件

架构改进（Per-WS 队列 + 独立消费者，消除 asyncio.gather 死锁）：
- 每个 WS 连接分配独立的 asyncio.Queue(maxsize=1024) 和专用消费者协程
- 事件生产者只做 put_nowait（队列满时丢弃低优先级事件），不阻塞，不创建 task
- 消费者串行消费队列，逐个 send_text，慢就慢但不阻塞生产者
- 完全移除 _broadcast_to_clients 中的 asyncio.gather 嵌套

通道模型：
- chat: per-session 订阅，前端只收自己看的 session 的 token/tool/chain_end 事件
- events: 全局广播，系统级事件（会话状态变更、wf_task_update 等）
"""
import asyncio
import json
import logging
import os as _os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 每个 WS 连接的事件队列容量（从环境变量读取，默认 1024）
_WS_QUEUE_SIZE = int(_os.getenv("EVENT_QUEUE_SIZE", "1024"))
# WS 发送超时秒数（从环境变量读取，默认 30）
_WS_SEND_TIMEOUT = float(_os.getenv("WS_SEND_TIMEOUT", "30.0"))

# 事件优先级（用于背压丢弃决策）
# 优先级越高越重要，低优先级事件在队列满时优先丢弃
EVENT_PRIORITY: dict[str, int] = {
    "stream_start": 10,
    "stream_end": 10,
    "chain_end": 10,
    "error": 10,
    "llm_usage": 8,
    "tool_start": 7,
    "tool_end": 7,
    "tool_call_delta": 3,
    "reasoning_token": 2,
    "token": 1,
}

# 丢弃阈值：优先级低于此值的事件在队列满时丢弃
# token(1) 和 reasoning_token(2) 在背压时优先丢弃
_DROP_THRESHOLD = 5

# Workflow sub-session 只推送的状态事件类型
WF_STATUS_EVENTS = {
    "stream_start", "stream_end", "chain_end", "error",
    "tool_start", "tool_end", "llm_usage",
}


class _WsConnection:
    """单个 WebSocket 连接的队列 + 消费者管理。"""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_WS_QUEUE_SIZE)
        self._consumer_task: asyncio.Task | None = None
        self._dropped_count = 0

    def start_consumer(self):
        if self._consumer_task and not self._consumer_task.done():
            return
        self._consumer_task = asyncio.create_task(
            self._consume(), name=f"ws-consumer-{id(self.ws)}"
        )

    def cancel_consumer(self):
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
        self._consumer_task = None

    def enqueue(self, message: str, event_type: str) -> bool:
        """投递消息到队列。队列满时根据优先级决定是否丢弃。
        Returns: True 已入队, False 已丢弃
        """
        priority = EVENT_PRIORITY.get(event_type, 5)
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            if priority < _DROP_THRESHOLD:
                self._dropped_count += 1
                return False
            # 高优先级事件：丢弃队首最旧的低优先级消息（FIFO 中无法 peek）
            # 放弃：无法安全地从 asyncio.Queue 中间移除
            self._dropped_count += 1
            return False

    async def _consume(self):
        """串行消费队列，逐个发送到 WS 客户端。"""
        try:
            while True:
                message = await self.queue.get()
                if message is None:  # 停止信号
                    break
                try:
                    await asyncio.wait_for(
                        self.ws.send_text(message),
                        timeout=_WS_SEND_TIMEOUT,
                    )
                except (asyncio.TimeoutError, Exception):
                    # 发送超时或失败 → 停止消费（WS 已断开或僵死）
                    logger.debug(f"WS 消费失败 (id={id(self.ws)}), 停止消费者")
                    break
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("WS 消费者异常退出", exc_info=True)


class EventBus:
    """
    事件总线单例，负责：
    1. 管理 WebSocket 客户端连接（全局 + per-session）+ 独立队列/消费者
    2. 非阻塞投递事件到订阅者队列
    3. 记录事件日志供前端回溯
    """

    def __init__(self):
        # WS 连接注册表：WebSocket → _WsConnection
        self._connections: dict[int, _WsConnection] = {}

        # 通道订阅：channel -> set of ws ids
        self._channel_subscribers: dict[str, set[int]] = {
            "chat": set(),
            "events": set(),
        }

        # Per-session 订阅：session_id -> set of ws ids
        self._session_subscribers: dict[str, set[int]] = {}

        # 事件日志（最近 500 条）
        self._event_log: list[dict] = []
        self._max_log_size = 500

        # 统计数据
        self._tool_call_counts: dict[str, int] = {}
        self._total_tool_calls = 0
        self._total_llm_calls = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

        # 背压统计
        self._dropped_events = 0
        self._enqueued_events = 0

        # 周期性统计日志（每 30s）
        self._stats_task: asyncio.Task | None = None

    def start_periodic_stats(self):
        """启动周期性统计日志（幂等）。"""
        if self._stats_task and not self._stats_task.done():
            return
        self._stats_task = asyncio.get_running_loop().create_task(
            self._periodic_stats(), name="eventbus-stats"
        )

    def stop_periodic_stats(self):
        """停止周期性统计日志。"""
        if self._stats_task and not self._stats_task.done():
            self._stats_task.cancel()
        self._stats_task = None

    async def _periodic_stats(self):
        """每 30s 打印队列深度、丢弃数、连接数等状态。"""
        try:
            while True:
                await asyncio.sleep(30)
                stats = self.get_stats()
                logger.debug(
                    "[BUS] 状态: connections=%d, dropped=%d, enqueued=%d, "
                    "sessions=%d, llm_calls=%d, tool_calls=%d, "
                    "max_queue_depth=%s",
                    stats.get("total_connections", 0),
                    stats.get("dropped_events", 0),
                    stats.get("enqueued_events", 0),
                    len(stats.get("session_subscriptions", {})),
                    stats.get("total_llm_calls", 0),
                    stats.get("total_tool_calls", 0),
                    max(stats.get("queue_depths", {}).values()) if stats.get("queue_depths") else 0,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[BUS] _periodic_stats 异常退出", exc_info=True)

    # ============ 连接管理 ============

    async def subscribe(self, channel: str, ws: WebSocket):
        """订阅一个通道（全局广播）"""
        ws_id = id(ws)
        if ws_id not in self._connections:
            conn = _WsConnection(ws)
            conn.start_consumer()
            self._connections[ws_id] = conn
        if channel not in self._channel_subscribers:
            self._channel_subscribers[channel] = set()
        self._channel_subscribers[channel].add(ws_id)
        logger.info(
            f"WS 客户端已订阅 {channel}，当前连接数: {len(self._channel_subscribers[channel])}"
        )
        # 首次订阅时启动周期性统计日志
        self.start_periodic_stats()

    async def subscribe_session(self, session_id: str, ws: WebSocket):
        """订阅特定 session 的事件（per-session 模式，用于 chat 通道）。"""
        ws_id = id(ws)
        if ws_id not in self._connections:
            conn = _WsConnection(ws)
            conn.start_consumer()
            self._connections[ws_id] = conn
        if session_id not in self._session_subscribers:
            self._session_subscribers[session_id] = set()
        self._session_subscribers[session_id].add(ws_id)
        logger.debug(
            f"WS 订阅 session {session_id}，订阅者数: "
            f"{len(self._session_subscribers[session_id])}"
        )

    async def unsubscribe(self, channel: str, ws: WebSocket):
        """取消订阅通道"""
        ws_id = id(ws)
        if channel in self._channel_subscribers:
            self._channel_subscribers[channel].discard(ws_id)
        # 同时清理所有 per-session 订阅中的这个 WS
        for sid in list(self._session_subscribers.keys()):
            self._session_subscribers[sid].discard(ws_id)
            if not self._session_subscribers[sid]:
                del self._session_subscribers[sid]
        # 清理连接（如果没有其他 channel 引用）
        self._maybe_cleanup_connection(ws_id)
        logger.info(f"WS 客户端已取消订阅 {channel}")

    async def unsubscribe_session(self, session_id: str, ws: WebSocket):
        """取消特定 session 的订阅。"""
        ws_id = id(ws)
        if session_id in self._session_subscribers:
            self._session_subscribers[session_id].discard(ws_id)
            if not self._session_subscribers[session_id]:
                del self._session_subscribers[session_id]
        self._maybe_cleanup_connection(ws_id)

    def _maybe_cleanup_connection(self, ws_id: int):
        """如果 WS 连接不再被任何 channel 引用，取消消费者并清理。"""
        still_referenced = False
        for ch_ids in self._channel_subscribers.values():
            if ws_id in ch_ids:
                still_referenced = True
                break
        if still_referenced:
            return
        for s_ids in self._session_subscribers.values():
            if ws_id in s_ids:
                still_referenced = True
                break
        if still_referenced:
            return

        conn = self._connections.pop(ws_id, None)
        if conn:
            conn.cancel_consumer()

    # ============ 事件广播 ============

    async def emit(self, channel: str, event: dict):
        """向指定通道的所有客户端广播事件（全局广播，非阻塞队列投递）。"""
        event = {**event}
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        self._record_event(event)
        self._update_stats(event)

        message = json.dumps(event, ensure_ascii=False)
        ws_ids = set(self._channel_subscribers.get(channel, set()))
        if not ws_ids:
            return

        self._enqueue_to_connections(ws_ids, message, event.get("type", ""))

    async def emit_chat(self, event: dict):
        """广播到 chat 通道（per-session 订阅模式，非阻塞队列投递）。

        根据 event["session_id"] 只投递到订阅了该 session 的 WS 客户端队列。
        如果没有 per-session 订阅者，降级到全局 chat 通道广播（向后兼容）。
        """
        event = {**event}
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        self._record_event(event)
        self._update_stats(event)

        session_id = event.get("session_id", "")

        # Workflow sub-session 事件精简：只推状态事件，不推 token 流
        if self._is_workflow_sub_session(session_id):
            event_type = event.get("type", "")
            if event_type not in WF_STATUS_EVENTS:
                return  # 丢弃非状态事件

        message = json.dumps(event, ensure_ascii=False)
        event_type = event.get("type", "")

        # Per-session 订阅者优先
        sub_ids = self._session_subscribers.get(session_id)
        if sub_ids:
            self._enqueue_to_connections(set(sub_ids), message, event_type)
            return

        # 降级：全局 chat 通道广播（向后兼容）
        chat_ids = set(self._channel_subscribers.get("chat", set()))
        if chat_ids:
            self._enqueue_to_connections(chat_ids, message, event_type)

    async def emit_event(self, event: dict):
        """快捷方法：广播到 events 通道（全局广播）"""
        await self.emit("events", event)

    def _enqueue_to_connections(
        self, ws_ids: set[int], message: str, event_type: str
    ):
        """将消息投递到指定 WS 连接的队列（非阻塞，永不 await）。

        这是消除 asyncio.gather 死锁的核心：只做 put_nowait，
        不做任何 await/send/gather。队列满时丢弃低优先级事件。
        """
        for ws_id in list(ws_ids):
            conn = self._connections.get(ws_id)
            if conn is None:
                # 连接已清理，从订阅集合中移除
                self._remove_dead_ws(ws_id)
                continue
            if conn.enqueue(message, event_type):
                self._enqueued_events += 1
            else:
                self._dropped_events += 1

    def _remove_dead_ws(self, ws_id: int):
        """从所有订阅集合中移除已断开的 WS 连接。"""
        for ch_ids in self._channel_subscribers.values():
            ch_ids.discard(ws_id)
        for s_ids in self._session_subscribers.values():
            s_ids.discard(ws_id)
        # 清理空 session_subscribers
        for sid in list(self._session_subscribers.keys()):
            if not self._session_subscribers[sid]:
                del self._session_subscribers[sid]

    def _is_workflow_sub_session(self, session_id: str) -> bool:
        """判断 session 是否为 workflow sub-session。

        通过 SessionManager 查询：sub 类型 + 有 workflow_id = workflow sub-session。
        这类 session 的事件推送会精简为仅状态事件，避免 token 洪水。
        """
        try:
            from src.web_server import app
            session_mgr = app.state.session_manager
            session = session_mgr.sessions.get(session_id)
            if session and session.session_type == "sub" and session.workflow_id:
                return True
        except Exception:
            pass
        return False

    # ============ 事件日志 ============

    def _record_event(self, event: dict):
        """记录事件到日志"""
        self._event_log.append({
            **event,
            "_recorded_at": time.time(),
        })
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

    def get_recent_events(
        self, limit: int = 50, event_type: str | None = None
    ) -> list[dict]:
        """获取最近的事件日志"""
        events = self._event_log
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        return events[-limit:]

    # ============ 统计数据 ============

    def _update_stats(self, event: dict):
        """根据事件更新统计数据"""
        event_type = event.get("type", "")
        if event_type == "tool_start":
            name = event.get("name", "unknown")
            self._tool_call_counts[name] = self._tool_call_counts.get(name, 0) + 1
            self._total_tool_calls += 1
        elif event_type == "llm_start":
            self._total_llm_calls += 1
        elif event_type == "llm_usage":
            data = event.get("data", {})
            api = data.get("api", {})
            self._total_prompt_tokens += api.get("prompt_tokens", 0)
            self._total_completion_tokens += api.get("completion_tokens", 0)

    def get_stats(self) -> dict:
        """获取统计数据"""
        # 收集各连接的队列深度
        queue_depths = {
            ws_id: conn.queue.qsize()
            for ws_id, conn in self._connections.items()
        }
        return {
            "total_tool_calls": self._total_tool_calls,
            "total_llm_calls": self._total_llm_calls,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "tool_call_counts": dict(self._tool_call_counts),
            "connected_clients": {
                ch: len(ids) for ch, ids in self._channel_subscribers.items()
            },
            "session_subscriptions": {
                sid: len(ids) for sid, ids in self._session_subscribers.items()
            },
            "dropped_events": self._dropped_events,
            "enqueued_events": self._enqueued_events,
            "event_log_size": len(self._event_log),
            "queue_depths": queue_depths,
            "total_connections": len(self._connections),
        }

    def reset_stats(self):
        """重置统计数据"""
        self._tool_call_counts.clear()
        self._total_tool_calls = 0
        self._total_llm_calls = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._dropped_events = 0
        self._enqueued_events = 0


# 全局单例
event_bus = EventBus()
