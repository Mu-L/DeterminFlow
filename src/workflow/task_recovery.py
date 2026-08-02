"""工作流任务的节点重试、跳过与进程重启恢复能力。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.config import WORKFLOWS_DIR

from .definition import WorkflowDef, WorkflowTask, _now_iso
from .failure_policy import (
    AUTO_RETRY_TRIGGER,
    MANUAL_RETRY_TRIGGER,
    RECOVERY_REISSUE_TRIGGER,
    activate_scheduled_retry,
    apply_failure_skip,
    can_auto_retry,
    prepare_node_retry,
    record_attempt,
    should_auto_skip,
)

if TYPE_CHECKING:
    from .runtime_models import NodeExecutionState


logger = logging.getLogger(__name__)

_CONTROLLABLE_NODE_STATUSES = {"failed", "retry_waiting"}
_CONTROLLABLE_TASK_STATUSES = {"failed", "retry_waiting"}
_RECOVERABLE_TASK_STATUSES = {"running", "retry_waiting", "resume_pending"}
_PENDING_RESUME_TRIGGERS = {
    AUTO_RETRY_TRIGGER,
    MANUAL_RETRY_TRIGGER,
    RECOVERY_REISSUE_TRIGGER,
}
_INTERRUPTED_ERROR = "workflow_process_interrupted: 服务重启时节点仍处于 running"
_INTERRUPTED_APPROVAL_ERROR = (
    "workflow_process_interrupted: 服务重启时节点仍在等待人工审批"
)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _control_error(
    *,
    error: str,
    message: str,
    workflow_id: str,
    task_id: str,
    node_id: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "message": message,
        "workflow_id": workflow_id,
        "task_id": task_id,
        "node_id": node_id,
    }


class WorkflowTaskRecoveryMixin:
    """由 ``WorkflowManager`` 复用的通用节点失败恢复状态机。"""

    def _init_task_recovery(self) -> None:
        self._task_control_locks: dict[str, asyncio.Lock] = {}
        self._retry_timers: dict[str, asyncio.Task] = {}
        self._retry_timer_generations: dict[str, int] = {}
        self._task_recovery_stopping = False

    def _task_control_lock(self, task_id: str) -> asyncio.Lock:
        lock = self._task_control_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._task_control_locks[task_id] = lock
        return lock

    def _cancel_retry_timer(self, task_id: str) -> None:
        self._retry_timer_generations[task_id] = (
            self._retry_timer_generations.get(task_id, 0) + 1
        )
        timer = self._retry_timers.pop(task_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    @staticmethod
    def _retry_waiting_states(
        task: WorkflowTask,
    ) -> list[tuple[str, "NodeExecutionState", datetime]]:
        waiting: list[tuple[str, "NodeExecutionState", datetime]] = []
        for node_id, state in task.node_states.items():
            if state.status != "retry_waiting":
                continue
            due_at = _parse_datetime(state.next_retry_at)
            if due_at is not None:
                waiting.append((node_id, state, due_at))
        return waiting

    def _schedule_retry_for_task(self, task: WorkflowTask) -> None:
        """为任务中最早到期的自动重试创建唯一内存计时器。"""
        self._cancel_retry_timer(task.task_id)
        current = self._load_task(task.workflow_id, task.task_id)
        if current is None:
            return
        task = current
        if self._task_recovery_stopping or task.status != "retry_waiting":
            return

        waiting = self._retry_waiting_states(task)
        if not waiting:
            logger.warning(
                "任务标记为 retry_waiting 但没有有效 next_retry_at: workflow=%s task=%s",
                task.workflow_id,
                task.task_id,
            )
            return

        earliest = min(item[2] for item in waiting)
        generation = self._retry_timer_generations[task.task_id]
        timer = asyncio.create_task(
            self._retry_timer_worker(
                task.workflow_id,
                task.task_id,
                earliest,
                generation,
            )
        )
        self._retry_timers[task.task_id] = timer

    async def _retry_timer_worker(
        self,
        workflow_id: str,
        task_id: str,
        due_at: datetime,
        generation: int,
    ) -> None:
        try:
            delay = max(0.0, (due_at - _utc_now()).total_seconds())
            if delay:
                await asyncio.sleep(delay)
            await self._activate_due_retries(workflow_id, task_id, generation)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "激活自动重试失败: workflow=%s task=%s",
                workflow_id,
                task_id,
            )
        finally:
            if self._retry_timer_generations.get(task_id) == generation:
                self._retry_timers.pop(task_id, None)

    async def _activate_due_retries(
        self,
        workflow_id: str,
        task_id: str,
        generation: int,
    ) -> None:
        async with self._task_control_lock(task_id):
            if self._task_recovery_stopping:
                return
            if self._retry_timer_generations.get(task_id) != generation:
                return
            if not self.is_workflow_owner_enabled(workflow_id):
                logger.info(
                    "扩展未运行，保留节点自动重试等待状态: workflow=%s task=%s",
                    workflow_id,
                    task_id,
                )
                return
            running = self._running_tasks.get(task_id)
            if running is not None and not running.done():
                return

            task = self._load_task(workflow_id, task_id)
            if task is None or task.status != "retry_waiting":
                return

            now = _utc_now()
            activated: list[str] = []
            for node_id, state, due_at in self._retry_waiting_states(task):
                if due_at <= now:
                    task.node_states[node_id] = activate_scheduled_retry(state)
                    activated.append(node_id)
            if not activated:
                self._schedule_retry_for_task(task)
                return

            task.status = "resume_pending"
            task.current_node_id = None
            task.run_id = None
            task.completed_at = None
            self._save_task(task)

            result = await self.run_task(workflow_id, task_id)
            if not result.get("success"):
                logger.warning(
                    "自动重试任务未能启动: workflow=%s task=%s nodes=%s result=%s",
                    workflow_id,
                    task_id,
                    activated,
                    result,
                )

    def _validate_node_control(
        self,
        workflow_id: str,
        task_id: str,
        node_id: str,
        expected_attempt_count: int,
    ) -> tuple[WorkflowTask | None, dict[str, Any] | None]:
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None, _control_error(
                error="task_not_found",
                message=f"任务 {task_id} 不存在",
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        if task.status not in _CONTROLLABLE_TASK_STATUSES:
            return None, _control_error(
                error="node_control_conflict",
                message=(
                    f"任务状态为 {task.status}；仅 failed 或 retry_waiting "
                    "任务允许修改失败节点"
                ),
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        state = task.node_states.get(node_id)
        if state is None:
            return None, _control_error(
                error="node_not_found",
                message=f"任务中不存在节点 {node_id}",
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        running = self._running_tasks.get(task_id)
        if running is not None and not running.done():
            return None, _control_error(
                error="node_control_conflict",
                message="任务正在执行，不能同时修改节点状态",
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        if state.status not in _CONTROLLABLE_NODE_STATUSES:
            return None, _control_error(
                error="node_control_conflict",
                message=(
                    f"节点状态为 {state.status}；仅 failed 或 retry_waiting "
                    "节点允许此操作"
                ),
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        if (
            isinstance(expected_attempt_count, bool)
            or not isinstance(expected_attempt_count, int)
            or expected_attempt_count < 0
        ):
            return None, _control_error(
                error="node_control_invalid",
                message="expected_attempt_count 必须是非负整数",
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        if state.attempt_count != expected_attempt_count:
            return None, _control_error(
                error="node_control_stale",
                message=(
                    "节点已产生新的执行尝试；请刷新详情后再操作 "
                    f"(expected={expected_attempt_count}, current={state.attempt_count})"
                ),
                workflow_id=workflow_id,
                task_id=task_id,
                node_id=node_id,
            )
        return task, None

    async def retry_node(
        self,
        workflow_id: str,
        task_id: str,
        node_id: str,
        expected_attempt_count: int,
    ) -> dict[str, Any]:
        """原任务、原快照和原参数不变，手动重试一个失败节点。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        async with self._task_control_lock(task_id):
            task, error = self._validate_node_control(
                workflow_id, task_id, node_id, expected_attempt_count
            )
            if error is not None:
                return error
            assert task is not None

            self._cancel_retry_timer(task_id)
            state = task.node_states[node_id]
            if state.status == "retry_waiting":
                state = replace(state, status="failed")
            task.node_states[node_id] = prepare_node_retry(
                state,
                trigger=MANUAL_RETRY_TRIGGER,
                preserve_child_runtime=True,
            )
            self._prepare_task_for_resume(task)
            self._save_task(task)
            result = await self.run_task(workflow_id, task_id)
            return {
                **result,
                "node_id": node_id,
                "trigger": MANUAL_RETRY_TRIGGER,
            }

    async def skip_node(
        self,
        workflow_id: str,
        task_id: str,
        node_id: str,
        expected_attempt_count: int,
    ) -> dict[str, Any]:
        """清除失败节点产出并将其标记为 skipped 后继续原任务。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        async with self._task_control_lock(task_id):
            task, error = self._validate_node_control(
                workflow_id, task_id, node_id, expected_attempt_count
            )
            if error is not None:
                return error
            assert task is not None

            self._cancel_retry_timer(task_id)
            state = task.node_states[node_id]
            if state.status == "retry_waiting":
                state = replace(state, status="failed")
            task.node_states[node_id] = apply_failure_skip(
                state,
                completed_at=_now_iso(),
            )
            self._prepare_task_for_resume(task)
            self._save_task(task)
            result = await self.run_task(workflow_id, task_id)
            return {**result, "node_id": node_id, "status": "skipped"}

    @staticmethod
    def _prepare_task_for_resume(task: WorkflowTask) -> None:
        # 与普通待启动任务区分；若进程在落盘和 run_task 之间退出，启动扫描
        # 仍能识别并继续这次恢复意图。
        task.status = "resume_pending"
        task.current_node_id = None
        task.run_id = None
        task.completed_at = None

    def _task_definition(self, task: WorkflowTask) -> WorkflowDef:
        if task.snapshot_definition:
            return WorkflowDef.from_dict(task.snapshot_definition)
        workflow = self.get_workflow(task.workflow_id)
        if workflow and workflow.get("definition"):
            return WorkflowDef.from_dict(workflow["definition"])
        raise ValueError("任务缺少 definition 快照且当前工作流定义不可用")

    @staticmethod
    def _interrupted_state(
        state: "NodeExecutionState",
        *,
        completed_at: str,
        error_message: str = _INTERRUPTED_ERROR,
    ) -> "NodeExecutionState":
        error = state.error.strip()
        error = f"{error}\n{error_message}" if error else error_message
        failed = replace(
            state,
            status="failed",
            completed_at=completed_at,
            error=error,
            attempt_count=max(1, state.attempt_count),
        )
        return record_attempt(
            failed,
            status="failed",
            completed_at=completed_at,
        )

    @staticmethod
    def _prepare_recovery_reissue(
        state: "NodeExecutionState",
        *,
        preserve_result: bool,
    ) -> "NodeExecutionState":
        """把已收尾的中断 Attempt 转为不消耗重试预算的恢复入口。"""
        return replace(
            state,
            status="pending",
            session_id=state.session_id if preserve_result else "",
            started_at=None,
            completed_at=None,
            summary=state.summary if preserve_result else "",
            error="",
            rejection_reason="",
            outputs={},
            stdout="",
            stderr="",
            is_skipped=False,
            next_retry_at=None,
            next_attempt_trigger=RECOVERY_REISSUE_TRIGGER,
        )

    @classmethod
    def _contains_recovery_reissue(
        cls,
        state: "NodeExecutionState",
    ) -> bool:
        return (
            state.status == "pending"
            and state.next_attempt_trigger == RECOVERY_REISSUE_TRIGGER
        ) or any(
            cls._contains_recovery_reissue(child)
            for child in state.child_states.values()
        )

    @classmethod
    def _recover_interrupted_child_states(
        cls,
        state: "NodeExecutionState",
        *,
        completed_at: str,
    ) -> "NodeExecutionState":
        """后序收尾嵌套子流程中因进程退出而悬空的子节点。"""
        recovered_children: dict[str, "NodeExecutionState"] = {}
        for child_id, child_state in state.child_states.items():
            recovered = cls._recover_interrupted_child_states(
                child_state,
                completed_at=completed_at,
            )
            descendant_reissue = any(
                cls._contains_recovery_reissue(child)
                for child in recovered.child_states.values()
            )
            if recovered.status == "waiting_approval":
                closed = cls._interrupted_state(
                    recovered,
                    completed_at=completed_at,
                    error_message=_INTERRUPTED_APPROVAL_ERROR,
                )
                recovered = cls._prepare_recovery_reissue(
                    closed,
                    preserve_result=bool(closed.session_id and closed.summary),
                )
            elif recovered.status == "running":
                closed = cls._interrupted_state(
                    recovered,
                    completed_at=completed_at,
                )
                recovered = (
                    cls._prepare_recovery_reissue(
                        closed,
                        preserve_result=False,
                    )
                    if descendant_reissue
                    else closed
                )
            recovered_children[child_id] = recovered
        if recovered_children == state.child_states:
            return state
        return replace(state, child_states=recovered_children)

    def _recover_waiting_approval_nodes(
        self,
        task: WorkflowTask,
        definition: WorkflowDef,
    ) -> list[str]:
        """收尾已丢失的审批等待器，并在原任务中重新签发审批。"""
        recovered: list[str] = []
        now = _now_iso()
        nodes = {node.id: node for node in definition.nodes}
        for node_id, state in list(task.node_states.items()):
            state = self._recover_interrupted_child_states(
                state,
                completed_at=now,
            )
            task.node_states[node_id] = state
            if state.status != "waiting_approval":
                continue
            closed = self._interrupted_state(
                state,
                completed_at=now,
                error_message=_INTERRUPTED_APPROVAL_ERROR,
            )
            preserve_agent_result = (
                nodes.get(node_id) is not None
                and nodes[node_id].node_type == "agent"
                and bool(closed.session_id)
                and bool(closed.summary)
            )
            task.node_states[node_id] = self._prepare_recovery_reissue(
                closed,
                preserve_result=preserve_agent_result,
            )
            recovered.append(node_id)
        return recovered

    def _recover_interrupted_nodes(
        self,
        task: WorkflowTask,
        definition: WorkflowDef,
    ) -> list[str]:
        recovered: list[str] = []
        nodes = {node.id: node for node in definition.nodes}
        now = _now_iso()
        for node_id, state in list(task.node_states.items()):
            state = self._recover_interrupted_child_states(
                state,
                completed_at=now,
            )
            task.node_states[node_id] = state
            if state.status != "running":
                continue
            failed = self._interrupted_state(state, completed_at=now)
            if any(
                self._contains_recovery_reissue(child)
                for child in failed.child_states.values()
            ):
                task.node_states[node_id] = self._prepare_recovery_reissue(
                    failed,
                    preserve_result=False,
                )
                recovered.append(node_id)
                continue
            node = nodes.get(node_id)
            if node is not None and can_auto_retry(node, failed):
                interval = max(0, node.auto_retry_interval_seconds)
                due_at = (_utc_now() + timedelta(seconds=interval)).isoformat()
                task.node_states[node_id] = prepare_node_retry(
                    failed,
                    trigger=AUTO_RETRY_TRIGGER,
                    next_retry_at=due_at,
                    preserve_child_runtime=True,
                )
            elif node is not None and should_auto_skip(node, failed):
                task.node_states[node_id] = apply_failure_skip(
                    failed,
                    completed_at=now,
                )
            else:
                task.node_states[node_id] = failed
            recovered.append(node_id)
        return recovered

    async def recover_workflow_tasks(self) -> dict[str, int]:
        """恢复启用工作流中因进程退出而中断或等待重试的任务。"""
        summary = {
            "scanned": 0,
            "resumed": 0,
            "scheduled": 0,
            "failed": 0,
            "errors": 0,
        }
        self._task_recovery_stopping = False
        if not WORKFLOWS_DIR.exists():
            return summary

        for workflow_dir in sorted(WORKFLOWS_DIR.iterdir()):
            if not workflow_dir.is_dir():
                continue
            workflow_id = workflow_dir.name
            if not self.is_workflow_owner_enabled(workflow_id):
                continue
            tasks_dir = workflow_dir / "tasks"
            if not tasks_dir.exists():
                continue
            for task_file in sorted(tasks_dir.glob("*.json")):
                try:
                    task = WorkflowTask.from_dict(
                        json.loads(task_file.read_text(encoding="utf-8"))
                    )
                    if task.status not in _RECOVERABLE_TASK_STATUSES:
                        continue
                    summary["scanned"] += 1
                    action = await self._recover_task(task)
                    summary[action] += 1
                except Exception:
                    summary["errors"] += 1
                    logger.exception(
                        "恢复工作流任务失败: workflow=%s file=%s",
                        workflow_id,
                        task_file,
                    )
        return summary

    async def _recover_task(self, task: WorkflowTask) -> str:
        async with self._task_control_lock(task.task_id):
            current = self._load_task(task.workflow_id, task.task_id)
            if current is None or current.status not in _RECOVERABLE_TASK_STATUSES:
                return "failed"
            definition = self._task_definition(current)
            interrupted = self._recover_interrupted_nodes(current, definition)
            reissued_approvals = self._recover_waiting_approval_nodes(
                current,
                definition,
            )

            for node_id, state in list(current.node_states.items()):
                if state.status != "retry_waiting":
                    continue
                if _parse_datetime(state.next_retry_at) is not None:
                    continue
                message = "retry_waiting 节点缺少有效 next_retry_at，无法恢复"
                current.node_states[node_id] = replace(
                    state,
                    status="failed",
                    completed_at=_now_iso(),
                    next_retry_at=None,
                    error=f"{state.error}\n{message}".strip(),
                )

            states = list(current.node_states.values())
            # resume_pending 表示恢复动作已经持久化、但尚未可靠交给执行器。
            # run_task 自身也会先把 Task 写成 running，再创建后台协程，因此
            # running + pending retry trigger 是同一启动窗口的后半段。两种情况
            # 都必须先兑现 pending 启动意图，不能被并行兄弟的 failed 状态截断。
            resume_requested = current.status == "resume_pending" or any(
                state.status == "pending"
                and state.next_attempt_trigger in _PENDING_RESUME_TRIGGERS
                for state in states
            )

            if (
                not resume_requested
                and any(state.status == "retry_waiting" for state in states)
            ):
                current.status = "retry_waiting"
                current.completed_at = None
                current.current_node_id = None
                self._save_task(current)
                self._schedule_retry_for_task(current)
                return "scheduled"

            if (
                not resume_requested
                and any(state.status == "failed" for state in states)
            ):
                current.status = "failed"
                current.completed_at = _now_iso()
                current.current_node_id = None
                self._save_task(current)
                logger.info(
                    "中断任务恢复为 failed: workflow=%s task=%s nodes=%s approvals=%s",
                    current.workflow_id,
                    current.task_id,
                    interrupted,
                    reissued_approvals,
                )
                return "failed"

            self._prepare_task_for_resume(current)
            self._save_task(current)
            result = await self.run_task(current.workflow_id, current.task_id)
            if result.get("success"):
                return "resumed"
            logger.warning(
                "恢复任务未能启动: workflow=%s task=%s result=%s",
                current.workflow_id,
                current.task_id,
                result,
            )
            return "failed"

    async def shutdown_task_recovery(self) -> None:
        """取消进程内计时器；持久化的 retry_waiting 状态保持不变。"""
        self._task_recovery_stopping = True
        timers = list(self._retry_timers.values())
        for task_id in list(self._retry_timers):
            self._cancel_retry_timer(task_id)
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)
        self._task_control_locks.clear()
