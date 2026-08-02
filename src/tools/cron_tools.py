"""
cronjob 工具 — 管理定时任务（创建、查看、更新、暂停、恢复、立即触发、删除、状态）
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.cron.types import CronJob, CronSchedule

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)


class CronjobArgs(BaseModel):
    """cronjob 工具参数"""
    action: str = Field(description=(
        "操作类型: create (创建), list (列出全部), get (查看单个), "
        "update (更新), pause (暂停), resume (恢复), run (立即触发), "
        "remove (删除), status (调度器状态), output (查看输出)"
    ))
    job_id: str | None = Field(default=None, description="任务 ID（get/update/pause/resume/run/remove/output 需要）")
    name: str | None = Field(default=None, description="任务名称（create 可选，update 可选）")
    schedule: str | None = Field(default=None, description="调度表达式（create/update 可选）。格式: 'once:ISO时间' / 'interval:分钟数' / 'cron:cron表达式'")
    prompt: str | None = Field(default=None, description="任务提示词（create/update 可选）")
    agent_type: str | None = Field(default=None, description="Agent 类型（create/update 可选，默认 researcher）")
    silent_on_empty: bool | None = Field(default=None, description="是否启用静默模式（create/update 可选）")
    model_override: str | None = Field(default=None, description="模型覆盖（create/update 可选）")
    max_turns: int | None = Field(default=None, description="最大轮次（create/update 可选）")
    repeat: int | None = Field(default=None, description="重复次数限制（create/update 可选，None=无限）")
    enabled: bool | None = Field(default=None, description="是否启用（update 可选）")
    filename: str | None = Field(default=None, description="输出文件名（output 操作可选）")


def _parse_schedule(raw: str) -> CronSchedule | None:
    """解析调度字符串: 'once:...' / 'interval:...' / 'cron:...'"""
    if ":" not in raw:
        return None
    kind, _, value = raw.partition(":")
    kind = kind.strip().lower()
    value = value.strip()
    if kind == "once":
        return CronSchedule(kind="once", at=value)
    elif kind == "interval":
        try:
            mins = int(value)
        except (ValueError, TypeError):
            return None
        return CronSchedule(kind="interval", every_minutes=mins)
    elif kind == "cron":
        try:
            from croniter import croniter
            if not croniter.is_valid(value):
                logger.warning(f"非法 cron 表达式: {value!r}")
                return None
        except ImportError:
            logger.warning("croniter 未安装，跳过 cron 表达式验证")
        return CronSchedule(kind="cron", expr=value)
    return None


def _format_schedule(sched: CronSchedule) -> str:
    """格式化调度为可读字符串"""
    if sched.kind == "once":
        return f"once:{sched.at}"
    elif sched.kind == "interval":
        return f"interval:{sched.every_minutes}"
    elif sched.kind == "cron":
        return f"cron:{sched.expr}"
    return "unknown"


def _job_to_summary(job: CronJob) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "schedule": _format_schedule(job.schedule),
        "enabled": job.enabled,
        "agent_type": job.agent_type,
        "prompt": job.prompt[:200],
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "last_status": job.last_status,
        "repeat": job.repeat,
        "completed": job.completed,
    }


def _ok(data: dict) -> str:
    """构建成功 JSON 响应"""
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def _fail(message: str) -> str:
    """构建失败 JSON 响应"""
    return json.dumps({"success": False, "message": message}, ensure_ascii=False)


def _require_job_id(kwargs: dict) -> str | None:
    """从 kwargs 提取 job_id，缺失时返回错误 JSON 字符串；成功返回 None"""
    job_id = kwargs.get("job_id")
    if not job_id:
        return _fail("缺少 job_id")
    return None


def _find_job(jobs: list[CronJob], job_id: str) -> CronJob | None:
    """在 jobs 列表中查找指定 ID 的任务"""
    for j in jobs:
        if j.id == job_id:
            return j
    return None


# ---------------------------------------------------------------------------
# 单独的 action 处理函数
# ---------------------------------------------------------------------------

async def _action_status(scheduler: Any, _job_mgr: Any, _kwargs: dict) -> str:
    status = scheduler.get_status()
    return _ok(status)


async def _action_list(_scheduler: Any, job_mgr: Any, _kwargs: dict) -> str:
    jobs = job_mgr.load_jobs()
    summaries = [_job_to_summary(j) for j in jobs]
    return _ok({"total": len(jobs), "jobs": summaries})


async def _action_get(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    target = _find_job(jobs, kwargs["job_id"])
    if target is None:
        return _fail(f"未找到任务 {kwargs['job_id']}")
    return _ok({"job": _job_to_summary(target), "full_prompt": target.prompt})


async def _action_output(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    job_id = kwargs["job_id"]
    filename = kwargs.get("filename")
    if filename:
        content = job_mgr.read_output(job_id, filename)
        if content is None:
            return _fail(f"输出文件不存在: {filename}")
        return _ok({"filename": filename, "content": content[:5000]})
    files = job_mgr.get_output(job_id)
    return _ok({"job_id": job_id, "outputs": files})


async def _action_create(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    prompt = kwargs.get("prompt", "")
    if not prompt:
        return _fail("缺少 prompt")

    schedule_raw = kwargs.get("schedule")
    if not schedule_raw:
        return _fail("缺少 schedule (格式: once:ISO时间 / interval:分钟数 / cron:cron表达式)")

    sched = _parse_schedule(schedule_raw)
    if sched is None:
        return _fail(f"无法解析 schedule: {schedule_raw}")

    job = CronJob(
        id=uuid.uuid4().hex[:8],
        name=kwargs.get("name") or "Unnamed",
        prompt=prompt,
        schedule=sched,
        enabled=True,
        agent_type=kwargs.get("agent_type") or "researcher",
        silent_on_empty=kwargs.get("silent_on_empty") if kwargs.get("silent_on_empty") is not None else True,
        model_override=kwargs.get("model_override"),
        max_turns=kwargs.get("max_turns") or 50,
        repeat=kwargs.get("repeat"),
    )
    jobs = job_mgr.load_jobs()
    job_mgr.add_job(jobs, job)
    job_mgr.save_jobs(jobs)
    logger.info(f"Cron job 已创建: {job.id} ({job.name})")
    return _ok({"job": _job_to_summary(job), "message": f"定时任务 {job.id} 已创建"})


# 白名单字段：仅允许更新以下 CronJob 属性
_UPDATABLE_FIELDS = (
    "name", "prompt", "agent_type", "model_override",
    "repeat", "silent_on_empty", "max_turns", "enabled",
)


async def _action_update(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    target = _find_job(jobs, kwargs["job_id"])
    if target is None:
        return _fail(f"未找到任务 {kwargs['job_id']}")

    # 显式白名单赋值，避免 setattr 修改不可变字段（如 id）
    for field in _UPDATABLE_FIELDS:
        val = kwargs.get(field)
        if val is not None:
            setattr(target, field, val)

    schedule_raw = kwargs.get("schedule")
    if schedule_raw:
        sched = _parse_schedule(schedule_raw)
        if sched is None:
            return _fail(f"无法解析 schedule: {schedule_raw}")
        target.schedule = sched
        target.next_run_at = job_mgr.compute_next_run(target)

    job_mgr.save_jobs(jobs)
    return _ok({"job": _job_to_summary(target), "message": f"任务 {kwargs['job_id']} 已更新"})


async def _action_pause(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    target = _find_job(jobs, kwargs["job_id"])
    if target is None:
        return _fail(f"未找到任务 {kwargs['job_id']}")
    target.enabled = False
    job_mgr.save_jobs(jobs)
    return _ok({"message": f"任务 {kwargs['job_id']} 已暂停"})


async def _action_resume(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    target = _find_job(jobs, kwargs["job_id"])
    if target is None:
        return _fail(f"未找到任务 {kwargs['job_id']}")
    target.enabled = True
    next_run = job_mgr.compute_next_run(target)
    target.next_run_at = next_run
    job_mgr.save_jobs(jobs)
    return _ok({"message": f"任务 {kwargs['job_id']} 已恢复", "next_run_at": next_run})


async def _action_run(scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    target = _find_job(jobs, kwargs["job_id"])
    if target is None:
        return _fail(f"未找到任务 {kwargs['job_id']}")
    await scheduler.run_job_now(target)
    job_mgr.save_jobs(jobs)
    return _ok({"message": f"任务 {kwargs['job_id']} 已触发执行"})


async def _action_remove(_scheduler: Any, job_mgr: Any, kwargs: dict) -> str:
    err = _require_job_id(kwargs)
    if err:
        return err
    jobs = job_mgr.load_jobs()
    before = len(jobs)
    jobs = [j for j in jobs if j.id != kwargs["job_id"]]
    if len(jobs) == before:
        return _fail(f"未找到任务 {kwargs['job_id']}")
    job_mgr.save_jobs(jobs)
    return _ok({"message": f"任务 {kwargs['job_id']} 已删除"})


# action 名称 → 处理函数 映射
_ACTION_HANDLERS: dict[str, Any] = {
    "status": _action_status,
    "list": _action_list,
    "get": _action_get,
    "output": _action_output,
    "create": _action_create,
    "update": _action_update,
    "pause": _action_pause,
    "resume": _action_resume,
    "run": _action_run,
    "remove": _action_remove,
}


def create_cronjob_tool(
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 cronjob 工具。

    通过 session_manager 获取 _cron_scheduler 和 _cron_job_manager。
    """

    def _get_deps() -> tuple | None:
        """从 session_manager 获取调度器和 job 管理器"""
        scheduler = session_manager.cron_scheduler
        job_mgr = session_manager.cron_job_manager
        if scheduler is None or job_mgr is None:
            return None
        return scheduler, job_mgr

    async def _cronjob(**kwargs: Any) -> str:
        action = kwargs.get("action", "list")
        deps = _get_deps()
        if deps is None:
            return _fail("定时任务系统未初始化")

        scheduler, job_mgr = deps

        handler = _ACTION_HANDLERS.get(action)
        if handler is None:
            return _fail(
                f"未知操作: {action}。支持: {', '.join(_ACTION_HANDLERS)}"
            )

        try:
            return await handler(scheduler, job_mgr, kwargs)
        except Exception:
            logger.exception("cronjob 工具异常")
            return _fail("操作失败（详见服务日志）")

    return StructuredTool(
        name="cronjob",
        description=(
            "管理定时任务系统。支持的操作：\n"
            "- status: 查看调度器状态（job 总数、due job 数等）\n"
            "- list: 列出所有定时任务\n"
            "- get: 查看单个任务详情（需要 job_id）\n"
            "- output: 查看任务历史输出（需要 job_id；可选 filename）\n"
            "- create: 创建新任务（需要 schedule + prompt；可选 name/agent_type/silent_on_empty 等）\n"
            "- update: 更新已有任务（需要 job_id；其他字段可选）\n"
            "- pause: 暂停任务（需要 job_id）\n"
            "- resume: 恢复任务（需要 job_id）\n"
            "- run: 立即触发一次任务（需要 job_id）\n"
            "- remove: 删除任务（需要 job_id）\n\n"
            "schedule 格式:\n"
            "- once:ISO时间 — 一次性执行（如 'once:2026-06-01T08:00:00+08:00'）\n"
            "- interval:分钟数 — 周期性执行（如 'interval:60' 每 60 分钟）\n"
            "- cron:cron表达式 — cron 表达式调度（如 'cron:0 8 * * *' 每天早上 8 点）"
        ),
        args_schema=CronjobArgs,
        func=lambda **kw: None,
        coroutine=_cronjob,
    )
