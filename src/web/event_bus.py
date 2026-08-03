"""
事件总线 - 管理 WebSocket 客户端连接池，广播系统事件

架构改进（Per-WS 队列 + 独立消费者，消除 asyncio.gather 死锁）：
- 每个 WS 连接分配独立的 asyncio.Queue(maxsize=1024) 和专用消费者协程
- 事件生产者只做 put_nowait（队列满时丢弃增量事件，终止事件进入有序溢出队列）
- 消费者串行消费队列，逐个 send_text，慢就慢但不阻塞生产者
- 完全移除 _broadcast_to_clients 中的 asyncio.gather 嵌套

通道模型：
- chat: per-session 订阅，前端只收自己看的 session 的 token/tool/chain_end 事件
- events: 全局广播，系统级事件（会话状态变更、wf_task_update 等）
"""
import asyncio
from collections import deque
from copy import deepcopy
import json
import logging
import os as _os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


async def _close_ws_safely(ws: WebSocket, code: int) -> None:
    """关闭连接并吸收已关闭/已断开的竞态错误。"""
    try:
        await ws.close(code=code)
    except Exception:
        logger.debug("WS 连接已关闭，无需重复关闭", exc_info=True)

# 每个 WS 连接的事件队列容量（从环境变量读取，默认 1024）
_WS_QUEUE_SIZE = int(_os.getenv("EVENT_QUEUE_SIZE", "1024"))
_WS_OVERFLOW_SIZE = int(_os.getenv("EVENT_OVERFLOW_SIZE", str(_WS_QUEUE_SIZE)))
# WS 发送超时秒数（从环境变量读取，默认 30）
_WS_SEND_TIMEOUT = float(_os.getenv("WS_SEND_TIMEOUT", "30.0"))

# 这些事件决定一次生成已经结束，不能在背压时静默丢弃。
_TERMINAL_EVENT_TYPES = {
    "snapshot",
    "stream_end",
    "chain_end",
    "error",
    "rt_turn_end",
    "roundtable_summary",
    "roundtable_conclusion",
    "rt_ended",
}
_REVISIONED_CHAT_EVENT_TYPES = {
    "stream_start",
    "token",
    "reasoning_token",
    "tool_call_delta",
    "tool_start",
    "tool_end",
    "stream_end",
    "chain_end",
    "error",
    "llm_usage",
}


