"""
圆桌会议 (Roundtable) 核心数据模型

Phase 1: 席位、固定轮询调度器、讨论记录、会话容器
Phase 2: 可插拔调度策略、Moderator 抽象、上下文压缩、共享记忆
Phase 3: 用户干预队列、动态增减席位、暂停/恢复、结构化结论

与现有 AgentSession 完全独立，不继承不复用。
"""
import asyncio
import os
import uuid
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.config import SESSIONS_DIR

logger = logging.getLogger("roundtable")


# ============================================================
# Seat - 席位配置与状态
# ============================================================

@dataclass
class Seat:
    """
    圆桌会议中的一个席位。

    包含角色配置（名称、system prompt、temperature 等）
    和运行时状态（idle / speaking / thinking / done）。
    """
    seat_id: str
    role_name: str
    system_prompt: str
    temperature: float = 0.7
    model_name: str | None = None
    allowed_tools: list[str] | None = None
    is_moderator: bool = False
    status: str = "idle"  # "idle" | "speaking" | "thinking" | "done"

    def to_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "role_name": self.role_name,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "model_name": self.model_name,
            "allowed_tools": self.allowed_tools,
            "is_moderator": self.is_moderator,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Seat":
        return cls(
            seat_id=data.get("seat_id", uuid.uuid4().hex[:6]),
            role_name=data.get("role_name", "未命名角色"),
            system_prompt=data.get("system_prompt", ""),
            temperature=data.get("temperature", 0.7),
            model_name=data.get("model_name"),
            allowed_tools=data.get("allowed_tools"),
            is_moderator=data.get("is_moderator", False),
            status=data.get("status", "idle"),
        )


# ============================================================
# TranscriptEntry - 单条讨论记录
# ============================================================

@dataclass
class TranscriptEntry:
    """
    共享讨论记录中的一条发言。

    所有 Seat 的发言按时间顺序追加到 Transcript 列表中，
    构成圆桌会议的完整讨论历史。

    entry_type:
    - "statement": 普通发言
    - "moderator_note": 主持人笔记/决策
    - "summary": 阶段摘要（Phase 2 新增）
    - "conclusion": 会议结论（Phase 2 新增）
    """
    speaker_seat_id: str
    speaker_name: str
    content: str
    round_number: int
    timestamp: str = ""
    entry_type: str = "statement"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "speaker_seat_id": self.speaker_seat_id,
            "speaker_name": self.speaker_name,
            "content": self.content,
            "round_number": self.round_number,
            "timestamp": self.timestamp,
            "entry_type": self.entry_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptEntry":
        return cls(
            speaker_seat_id=data.get("speaker_seat_id", ""),
            speaker_name=data.get("speaker_name", ""),
            content=data.get("content", ""),
            round_number=data.get("round_number", 0),
            timestamp=data.get("timestamp", ""),
            entry_type=data.get("entry_type", "statement"),
        )


# ============================================================
# SharedMemory - 共享记忆作用域 (Phase 2)
# ============================================================

