"""
定时任务数据模型
"""
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

_VALID_KINDS = {"once", "interval", "cron"}


@dataclass
class CronSchedule:
    kind: Literal["once", "interval", "cron"]
    at: str | None = None              # ISO timestamp for "once"
    every_minutes: int | None = None   # for "interval"
    expr: str | None = None            # cron expression for "cron"

    def __post_init__(self):
        if self.kind not in _VALID_KINDS:
            logger.warning(f"CronSchedule.kind 值 '{self.kind}' 不合法，合法值: {_VALID_KINDS}，回退为 'once'")
            self.kind = "once"


@dataclass
class CronJob:
    id: str              # 8-char hex
    name: str
    prompt: str          # 发送给 agent 的任务描述
    schedule: CronSchedule
    enabled: bool = True
    agent_type: str = "researcher"     # 使用哪种 agent 类型
    model_override: str | None = None
    silent_on_empty: bool = True       # 启用 [SILENT] 机制
    max_turns: int = 50
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None     # "success" | "error" | "silent"
    repeat: int | None = None          # None=无限, N=执行N次后自动删除
    completed: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": {
                "kind": self.schedule.kind,
                "at": self.schedule.at,
                "every_minutes": self.schedule.every_minutes,
                "expr": self.schedule.expr,
            },
            "enabled": self.enabled,
            "agent_type": self.agent_type,
            "model_override": self.model_override,
            "silent_on_empty": self.silent_on_empty,
            "max_turns": self.max_turns,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "repeat": self.repeat,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        sched_data = data.get("schedule", {})
        schedule = CronSchedule(
            kind=sched_data.get("kind", "once"),
            at=sched_data.get("at"),
            every_minutes=sched_data.get("every_minutes"),
            expr=sched_data.get("expr"),
        )
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            prompt=data.get("prompt", ""),
            schedule=schedule,
            enabled=data.get("enabled", True),
            agent_type=data.get("agent_type", "researcher"),
            model_override=data.get("model_override"),
            silent_on_empty=data.get("silent_on_empty", True),
            max_turns=data.get("max_turns", 50),
            next_run_at=data.get("next_run_at"),
            last_run_at=data.get("last_run_at"),
            last_status=data.get("last_status"),
            repeat=data.get("repeat"),
            completed=data.get("completed", 0),
        )