class _WsConnection:
    """单个 WebSocket 连接的队列 + 消费者管理。"""

    def __init__(self, ws: WebSocket, on_failure=None):
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_WS_QUEUE_SIZE)
        # 队列满时，终止事件进入有序溢出队列。后续事件也进入该队列，
        # 避免 chain_end 被尚未发送的 token 越过或反向越过。
        self._overflow: deque[tuple[str, str]] = deque()
        self._consumer_task: asyncio.Task | None = None
        self._dropped_count = 0
        self._on_failure = on_failure
        self.unhealthy = False

    def start_consumer(self):
        if self._consumer_task and not self._consumer_task.done():
            return
        self._consumer_task = asyncio.create_task(
            self._consume(), name=f"ws-consumer-{id(self.ws)}"
        )

    def cancel_consumer(self):
        if self._consumer_task and not self._consumer_task.done():
            try:
                is_current = self._consumer_task is asyncio.current_task()
            except RuntimeError:
                is_current = False
            if not is_current:
                self._consumer_task.cancel()
        self._consumer_task = None

    def enqueue(self, message: str, event_type: str) -> bool:
        """投递消息到队列。队列满时仅保护终止事件。
        Returns: True 已入队, False 已丢弃
        """
        if self._overflow:
            if len(self._overflow) >= _WS_OVERFLOW_SIZE:
                self._dropped_count += 1
                self.unhealthy = True
                return False
            self._overflow.append((message, event_type))
            return True

        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            if event_type in _TERMINAL_EVENT_TYPES:
                self._overflow.append((message, event_type))
                return True
            self._dropped_count += 1
            # 有序增量丢失后立即重连取快照；若这是最后一个事件，客户端
            # 不会再有机会通过后续 revision 自行发现缺口。
            self.unhealthy = True
            return False

    @property
    def pending_count(self) -> int:
        """等待发送的事件数，包括受保护的终止事件。"""
        return self.queue.qsize() + len(self._overflow)

    async def _consume(self):
        """串行消费队列，逐个发送到 WS 客户端。"""
        try:
            while True:
                from_queue = True
                if self.queue.empty() and self._overflow:
                    message, _event_type = self._overflow.popleft()
                    from_queue = False
                else:
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
                    self.unhealthy = True
                    if self._on_failure:
                        self._on_failure()
                    break
                finally:
                    if from_queue:
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

        # Per-roundtable 订阅：roundtable_id -> set of ws ids
        self._roundtable_subscribers: dict[str, set[int]] = {}

        # 单进程内的会话流快照。revision 用于客户端发现漏包并重新同步；
        # 完整历史仍以 AgentSession.record 为权威来源。
        self._session_revisions: dict[str, int] = {}
        self._active_streams: dict[str, dict[str, Any]] = {}
        self._roundtable_revisions: dict[str, int] = {}

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
            conn = _WsConnection(ws, on_failure=lambda: self._drop_connection(ws_id))
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
            conn = _WsConnection(ws, on_failure=lambda: self._drop_connection(ws_id))
            conn.start_consumer()
            self._connections[ws_id] = conn
        if session_id not in self._session_subscribers:
            self._session_subscribers[session_id] = set()
        self._session_subscribers[session_id].add(ws_id)
        logger.debug(
            f"WS 订阅 session {session_id}，订阅者数: "
            f"{len(self._session_subscribers[session_id])}"
        )
        self.start_periodic_stats()

    async def subscribe_roundtable(self, roundtable_id: str, ws: WebSocket):
        """订阅单个圆桌；空 ID 只注册控制连接，不接收圆桌增量。"""
        ws_id = id(ws)
        if ws_id not in self._connections:
            conn = _WsConnection(ws, on_failure=lambda: self._drop_connection(ws_id))
            conn.start_consumer()
            self._connections[ws_id] = conn
        self._roundtable_subscribers.setdefault(roundtable_id, set()).add(ws_id)
        self.start_periodic_stats()

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
        for roundtable_id in list(self._roundtable_subscribers.keys()):
            self._roundtable_subscribers[roundtable_id].discard(ws_id)
            if not self._roundtable_subscribers[roundtable_id]:
                del self._roundtable_subscribers[roundtable_id]
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
        for roundtable_ids in self._roundtable_subscribers.values():
            if ws_id in roundtable_ids:
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

        带 session_id 的事件同时投递给该 session 的订阅者和全局 chat
        观察者。两组订阅使用集合合并，同一连接不会收到重复事件。
        """
        event = {**event}
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        self._apply_roundtable_revision(event)
        self._apply_chat_event(event)
        self._record_event(event)
        self._update_stats(event)

        session_id = event.get("session_id", "")
        message = json.dumps(event, ensure_ascii=False)
        event_type = event.get("type", "")

        recipient_ids = set(self._channel_subscribers.get("chat", set()))
        if session_id:
            recipient_ids.update(self._session_subscribers.get(session_id, set()))
        roundtable_id = str(event.get("roundtable_id") or "")
        if roundtable_id:
            recipient_ids.update(self._roundtable_subscribers.get(roundtable_id, set()))
        if recipient_ids:
            self._enqueue_to_connections(recipient_ids, message, event_type)

    def enqueue_to_ws(self, ws: WebSocket, event: dict) -> bool:
        """将单个控制事件放入连接队列，保持与实时事件的发送顺序。"""
        payload = {**event}
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        conn = self._connections.get(id(ws))
        if conn is None:
            return False
        message = json.dumps(payload, ensure_ascii=False)
        if conn.enqueue(message, payload.get("type", "")):
            self._enqueued_events += 1
            return True
        self._dropped_events += 1
        if conn.unhealthy:
            self._drop_connection(id(ws))
        return False

    def get_session_revision(self, session_id: str) -> int:
        """返回会话在当前进程内最后应用的流事件 revision。"""
        return self._session_revisions.get(session_id, 0)

    def get_active_stream(self, session_id: str) -> dict[str, Any] | None:
        """返回可直接下发给客户端的活动生成草稿。"""
        stream = self._active_streams.get(session_id)
        if stream is None:
            return None
        return {
            "generation_id": stream["generation_id"],
            "revision": stream["revision"],
            "baseline_record_length": stream.get("baseline_record_length"),
            "segments": deepcopy(stream["segments"]),
        }

    def clear_session(self, session_id: str) -> None:
        """清除已终止或删除会话的进程内流恢复状态。"""
        self._active_streams.pop(session_id, None)
        self._session_revisions.pop(session_id, None)

    def get_roundtable_revision(self, roundtable_id: str) -> int:
        """返回圆桌在当前进程内最后广播的有序事件 revision。"""
        return self._roundtable_revisions.get(roundtable_id, 0)

    def clear_roundtable(self, roundtable_id: str) -> None:
        """删除圆桌时清理其进程内事件水位。"""
        self._roundtable_revisions.pop(roundtable_id, None)

    def _apply_roundtable_revision(self, event: dict[str, Any]) -> None:
        """为圆桌事件分配独立水位，供 REST 快照与 WS 增量对账。"""
        roundtable_id = str(event.get("roundtable_id") or "")
        if not roundtable_id:
            return
        revision = self._roundtable_revisions.get(roundtable_id, 0) + 1
        self._roundtable_revisions[roundtable_id] = revision
        event["roundtable_revision"] = revision

    def _apply_chat_event(self, event: dict[str, Any]) -> None:
        """给事件分配 revision，并维护可供中途加入者恢复的活动草稿。"""
        session_id = str(event.get("session_id") or "")
        event_type = event.get("type", "")
        if (
            not session_id
            or event_type not in _REVISIONED_CHAT_EVENT_TYPES
            or (event_type == "error" and event.get("terminal") is False)
        ):
            return

        revision = self._session_revisions.get(session_id, 0) + 1
        self._session_revisions[session_id] = revision
        event["revision"] = revision
        if event_type == "stream_start":
            generation_id = str(event.get("generation_id") or uuid.uuid4().hex)
            stream = {
                "generation_id": generation_id,
                "revision": revision,
                "baseline_record_length": event.get("baseline_record_length"),
                "segments": [],
                "tool_indices": {},
            }
            self._active_streams[session_id] = stream
            event["generation_id"] = generation_id
            return

        stream = self._active_streams.get(session_id)
        if stream is not None:
            stream["revision"] = revision
            event["generation_id"] = stream["generation_id"]
            self._update_stream_segments(stream, event)

        if event_type == "chain_end" or (
            event_type == "error" and event.get("terminal") is not False
        ):
            self._active_streams.pop(session_id, None)

    @staticmethod
    def _update_stream_segments(stream: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = event.get("type", "")
        segments: list[dict[str, Any]] = stream["segments"]

        if event_type in {"token", "reasoning_token"}:
            segment_type = "text" if event_type == "token" else "reasoning"
            content = str(event.get("content") or "")
            if not content:
                return
            if segments and segments[-1].get("type") == segment_type:
                segments[-1]["content"] += content
            else:
                segments.append({"type": segment_type, "content": content})
            return

        tool_indices: dict[int, int] = stream["tool_indices"]
        if event_type == "tool_call_delta":
            index = int(event.get("index", 0))
            segment_index = tool_indices.get(index)
            if segment_index is not None:
                previous_tool = segments[segment_index]["tool"]
                incoming_id = event.get("id")
                is_new_tool = previous_tool.get("status") != "building" or (
                    incoming_id
                    and previous_tool.get("id")
                    and incoming_id != previous_tool["id"]
                )
                if is_new_tool:
                    segment_index = None
            if segment_index is None:
                tool_id = str(event.get("id") or f"delta_{index}")
                segments.append({
                    "type": "tool",
                    "tool": {
                        "id": event.get("id"),
                        "run_id": tool_id,
                        "index": index,
                        "name": str(event.get("name") or ""),
                        "args": str(event.get("args_delta") or ""),
                        "status": "building",
                    },
                })
                tool_indices[index] = len(segments) - 1
                return

            tool = segments[segment_index]["tool"]
            if event.get("id"):
                tool["id"] = event["id"]
                tool["run_id"] = event["id"]
            if event.get("name"):
                tool["name"] = event["name"]
            tool["args"] += str(event.get("args_delta") or "")
            return

        if event_type == "tool_start":
            index = int(event.get("index", -1))
            segment_index = tool_indices.get(index)
            args = event.get("args", {})
            args_text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            tool = {
                "run_id": str(event.get("run_id") or f"tool_{index}"),
                "index": index,
                "name": str(event.get("name") or ""),
                "args": args_text,
                "status": "running",
            }
            if segment_index is None:
                segments.append({"type": "tool", "tool": tool})
                tool_indices[index] = len(segments) - 1
            else:
                existing_id = segments[segment_index]["tool"].get("id")
                if existing_id:
                    tool["id"] = existing_id
                segments[segment_index] = {"type": "tool", "tool": tool}
            return

        if event_type == "tool_end":
            run_id = str(event.get("run_id") or "")
            for segment in reversed(segments):
                if segment.get("type") != "tool":
                    continue
                tool = segment["tool"]
                if tool.get("run_id") == run_id:
                    tool["result"] = str(event.get("result") or "")
                    raw_status = str(event.get("status") or "completed").lower()
                    tool["status"] = (
                        "failed"
                        if raw_status in {"error", "failed", "failure"}
                        else "cancelled"
                        if raw_status in {"cancelled", "canceled", "aborted"}
                        else "completed"
                    )
                    break

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
                if conn.unhealthy:
                    self._drop_connection(ws_id)

    def _remove_dead_ws(self, ws_id: int):
        """从所有订阅集合中移除已断开的 WS 连接。"""
        for ch_ids in self._channel_subscribers.values():
            ch_ids.discard(ws_id)
        for s_ids in self._session_subscribers.values():
            s_ids.discard(ws_id)
        for roundtable_ids in self._roundtable_subscribers.values():
            roundtable_ids.discard(ws_id)
        # 清理空 session_subscribers
        for sid in list(self._session_subscribers.keys()):
            if not self._session_subscribers[sid]:
                del self._session_subscribers[sid]
        for roundtable_id in list(self._roundtable_subscribers.keys()):
            if not self._roundtable_subscribers[roundtable_id]:
                del self._roundtable_subscribers[roundtable_id]

    def _drop_connection(self, ws_id: int) -> None:
        """移除无法继续有序发送的连接，并促使客户端走重连快照。"""
        conn = self._connections.pop(ws_id, None)
        self._remove_dead_ws(ws_id)
        if conn is None:
            return
        conn.cancel_consumer()
        try:
            asyncio.create_task(_close_ws_safely(conn.ws, code=1013))
        except (RuntimeError, AttributeError):
            pass

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
            ws_id: conn.pending_count
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
            "roundtable_subscriptions": {
                rid: len(ids) for rid, ids in self._roundtable_subscribers.items() if rid
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