class SharedMemory:
    """
    圆桌会议的共享记忆空间。

    所有 Seat 可读取，用于存放：
    - 阶段结论 (conclusions)
    - 讨论共识 (consensus)
    - 关键争议点 (controversies)
    - 阶段摘要 (summaries)
    """

    def __init__(self):
        self.conclusions: list[dict] = []
        self.consensus: list[str] = []
        self.controversies: list[str] = []
        self.summaries: list[dict] = []  # {"round": int, "content": str}
        self.structured_conclusion: dict | None = None  # Phase 3: 结构化结论

    def add_conclusion(self, content: str, source: str = "moderator") -> None:
        self.conclusions.append({
            "content": content,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def add_consensus(self, point: str) -> None:
        if point not in self.consensus:
            self.consensus.append(point)

    def add_controversy(self, point: str) -> None:
        if point not in self.controversies:
            self.controversies.append(point)

    def add_summary(self, round_number: int, content: str) -> None:
        self.summaries.append({
            "round": round_number,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_context_text(self) -> str:
        """生成可注入 LLM 上下文的文本"""
        parts = []
        if self.consensus:
            parts.append("**已达成共识**:\n" + "\n".join(f"- {c}" for c in self.consensus))
        if self.controversies:
            parts.append("**关键争议**:\n" + "\n".join(f"- {c}" for c in self.controversies))
        if self.summaries:
            for s in self.summaries:
                parts.append(f"**第 {s['round']} 轮摘要**: {s['content']}")
        if self.conclusions:
            for c in self.conclusions:
                parts.append(f"**结论** ({c['source']}): {c['content']}")
        # Phase 3: 结构化结论
        if self.structured_conclusion:
            sc = self.structured_conclusion
            if sc.get("pending_verification"):
                parts.append("**待验证事项**:\n" + "\n".join(f"- {v}" for v in sc["pending_verification"]))
            if sc.get("action_items"):
                parts.append("**行动项**:\n" + "\n".join(f"- {a}" for a in sc["action_items"]))
        return "\n\n".join(parts) if parts else ""

    def set_structured_conclusion(self, conclusion: dict) -> None:
        """设置结构化结论（Phase 3）"""
        self.structured_conclusion = conclusion
        # 同步更新 consensus 和 controversies
        if conclusion.get("consensus"):
            for c in conclusion["consensus"]:
                self.add_consensus(c)
        if conclusion.get("disagreements"):
            for d in conclusion["disagreements"]:
                self.add_controversy(d)

    def to_dict(self) -> dict:
        result = {
            "conclusions": self.conclusions,
            "consensus": self.consensus,
            "controversies": self.controversies,
            "summaries": self.summaries,
        }
        if self.structured_conclusion:
            result["structured_conclusion"] = self.structured_conclusion
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SharedMemory":
        mem = cls()
        mem.conclusions = data.get("conclusions", [])
        mem.consensus = data.get("consensus", [])
        mem.controversies = data.get("controversies", [])
        mem.summaries = data.get("summaries", [])
        mem.structured_conclusion = data.get("structured_conclusion")
        return mem


# ============================================================
# InterventionQueue - 用户干预队列 (Phase 3)
# ============================================================

@dataclass
class Intervention:
    """
    用户干预事件。

    统一事件模型：所有用户对圆桌会议的操作都通过此结构传递。

    intervention_type:
    - "inject": 用户插话（追加到 transcript）
    - "nominate": 点名某个 seat 发言 (@架构师)
    - "add_seat": 动态添加席位
    - "remove_seat": 动态移除席位
    - "pause": 暂停会议
    - "resume": 恢复会议
    - "end": 提前结束会议
    """
    intervention_type: str
    content: str = ""
    target_seat_id: str | None = None
    seat_config: dict | None = None  # 用于 add_seat
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class InterventionQueue:
    """
    线程安全的用户干预队列。

    Runner 在每轮调度间隙检查此队列，处理用户的插话、点名等干预。
    使用 asyncio.Queue 确保并发安全。
    """

    def __init__(self):
        self._queue: asyncio.Queue[Intervention] = asyncio.Queue()

    async def put(self, intervention: Intervention) -> None:
        """放入一个干预事件"""
        await self._queue.put(intervention)

    async def get(self) -> Intervention:
        """获取一个干预事件（阻塞）"""
        return await self._queue.get()

    def get_nowait(self) -> Intervention | None:
        """非阻塞获取"""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def drain_all(self) -> list[Intervention]:
        """非阻塞获取所有待处理的干预事件"""
        items = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    @property
    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def size(self) -> int:
        return self._queue.qsize()


# ============================================================
# TranscriptCompressor - 上下文压缩配置 (Phase 2)
# ============================================================

class TranscriptCompressor:
    """
    管理 Transcript 的滑动窗口和阶段摘要。

    当 transcript 超过 window_size 条时，较早的发言将被
    替换为摘要内容，减少 LLM 上下文窗口负担。
    """

    def __init__(
        self,
        enabled: bool = False,
        window_size: int = 20,
        summary_interval: int = 0,
    ):
        self.enabled = enabled
        self.window_size = window_size  # 保留最近 N 条原始记录
        self.summary_interval = summary_interval  # 每 N 轮生成摘要（0=仅窗口裁剪）

    def get_context_entries(
        self,
        transcript: list[TranscriptEntry],
        shared_memory: "SharedMemory",
    ) -> tuple[list[TranscriptEntry], str]:
        """
        获取用于 LLM 上下文的 transcript 子集。

        Returns:
            (recent_entries, summary_prefix)
            - recent_entries: 窗口内的最近记录
            - summary_prefix: 窗口外记录的摘要文本（来自 shared_memory）
        """
        if not self.enabled or len(transcript) <= self.window_size:
            return transcript, ""

        # 窗口内的最近记录
        recent = transcript[-self.window_size:]

        # 构建摘要前缀（来自 shared_memory 的阶段摘要）
        summary_text = ""
        if shared_memory.summaries:
            summary_parts = []
            for s in shared_memory.summaries:
                summary_parts.append(f"[第 {s['round']} 轮摘要] {s['content']}")
            summary_text = "\n".join(summary_parts)

        return recent, summary_text

    def should_summarize(self, current_round: int) -> bool:
        """判断是否应该在当前轮次结束时生成摘要"""
        if not self.enabled or self.summary_interval <= 0:
            return False
        return current_round > 0 and current_round % self.summary_interval == 0

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "window_size": self.window_size,
            "summary_interval": self.summary_interval,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptCompressor":
        return cls(
            enabled=data.get("enabled", False),
            window_size=data.get("window_size", 20),
            summary_interval=data.get("summary_interval", 0),
        )


# ============================================================
# SpeakerStrategy - 可插拔调度策略 (Phase 2)
# ============================================================

class SpeakerStrategy(ABC):
    """
    发言调度策略的抽象基类。

    Phase 1 的 TurnController 硬编码了 round_robin 逻辑，
    Phase 2 将其抽象为可插拔策略，支持多种调度方式。
    """

    @abstractmethod
    def next_speaker(
        self,
        seats: list[Seat],
        transcript: list[TranscriptEntry],
        current_round: int,
        max_rounds: int,
        **kwargs,
    ) -> Seat | None:
        """
        选择下一个发言者。

        Returns:
            下一个应该发言的 Seat，如果讨论应结束则返回 None
        """
        ...

    @abstractmethod
    def advance(self) -> None:
        """标记当前发言者已完成发言"""
        ...

    @abstractmethod
    def should_end(self) -> bool:
        """判断讨论是否应结束"""
        ...

    @abstractmethod
    def is_round_end(self) -> bool:
        """判断当前轮次是否结束"""
        ...

    @abstractmethod
    def get_current_round(self) -> int:
        """获取当前轮次"""
        ...

    def get_current_speaker(self, seats: list[Seat]) -> Seat | None:
        """返回当前正在发言（或即将发言）的 Seat，无则返回 None。

        默认实现返回 None；各策略子类覆盖此方法。
        """
        return None

    @abstractmethod
    def to_dict(self) -> dict:
        """序列化"""
        ...

    @property
    def strategy_name(self) -> str:
        """策略名称标识"""
        return "base"


class RoundRobinStrategy(SpeakerStrategy):
    """
    轮询策略（Phase 1 的 TurnController 逻辑提取）。

    按固定顺序循环，每个 Seat 发言一次为一轮。
    达到 max_rounds 自动结束。

    完全兼容 Phase 1 行为。
    """

    def __init__(self, seat_count: int, max_rounds: int = 3):
        self._seat_count = seat_count
        self._max_rounds = max_rounds
        self._current_round: int = 1
        self._speaker_index: int = 0
        self._finished: bool = False

    def next_speaker(
        self,
        seats: list[Seat],
        transcript: list[TranscriptEntry],
        current_round: int,
        max_rounds: int,
        **kwargs,
    ) -> Seat | None:
        if self._finished:
            return None
        if not seats:
            self._finished = True
            return None

        if self._speaker_index >= len(seats):
            if self._current_round >= self._max_rounds:
                self._finished = True
                return None
            self._current_round += 1
            self._speaker_index = 0

        return seats[self._speaker_index]

    def advance(self) -> None:
        self._speaker_index += 1

    def should_end(self) -> bool:
        return self._finished

    def is_round_end(self) -> bool:
        return self._speaker_index >= self._seat_count

    def get_current_round(self) -> int:
        return self._current_round

    def get_current_speaker(self, seats: list[Seat]) -> Seat | None:
        if self._finished or not seats:
            return None
        if self._speaker_index < len(seats):
            return seats[self._speaker_index]
        return None

    @property
    def strategy_name(self) -> str:
        return "round_robin"

    def to_dict(self) -> dict:
        return {
            "strategy": "round_robin",
            "max_rounds": self._max_rounds,
            "current_round": self._current_round,
            "speaker_index": self._speaker_index,
            "finished": self._finished,
        }

    @classmethod
    def from_dict(cls, data: dict, seat_count: int) -> "RoundRobinStrategy":
        s = cls(seat_count=seat_count, max_rounds=data.get("max_rounds", 3))
        s._current_round = data.get("current_round", 1)
        s._speaker_index = data.get("speaker_index", 0)
        s._finished = data.get("finished", False)
        return s


class ModeratorDecidesStrategy(SpeakerStrategy):
    """
    Moderator 决策策略（Phase 2 新增）。

    Moderator（LLM 驱动）决定下一位发言者、是否结束讨论、是否需要总结。
    该策略本身只维护状态，实际 LLM 调用由 RoundtableRunner 驱动。

    工作流程：
    1. Runner 调用 next_speaker()，如果有 pending 决策则返回对应 Seat
    2. 如果没有 pending 决策，返回 None 并标记 needs_moderator_decision=True
    3. Runner 检测到 needs_moderator_decision，调用 Moderator LLM 做决策
    4. Runner 将决策结果写入 set_decision()
    5. Runner 再次调用 next_speaker() 获取决策出的 Seat
    """

    def __init__(self, seat_count: int, max_rounds: int = 3):
        self._seat_count = seat_count
        self._max_rounds = max_rounds
        self._current_round: int = 1
        self._speakers_this_round: list[str] = []  # 本轮已发言的 seat_id
        self._finished: bool = False

        # Moderator 决策状态
        self._pending_speaker_id: str | None = None
        self._current_speaker_id: str | None = None  # 当前正在发言的 seat_id
        self.needs_moderator_decision: bool = True
        self._should_conclude: bool = False

    def next_speaker(
        self,
        seats: list[Seat],
        transcript: list[TranscriptEntry],
        current_round: int,
        max_rounds: int,
        **kwargs,
    ) -> Seat | None:
        if self._finished or self._should_conclude:
            return None
        if not seats:
            self._finished = True
            return None

        # 如果有 pending 决策，返回对应 Seat
        if self._pending_speaker_id:
            for seat in seats:
                if seat.seat_id == self._pending_speaker_id:
                    self._current_speaker_id = self._pending_speaker_id
                    self._pending_speaker_id = None
                    return seat
            # 未找到对应 Seat，清除 pending
            self._pending_speaker_id = None

        # 需要 Moderator 做决策
        self.needs_moderator_decision = True
        return None

    def set_decision(
        self,
        speaker_id: str | None = None,
        should_conclude: bool = False,
        new_round: bool = False,
    ) -> None:
        """
        接收 Moderator 的决策结果。

        Args:
            speaker_id: 下一位发言者的 seat_id
            should_conclude: 是否结束讨论
            new_round: 是否开始新一轮
        """
        self.needs_moderator_decision = False

        if should_conclude:
            self._should_conclude = True
            self._finished = True
            return

        if new_round:
            if self._current_round >= self._max_rounds:
                self._finished = True
                return
            self._current_round += 1
            self._speakers_this_round = []

        if speaker_id:
            self._pending_speaker_id = speaker_id

    def advance(self) -> None:
        # 记录本轮已发言者
        if self._current_speaker_id is not None:
            self._speakers_this_round.append(self._current_speaker_id)
            self._current_speaker_id = None
        self.needs_moderator_decision = True

    def should_end(self) -> bool:
        return self._finished

    def is_round_end(self) -> bool:
        return False  # Moderator 策略不基于固定轮次

    def get_current_round(self) -> int:
        return self._current_round

    def get_current_speaker(self, seats: list[Seat]) -> Seat | None:
        if self._current_speaker_id is None:
            return None
        for seat in seats:
            if seat.seat_id == self._current_speaker_id:
                return seat
        return None

    @property
    def strategy_name(self) -> str:
        return "moderator_decides"

    def to_dict(self) -> dict:
        return {
            "strategy": "moderator_decides",
            "max_rounds": self._max_rounds,
            "current_round": self._current_round,
            "speakers_this_round": self._speakers_this_round,
            "finished": self._finished,
            "pending_speaker_id": self._pending_speaker_id,
            "current_speaker_id": self._current_speaker_id,
            "should_conclude": self._should_conclude,
        }

    @classmethod
    def from_dict(cls, data: dict, seat_count: int) -> "ModeratorDecidesStrategy":
        s = cls(seat_count=seat_count, max_rounds=data.get("max_rounds", 3))
        s._current_round = data.get("current_round", 1)
        s._speakers_this_round = data.get("speakers_this_round", [])
        s._finished = data.get("finished", False)
        s._pending_speaker_id = data.get("pending_speaker_id")
        s._current_speaker_id = data.get("current_speaker_id")
        s._should_conclude = data.get("should_conclude", False)
        return s


# ============================================================
# 策略工厂
# ============================================================

def create_strategy(
    strategy_name: str,
    seat_count: int,
    max_rounds: int = 3,
) -> SpeakerStrategy:
    """
    策略工厂：根据名称创建对应的调度策略。

    Args:
        strategy_name: 策略名称 ("round_robin" | "moderator_decides")
        seat_count: 席位总数
        max_rounds: 最大轮次
    """
    strategies = {
        "round_robin": lambda: RoundRobinStrategy(seat_count, max_rounds),
        "moderator_decides": lambda: ModeratorDecidesStrategy(seat_count, max_rounds),
    }

    factory = strategies.get(strategy_name)
    if not factory:
        logger.warning(f"未知策略 {strategy_name}，回退到 round_robin")
        return RoundRobinStrategy(seat_count, max_rounds)

    return factory()


def restore_strategy(data: dict, seat_count: int) -> SpeakerStrategy:
    """从序列化数据恢复策略实例"""
    strategy_name = data.get("strategy", "round_robin")
    if strategy_name == "moderator_decides":
        return ModeratorDecidesStrategy.from_dict(data, seat_count)
    return RoundRobinStrategy.from_dict(data, seat_count)


# ============================================================
# TurnController - 调度器（Phase 2 重构为策略代理）
# ============================================================

class TurnController:
    """
    发言调度控制器。

    Phase 1: 内置固定轮询逻辑
    Phase 2: 代理到可插拔的 SpeakerStrategy

    保持 Phase 1 的对外接口完全不变，确保向后兼容。
    """

    def __init__(self, seats: list[Seat], max_rounds: int = 3, strategy: str = "round_robin"):
        self.seats = seats
        self.max_rounds = max_rounds
        self.strategy_name = strategy
        self._strategy: SpeakerStrategy = create_strategy(strategy, len(seats), max_rounds)

    @property
    def current_round(self) -> int:
        return self._strategy.get_current_round()

    @current_round.setter
    def current_round(self, value: int):
        # 向后兼容 Phase 1 的 from_dict 赋值
        if isinstance(self._strategy, RoundRobinStrategy):
            self._strategy._current_round = value

    @property
    def current_speaker(self) -> Seat | None:
        """当前正在发言（或即将发言）的 Seat"""
        if self._strategy.should_end() or not self.seats:
            return None
        return self._strategy.get_current_speaker(self.seats)

    def next_speaker(self) -> Seat | None:
        return self._strategy.next_speaker(
            seats=self.seats,
            transcript=[],  # Runner 会单独管理
            current_round=self.current_round,
            max_rounds=self.max_rounds,
        )

    def advance(self) -> None:
        self._strategy.advance()

    def should_end(self) -> bool:
        return self._strategy.should_end()

    def is_round_end(self) -> bool:
        return self._strategy.is_round_end()

    @property
    def strategy(self) -> SpeakerStrategy:
        """暴露底层策略供 Runner 使用"""
        return self._strategy

    def to_dict(self) -> dict:
        return self._strategy.to_dict()

    @classmethod
    def from_dict(cls, data: dict, seats: list[Seat]) -> "TurnController":
        strategy_name = data.get("strategy", "round_robin")
        tc = cls(seats=seats, max_rounds=data.get("max_rounds", 3), strategy=strategy_name)
        tc._strategy = restore_strategy(data, len(seats))
        return tc


# ============================================================
# RoundtableSession - 圆桌会议完整会话
# ============================================================

class RoundtableSession:
    """
    圆桌会议的完整会话容器。

    包含所有席位配置、共享讨论记录、调度器状态，
    以及会议元数据（主题、状态、时间戳等）。

    Phase 2 新增：
    - strategy: 调度策略名称
    - shared_memory: 共享记忆空间
    - compressor: 上下文压缩配置

    Phase 3 新增：
    - intervention_queue: 用户干预队列
    - _lock: 并发锁
    - _pause_event: 暂停/恢复信号
    - add_seat / remove_seat: 动态增减席位
    """

    def __init__(
        self,
        topic: str,
        seats: list[Seat],
        max_rounds: int = 3,
        session_id: str | None = None,
        strategy: str = "round_robin",
        compressor_config: dict | None = None,
    ):
        self.session_id: str = session_id or f"rt-{uuid.uuid4().hex[:8]}"
        self.topic: str = topic
        self.status: str = "waiting"  # "waiting" | "discussing" | "paused" | "ended"
        self.seats: list[Seat] = seats
        self.transcript: list[TranscriptEntry] = []
        self.turn_controller: TurnController = TurnController(seats, max_rounds, strategy)
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.ended_at: str | None = None
        # 仅保存在当前进程，用于生成中途打开页面时恢复尚未提交的发言。
        # 进程重启后正在执行的模型调用不会继续，因此不从磁盘恢复该草稿。
        self.active_turn: dict | None = None

        # Phase 2 新增
        self.shared_memory: SharedMemory = SharedMemory()
        self.compressor: TranscriptCompressor = TranscriptCompressor(
            **(compressor_config or {})
        )

        # Phase 3 新增（运行时对象，不序列化）
        self.intervention_queue: InterventionQueue = InterventionQueue()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # 初始非暂停状态

    @property
    def strategy_name(self) -> str:
        return self.turn_controller.strategy_name

    @property
    def current_round(self) -> int:
        return self.turn_controller.current_round

    @property
    def max_rounds(self) -> int:
        return self.turn_controller.max_rounds

    @property
    def current_speaker(self) -> Seat | None:
        return self.turn_controller.current_speaker

    def get_moderator_seat(self) -> Seat | None:
        """获取主持人席位"""
        for seat in self.seats:
            if seat.is_moderator:
                return seat
        return None

    def get_seat(self, seat_id: str) -> Seat | None:
        """按 ID 获取席位"""
        for seat in self.seats:
            if seat.seat_id == seat_id:
                return seat
        return None

    def get_seat_by_name(self, role_name: str) -> Seat | None:
        """按角色名获取席位（支持模糊匹配）"""
        # 精确匹配
        for seat in self.seats:
            if seat.role_name == role_name:
                return seat
        # 包含匹配
        role_lower = role_name.lower()
        for seat in self.seats:
            if role_lower in seat.role_name.lower() or seat.role_name.lower() in role_lower:
                return seat
        return None

    def begin_active_turn(self, seat: Seat, round_number: int) -> None:
        """记录当前正在生成的可见发言。"""
        self.active_turn = {
            "seat_id": seat.seat_id,
            "speaker_name": seat.role_name,
            "content": "",
            "round": round_number,
        }

    def append_active_turn(self, content: str) -> None:
        """把模型增量追加到当前发言草稿。"""
        if self.active_turn is not None:
            self.active_turn["content"] += content

    def end_active_turn(self) -> None:
        """最终发言进入 transcript 后清除临时草稿。"""
        self.active_turn = None

    def add_seat(self, seat_config: dict) -> Seat:
        """
        动态添加席位（Phase 3）。

        同步更新 TurnController 的 seats 和策略的 seat_count。
        """
        # 计算下一个可用的 seat 编号，避免移除后重新添加产生重复 ID
        max_num = -1
        for s in self.seats:
            if s.seat_id.startswith("seat-"):
                try:
                    max_num = max(max_num, int(s.seat_id[5:]))
                except ValueError:
                    pass
        next_num = max_num + 1

        seat = Seat(
            seat_id=seat_config.get("seat_id", f"seat-{next_num}"),
            role_name=seat_config.get("role_name", f"角色{next_num + 1}"),
            system_prompt=seat_config.get("system_prompt", ""),
            temperature=seat_config.get("temperature", 0.7),
            model_name=seat_config.get("model_name"),
            allowed_tools=seat_config.get("allowed_tools"),
            is_moderator=seat_config.get("is_moderator", False),
        )
        self.seats.append(seat)

        # 同步更新 TurnController
        self.turn_controller.seats = self.seats
        strategy = self.turn_controller.strategy
        if isinstance(strategy, RoundRobinStrategy):
            strategy._seat_count = len(self.seats)
        elif isinstance(strategy, ModeratorDecidesStrategy):
            strategy._seat_count = len(self.seats)

        logger.info(f"Roundtable {self.session_id} 添加席位: {seat.role_name} ({seat.seat_id})")
        return seat

    def remove_seat(self, seat_id: str) -> Seat | None:
        """
        动态移除席位（Phase 3）。

        不允许移除正在发言的席位。
        同步更新 TurnController 的 seats 和策略的 seat_count。
        """
        target = None
        for seat in self.seats:
            if seat.seat_id == seat_id:
                target = seat
                break

        if not target:
            return None

        if target.status == "speaking":
            logger.warning(f"不能移除正在发言的席位: {target.role_name}")
            return None

        # 记录被移除 seat 在列表中的索引，用于后续调整 speaker_index
        removed_index = next(
            (i for i, s in enumerate(self.seats) if s.seat_id == seat_id), -1
        )

        self.seats = [s for s in self.seats if s.seat_id != seat_id]

        # 同步更新 TurnController
        self.turn_controller.seats = self.seats
        strategy = self.turn_controller.strategy
        if isinstance(strategy, RoundRobinStrategy):
            strategy._seat_count = len(self.seats)
            # 调整 speaker_index 防止越界或跳过
            if removed_index >= 0 and strategy._speaker_index > 0:
                if removed_index < strategy._speaker_index:
                    strategy._speaker_index -= 1
                elif removed_index == strategy._speaker_index:
                    # 移除当前索引指向的 seat，clamp 到合法范围
                    strategy._speaker_index = min(
                        strategy._speaker_index, max(0, len(self.seats) - 1)
                    )
        elif isinstance(strategy, ModeratorDecidesStrategy):
            strategy._seat_count = len(self.seats)

        logger.info(f"Roundtable {self.session_id} 移除席位: {target.role_name} ({seat_id})")
        return target

    def pause(self) -> bool:
        """暂停会议（Phase 3）"""
        if self.status != "discussing":
            return False
        self.status = "paused"
        self._pause_event.clear()
        logger.info(f"Roundtable {self.session_id} 已暂停")
        return True

    def resume(self) -> bool:
        """恢复会议（Phase 3）"""
        if self.status != "paused":
            return False
        self.status = "discussing"
        self._pause_event.set()
        logger.info(f"Roundtable {self.session_id} 已恢复")
        return True

    def get_summary(self) -> dict:
        """返回会话摘要（用于列表展示）"""
        speaker = self.current_speaker
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "status": self.status,
            "seat_count": len(self.seats),
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "current_speaker": speaker.role_name if speaker else None,
            "transcript_count": len(self.transcript),
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "strategy": self.strategy_name,
        }

    # ============ 序列化 ============

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_type": "roundtable",
            "topic": self.topic,
            "status": self.status,
            "seat_count": len(self.seats),
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "transcript_count": len(self.transcript),
            "seats": [s.to_dict() for s in self.seats],
            "transcript": [t.to_dict() for t in self.transcript],
            "active_turn": dict(self.active_turn) if self.active_turn else None,
            "turn_controller": self.turn_controller.to_dict(),
            "shared_memory": self.shared_memory.to_dict(),
            "compressor": self.compressor.to_dict(),
            "created_at": self.created_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoundtableSession":
        seats = [Seat.from_dict(s) for s in data.get("seats", [])]
        tc_data = data.get("turn_controller", {})

        session = cls(
            topic=data.get("topic", ""),
            seats=seats,
            max_rounds=tc_data.get("max_rounds", 3),
            session_id=data.get("session_id"),
            strategy=tc_data.get("strategy", "round_robin"),
        )
        session.status = data.get("status", "ended")
        session.created_at = data.get("created_at", session.created_at)
        session.ended_at = data.get("ended_at")
        session.transcript = [
            TranscriptEntry.from_dict(t) for t in data.get("transcript", [])
        ]
        session.active_turn = None
        session.turn_controller = TurnController.from_dict(tc_data, seats)

        # Phase 2 字段恢复
        if "shared_memory" in data:
            session.shared_memory = SharedMemory.from_dict(data["shared_memory"])
        if "compressor" in data:
            session.compressor = TranscriptCompressor.from_dict(data["compressor"])

        # Phase 3 运行时对象（不序列化，重新初始化）
        session.intervention_queue = InterventionQueue()
        session._lock = asyncio.Lock()
        session._pause_event = asyncio.Event()
        session._pause_event.set()

        return session

    # ============ 持久化 ============

    def save(self) -> bool:
        """将会话状态持久化到 JSON 文件。返回 True 表示成功，False 表示失败。"""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SESSIONS_DIR / f"{self.session_id}.json"
        tmp_path = file_path.with_suffix(".tmp")
        try:
            data = self.to_dict()
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_path), str(file_path))
            logger.debug(f"Roundtable {self.session_id} 已保存")
            return True
        except (IOError, OSError) as e:
            logger.error(f"保存 Roundtable {self.session_id} 失败: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @classmethod
    def load(cls, session_id: str) -> "RoundtableSession | None":
        """从 JSON 文件加载会话"""
        file_path = SESSIONS_DIR / f"{session_id}.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只加载 roundtable 类型
            if data.get("session_type") != "roundtable":
                return None
            return cls.from_dict(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 Roundtable {session_id} 失败: {e}")
            return None

    def __repr__(self):
        return (
            f"<RoundtableSession id={self.session_id} topic={self.topic!r} "
            f"status={self.status} seats={len(self.seats)} "
            f"strategy={self.strategy_name} "
            f"round={self.current_round}/{self.max_rounds}>"
        )
