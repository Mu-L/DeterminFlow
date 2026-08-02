"""
定时任务调度器 — 后台 tick loop，每分钟检查并执行到期 job
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.cron.runner import run_job

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager
    from src.cron.jobs import CronJobManager

logger = logging.getLogger(__name__)


class CronScheduler:
    """定时任务调度器。

    通过后台 asyncio task 驱动 tick loop，每分钟检查一次是否
    有到期的 job，有则异步执行。单个 job 失败不影响其他 job。
    """

    def __init__(self, session_manager: "SessionManager", job_manager: "CronJobManager"):
        self._session_mgr = session_manager
        self._job_mgr = job_manager
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._jobs_cache: list | None = None
        self._jobs_cache_ts: float = 0.0

    async def start(self) -> None:
        """启动后台 tick loop"""
        if self._task and not self._task.done():
            logger.warning("CronScheduler 已在运行")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="cron-scheduler")
        logger.info("CronScheduler 已启动")

    async def stop(self) -> None:
        """停止后台 tick loop"""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("CronScheduler 已停止")

    async def _run_loop(self) -> None:
        """后台循环：每 60 秒执行一次 tick"""
        logger.info("CronScheduler 循环开始运行")
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron tick 异常")
            # 等待 60 秒，但响应 stop_event
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=60,
                )
                break  # stop_event 被触发
            except asyncio.TimeoutError:
                pass  # 正常超时，继续下一轮

    async def _tick(self) -> None:
        """单次 tick：加载 jobs，找出到期的，逐个执行"""
        jobs = self._job_mgr.load_jobs()
        if not jobs:
            return

        due, skipped = self._job_mgr.get_due_jobs(jobs)
        if not due:
            return

        logger.info(f"tick: {len(due)} 个到期 job")

        for job in due:
            try:
                await self._run_job(job)
            except Exception:
                logger.exception(f"Job {job.id} 执行失败")

        # 循环结束后统一持久化，避免多次重复序列化+磁盘写入
        try:
            self._job_mgr.save_jobs(jobs)
            self._jobs_cache = None  # 失效缓存，下次 get_status 重新读取
        except Exception:
            logger.exception("保存 jobs 失败")

    async def _run_job(self, job) -> None:
        """执行单个 job（委托给 runner）"""
        logger.info(f"执行 Job: {job.id} ({job.name})")
        await run_job(self._session_mgr, self._job_mgr, job)

    async def run_job_now(self, job) -> None:
        """立即触发一次 job 执行（外部调用）。

        注意：不修改 job.next_run_at，避免对外部调用者产生副作用。
        run_job() 内部的 mark_job_run() 会自动更新调度状态。
        """
        await self._run_job(job)

    def get_status(self) -> dict:
        """返回调度器状态快照（使用 30s TTL 缓存避免频繁磁盘读取）"""
        now_mono = time.monotonic()
        if self._jobs_cache is None or (now_mono - self._jobs_cache_ts) > 30:
            self._jobs_cache = self._job_mgr.load_jobs()
            self._jobs_cache_ts = now_mono
        jobs = self._jobs_cache
        now_iso = datetime.now(timezone.utc).isoformat()
        due_count = len([
            j for j in jobs
            if j.enabled and j.next_run_at and j.next_run_at <= now_iso
        ])
        return {
            "running": self._task is not None and not self._task.done(),
            "total_jobs": len(jobs),
            "enabled_jobs": sum(1 for j in jobs if j.enabled),
            "due_jobs": due_count,
        }
