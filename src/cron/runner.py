"""
Job 执行器 — 通过创建 Sub Session 执行定时任务
"""
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager
    from src.cron.jobs import CronJobManager
    from src.cron.types import CronJob

logger = logging.getLogger(__name__)

CRON_CONTEXT = (
    "[IMPORTANT] You are running as a scheduled cron job. "
    "There is no user present — execute the task fully and autonomously. "
    "Do not ask questions or wait for follow-up. "
    "If there is genuinely nothing new to report, respond with exactly "
    "\"[SILENT]\" (nothing else) to suppress output. "
    "Never combine [SILENT] with other content."
)


def _is_silent(text: str) -> bool:
    """检测回复是否为 [SILENT]（忽略空白和大小写）"""
    return text.strip().upper() == "[SILENT]"


async def run_job(
    session_mgr: "SessionManager",
    job_mgr: "CronJobManager",
    job: "CronJob",
) -> None:
    """执行单个定时任务。

    流程：
    1. 构建 cron context prompt，注入到 system prompt 末尾
    2. 通过 session_mgr.create_sub_session() 创建子会话
    3. 等待 session 完成
    4. [SILENT] 检测
    5. mark_job_run 记录结果
    """
    # 构建 cron context（注入到 sub agent 的 system prompt 末尾）
    cron_context = CRON_CONTEXT

    try:
        # 创建子会话（自动发送首条消息）
        result = await session_mgr.create_sub_session(
            task_description=job.prompt,
            custom_prompt=cron_context,
            agent_type=job.agent_type,
            model_override=job.model_override,
        )

        if not result.get("success"):
            error_msg = result.get("message", "未知错误")
            logger.error(f"Job {job.id} 子会话创建失败: {error_msg}")
            job_mgr.mark_job_run(job, "error", f"子会话创建失败: {error_msg}")
            return

        session_id = result.get("session_id")
        if not session_id:
            logger.error(f"Job {job.id} 子会话创建成功但返回结果缺少 session_id: {result}")
            job_mgr.mark_job_run(job, "error", "子会话创建成功但返回结果缺少 session_id")
            return
        logger.info(f"Job {job.id} 子会话 {session_id} 已创建，等待执行完成...")

        # 等待子会话完成（polling 方式检查状态）
        max_wait = job.max_turns * 60  # 粗略超时：每轮最多 60 秒
        waited = 0
        poll_interval = 2  # 每 2 秒检查一次
        timed_out = False

        while waited < max_wait:
            session = session_mgr.get_session(session_id)
            if session is None:
                logger.warning(f"Job {job.id} 子会话 {session_id} 已消失")
                job_mgr.mark_job_run(job, "error", f"子会话 {session_id} 已消失")
                return

            if session.status in ("completed", "error"):
                break

            await asyncio.sleep(poll_interval)
            waited += poll_interval
        else:
            # while 循环正常结束（未 break），说明超时
            timed_out = True

        # 获取最终 session 状态
        session = session_mgr.get_session(session_id)
        if session is None:
            job_mgr.mark_job_run(job, "error", f"子会话 {session_id} 最终已消失")
            return

        # 超时处理：尝试取消仍在运行的会话
        if timed_out and session.status not in ("completed", "error"):
            logger.warning(f"Job {job.id} 子会话 {session_id} 超时 ({max_wait}s)，尝试终止")
            try:
                await session.abort()
            except Exception as abort_err:
                logger.error(f"Job {job.id} 超时终止子会话失败: {abort_err}")
            job_mgr.mark_job_run(job, "timeout", f"子会话 {session_id} 执行超时 ({max_wait}s)")
            return

        if session.status == "error":
            error_output = f"子会话 {session_id} 执行失败 (status=error)"
            # 尝试获取最后的消息作为上下文
            last_msg = session.get_last_assistant_message()
            if last_msg:
                error_output += f"\n\nLast message:\n{last_msg[:2000]}"
            job_mgr.mark_job_run(job, "error", error_output)
            logger.warning(f"Job {job.id} 子会话 {session_id} 执行失败")
            return

        # 获取 assistant 的最终回复
        output_text = session.get_last_assistant_message()

        # 空输出检测：无 assistant 消息或内容为空时标记为 silent
        if not output_text or not output_text.strip():
            job_mgr.mark_job_run(job, "silent", output_text)
            logger.info(f"Job {job.id} 静默完成 (empty output)")
        elif job.silent_on_empty and _is_silent(output_text):
            job_mgr.mark_job_run(job, "silent", output_text)
            logger.info(f"Job {job.id} 静默完成 (silent)")
        else:
            job_mgr.mark_job_run(job, "success", output_text)
            logger.info(f"Job {job.id} 执行成功")

    except Exception as e:
        logger.exception(f"Job {job.id} 执行异常")
        try:
            job_mgr.mark_job_run(job, "error", "执行异常，请查看服务日志获取详情")
        except Exception as mark_err:
            logger.error(f"Job {job.id} mark_job_run 也失败: {mark_err}")
