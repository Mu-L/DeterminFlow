"""
Job CRUD + JSON 持久化
"""
import json
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from croniter import croniter

from src.config import DATA_DIR
from src.cron.types import CronJob

logger = logging.getLogger(__name__)

# 持久化目录
CRON_DIR = DATA_DIR / "cron"
CRON_OUTPUT_DIR = CRON_DIR / "output"
JOBS_FILE = CRON_DIR / "jobs.json"


def _ensure_dirs():
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    CRON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# 模块加载时初始化一次目录，后续调用 _ensure_dirs 仍是幂等的但 syscalls 减少
_ensure_dirs()


class CronJobManager:
    """定时任务管理器：CRUD + 持久化 + 调度计算"""

    def load_jobs(self) -> list[CronJob]:
        """从 JSON 文件加载所有 job"""
        _ensure_dirs()
        if not JOBS_FILE.exists():
            return []
        try:
            with open(JOBS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            jobs = [CronJob.from_dict(d) for d in data.get("jobs", [])]
            logger.debug(f"已加载 {len(jobs)} 个定时任务")
            return jobs
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载定时任务失败: {e}")
            return []

    def save_jobs(self, jobs: list[CronJob]) -> None:
        """持久化所有 job 到 JSON 文件（原子写入）"""
        _ensure_dirs()
        data = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "jobs": [job.to_dict() for job in jobs],
        }
        tmp_path = JOBS_FILE.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_path), str(JOBS_FILE))
        except IOError as e:
            logger.error(f"保存定时任务失败: {e}")
            # 清理可能残留的临时文件
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get_due_jobs(self, jobs: list[CronJob]) -> tuple[list[CronJob], list[CronJob]]:
        """找出所有到期的 enabled jobs（next_run_at <= now()）。

        对于 overdue 的 job，计算宽限期：
        - grace_seconds = min(max(period_seconds/2, 120), 7200)
        - 如果 now - next_run_at > grace_seconds，快进到下一个未来时间而非追赶执行

        Returns:
            (due_jobs, skipped_jobs): due_jobs 为到期待执行列表，
            skipped_jobs 为因 grace period 被快进的 job 列表（next_run_at 已更新）。
            调用方需持久化 skipped_jobs 的 next_run_at 变更。
        """
        now = datetime.now(timezone.utc)
        due: list[CronJob] = []
        skipped: list[CronJob] = []

        for job in jobs:
            if not job.enabled or not job.next_run_at:
                continue

            try:
                next_run = datetime.fromisoformat(job.next_run_at)
            except (ValueError, TypeError):
                logger.warning(f"Job {job.id} next_run_at 解析失败: {job.next_run_at!r}")
                continue

            if next_run <= now:
                # 检查是否 overdue 到需要快进
                period_seconds = self._get_period_seconds(job)
                if period_seconds > 0:
                    grace = min(max(period_seconds / 2, 120), 7200)
                    if (now - next_run).total_seconds() > grace:
                        # 快进到下一个未来时间
                        new_next = self._compute_next_run(job, base=now)
                        if new_next:
                            job.next_run_at = new_next
                            skipped.append(job)
                            logger.info(
                                f"Job {job.id} overdue (grace={grace:.0f}s), "
                                f"快进到 {new_next}"
                            )
                        continue

                due.append(job)

        return due, skipped

    def _get_period_seconds(self, job: CronJob) -> float:
        """估算 job 的周期（秒），用于 grace window 计算"""
        sched = job.schedule
        if sched.kind == "interval" and sched.every_minutes:
            return sched.every_minutes * 60
        if sched.kind == "cron" and sched.expr:
            # 粗略估算：计算两次触发之间的间隔
            try:
                base = datetime.now(timezone.utc)
                cron = croniter(sched.expr, base)
                t1 = cron.get_next(datetime)
                t2 = cron.get_next(datetime)
                diff = (t2 - t1).total_seconds()
                if diff > 0:
                    return diff
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"cron 表达式周期估算失败: {sched.expr!r} — {e}")
            return 3600  # 默认 1 小时
        # once: 没有 period，grace 使用最小值 120
        return 0

    def compute_next_run(self, job: CronJob, base: datetime | None = None) -> str | None:
        """计算 job 的下次运行时间，返回 ISO 格式字符串。

        包含 jitter 逻辑：用 hash(job.id) % int(period * 0.1) 作为确定性偏移（最多 10% 周期，最多 60 秒）
        """
        next_dt = self._compute_next_run(job, base=base)
        if next_dt is None:
            return None
        return next_dt.isoformat()

    def _compute_next_run(self, job: CronJob, base: datetime | None = None) -> datetime | None:
        """计算下次运行时间（不带 jitter），返回 datetime 或 None"""
        sched = job.schedule
        now = base or datetime.now(timezone.utc)

        if sched.kind == "once":
            if sched.at:
                try:
                    at_dt = datetime.fromisoformat(sched.at)
                except (ValueError, TypeError):
                    return None
                # 已过期的 once job 不再调度
                if at_dt <= now:
                    return None
                return at_dt
            return None

        if sched.kind == "interval":
            if sched.every_minutes is None:
                return None
            last = now
            if job.last_run_at:
                try:
                    last = datetime.fromisoformat(job.last_run_at)
                except (ValueError, TypeError):
                    # last_run_at 解析失败时，优先使用 next_run_at（保留执行历史），
                    # 再回退到 now（避免 job 几乎立即执行）
                    if job.next_run_at:
                        try:
                            last = datetime.fromisoformat(job.next_run_at)
                            logger.warning(
                                f"Job {job.id} last_run_at 解析失败: {job.last_run_at!r}，"
                                f"使用 next_run_at 作为基准"
                            )
                        except (ValueError, TypeError):
                            logger.warning(
                                f"Job {job.id} last_run_at 解析失败: {job.last_run_at!r}，"
                                f"next_run_at 也解析失败，使用当前时间作为基准"
                            )
                    else:
                        logger.warning(
                            f"Job {job.id} last_run_at 解析失败: {job.last_run_at!r}，"
                            f"使用当前时间作为基准"
                        )
            return last + timedelta(minutes=sched.every_minutes)

        if sched.kind == "cron":
            if not sched.expr:
                return None
            try:
                cron = croniter(sched.expr, now)
                return cron.get_next(datetime)
            except (KeyError, ValueError) as e:
                logger.warning(f"Job {job.id} cron 表达式解析失败: {e}")
                return None

        return None

    def _apply_jitter(self, job: CronJob, next_dt: datetime, period_seconds: float) -> datetime:
        """对下次运行时间应用确定性 jitter 偏移"""
        if period_seconds <= 0:
            return next_dt
        max_jitter = min(int(period_seconds * 0.1), 60)
        if max_jitter <= 0:
            return next_dt
        # 确定性偏移：基于 job.id 的 hash
        seed = int(hashlib.md5(job.id.encode()).hexdigest(), 16) % max_jitter
        return next_dt + timedelta(seconds=seed)

    def mark_job_run(
        self,
        job: CronJob,
        status: str,
        output: str = "",
    ) -> None:
        """标记 job 执行完成，更新 last_run_at 和 last_status，计算 next_run_at。

        - 保存输出到 data/cron/output/{job_id}/{timestamp}.md
        - once 类型完成后 disable
        - repeat 达到上限后 disable
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        job.last_run_at = now_iso
        job.last_status = status
        job.completed += 1

        # 处理 once 类型
        if job.schedule.kind == "once":
            job.enabled = False
            job.next_run_at = None
        else:
            # 计算下次运行时间
            next_dt = self._compute_next_run(job, base=now)
            if next_dt:
                period_seconds = self._get_period_seconds(job)
                next_dt = self._apply_jitter(job, next_dt, period_seconds)
                job.next_run_at = next_dt.isoformat()
            else:
                job.next_run_at = None

        # 处理 repeat 限制
        if job.repeat is not None and job.completed >= job.repeat:
            job.enabled = False
            job.next_run_at = None
            logger.info(f"Job {job.id} 已完成 {job.completed}/{job.repeat} 次，自动禁用")

        # 保存输出到文件
        if output:
            self._save_output(job.id, now, output)

    def _save_output(self, job_id: str, timestamp: datetime, output: str) -> None:
        """保存 job 执行输出到 data/cron/output/{job_id}/{timestamp}.md"""
        _ensure_dirs()
        out_dir = CRON_OUTPUT_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = timestamp.strftime("%Y%m%d_%H%M%S") + ".md"
        filepath = out_dir / filename
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(output)
            logger.debug(f"Job {job_id} 输出已保存到 {filepath}")
        except IOError as e:
            logger.error(f"保存 Job {job_id} 输出失败: {e}")

    def get_output(self, job_id: str, limit: int = 20) -> list[dict]:
        """获取 job 的历史输出文件列表"""
        _ensure_dirs()
        out_dir = CRON_OUTPUT_DIR / job_id
        if not out_dir.exists():
            return []
        files = sorted(out_dir.glob("*.md"), reverse=True)
        results = []
        for f in files[:limit]:
            stat = f.stat()
            results.append({
                "filename": f.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return results

    def read_output(self, job_id: str, filename: str) -> str | None:
        """读取单个 job 输出文件内容"""
        _ensure_dirs()
        filepath = CRON_OUTPUT_DIR / job_id / filename
        # 安全校验：只允许访问 CRON_OUTPUT_DIR 内的文件
        try:
            resolved = filepath.resolve()
            if not resolved.is_relative_to(CRON_OUTPUT_DIR.resolve()):
                return None
        except Exception:
            return None
        if not filepath.exists():
            return None
        try:
            return filepath.read_text(encoding="utf-8")
        except IOError:
            return None

    def add_job(self, jobs: list[CronJob], job: CronJob) -> None:
        """添加新 job 到列表，计算首次运行时间"""
        now = datetime.now(timezone.utc)
        if job.schedule.kind == "once" and job.schedule.at:
            # once job: next_run_at = at
            job.next_run_at = job.schedule.at
            # 若指定时间已过期，警告并禁用
            try:
                at_dt = datetime.fromisoformat(job.schedule.at)
                if at_dt.tzinfo is None:
                    at_dt = at_dt.replace(tzinfo=timezone.utc)
                if at_dt < now:
                    logger.warning(
                        f"once job '{job.name}' scheduled_at={job.schedule.at} 已过期，"
                        f"将不会自动触发（可手动 enabled=True 后通过 run 立即执行）"
                    )
                    job.enabled = False
            except (ValueError, TypeError):
                pass
        else:
            next_dt = self._compute_next_run(job, base=now)
            if next_dt:
                period_seconds = self._get_period_seconds(job)
                next_dt = self._apply_jitter(job, next_dt, period_seconds)
                job.next_run_at = next_dt.isoformat()
        jobs.append(job)
