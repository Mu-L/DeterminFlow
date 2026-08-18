"""Sticky Task ownership and Controller-to-Executor routing helpers."""

from __future__ import annotations

import asyncio
import json
import logging

from .executor_client import ExecutorUnavailable


logger = logging.getLogger(__name__)


class WorkflowExecutionRoutingMixin:
    def attach_execution_delegate(self, delegate) -> None:
        self._execution_delegate = delegate

    def set_local_executor_identity(self, identity) -> None:
        self._local_executor_identity = identity

    @staticmethod
    def _executor_error(message: str, *, code: str = "executor_unavailable") -> dict:
        return {"success": False, "error": code, "message": message}

    def _task_ownership_error(self, task) -> dict | None:
        identity = getattr(self, "_local_executor_identity", None)
        if identity is None:
            return None
        if (
            task.executor_id != identity.executor_id
            or task.executor_epoch != identity.epoch
        ):
            return self._executor_error(
                "Task 不属于当前 Workflow Executor 世代",
                code="executor_epoch_stale",
            )
        return None

    def _should_delegate_task(self, task) -> bool:
        delegate = getattr(self, "_execution_delegate", None)
        if delegate is None or task.executor_id == "controller":
            return False
        return not task.main_takeover and task.status != "pre_running"

    def _delegate_client_for_task(self, task, *, allow_assignment: bool):
        delegate = self._execution_delegate
        if hasattr(delegate, "client_for"):
            if task.executor_id is not None:
                try:
                    return delegate.client_for(task.executor_id)
                except (ExecutorUnavailable, KeyError):
                    return None
            if allow_assignment:
                return delegate.select_client(task.task_id)
            return None
        return delegate

    def _assign_delegate(self, task):
        client = self._delegate_client_for_task(task, allow_assignment=True)
        if client is None:
            return None
        identity = client.identity
        if task.executor_id is None:
            task.executor_id = identity.executor_id
            task.executor_epoch = identity.epoch
            self._save_task(task)
        return client

    def _assign_controller(self, task) -> None:
        if task.executor_id is None:
            task.executor_id = "controller"
            task.executor_epoch = "inline"
            self._save_task(task)

    def _delegate_identity_error(self, task, client) -> dict | None:
        if client is None:
            return self._executor_error(
                f"Task 所属 Executor 不在当前执行池: {task.executor_id}",
            )
        identity = client.identity
        if task.executor_id == identity.executor_id and task.executor_epoch == identity.epoch:
            return None
        return self._executor_error(
            "Task 所属 Executor 世代尚未完成恢复交接",
            code="executor_epoch_stale",
        )

    async def _route_async_task_operation(self, operation: str, task, **arguments):
        if not self._should_delegate_task(task):
            return None
        client = self._assign_delegate(task)
        ownership_error = self._delegate_identity_error(task, client)
        if ownership_error is not None:
            return ownership_error
        try:
            return await client.call(operation, **arguments)
        except Exception as exc:
            logger.warning("Workflow Executor %s 请求失败: %s", operation, exc)
            return self._executor_error(str(exc))

    def _route_sync_task_operation(self, operation: str, task, **arguments):
        if not self._should_delegate_task(task):
            return None
        client = self._assign_delegate(task)
        ownership_error = self._delegate_identity_error(task, client)
        if ownership_error is not None:
            return ownership_error
        try:
            return client.call_sync(operation, **arguments)
        except Exception as exc:
            logger.warning("Workflow Executor %s 请求失败: %s", operation, exc)
            return self._executor_error(str(exc))

    def _reassign_executor_tasks(self, predicate, current) -> int:
        recoverable = {
            "pending", "running", "retry_waiting", "resume_pending", "failed",
        }
        reassigned = 0
        workflows_dir = self._execution_control.workflows_dir
        if not workflows_dir.exists():
            return reassigned
        from .definition import WorkflowTask

        for task_path in workflows_dir.glob("*/tasks/*.json"):
            try:
                task = WorkflowTask.from_dict(
                    json.loads(task_path.read_text(encoding="utf-8"))
                )
            except Exception:
                logger.warning("跳过无法读取的 Executor Task: %s", task_path)
                continue
            if task.status not in recoverable or not predicate(task):
                continue
            task.executor_id = current.executor_id
            task.executor_epoch = current.epoch
            self._save_task(task)
            reassigned += 1
        return reassigned

    def reassign_dead_executor_generation(self, previous, current) -> int:
        return self._reassign_executor_tasks(
            lambda task: (
                task.executor_id == previous.executor_id
                and task.executor_epoch != current.epoch
            ),
            current,
        )

    def reassign_stale_executor_generations(self, current) -> int:
        return self._reassign_executor_tasks(
            lambda task: (
                task.executor_id in {None, current.executor_id}
                and task.executor_epoch != current.epoch
            ),
            current,
        )

    def reconcile_executor_pool(self, pool) -> int:
        """Bind recoverable Tasks to live pool members after cold start."""
        current_by_id = {
            identity.executor_id: identity for identity in pool.identities
        }
        recoverable = {
            "pending", "running", "retry_waiting", "resume_pending", "failed",
        }
        reassigned = 0
        workflows_dir = self._execution_control.workflows_dir
        if not workflows_dir.exists():
            return reassigned
        from .definition import WorkflowTask

        for task_path in workflows_dir.glob("*/tasks/*.json"):
            try:
                task = WorkflowTask.from_dict(
                    json.loads(task_path.read_text(encoding="utf-8"))
                )
            except Exception:
                logger.warning("跳过无法读取的 Executor Task: %s", task_path)
                continue
            if task.status not in recoverable or task.status == "pre_running":
                continue
            if task.main_takeover or task.executor_id == "controller":
                if task.executor_id is None:
                    task.executor_id = "controller"
                    task.executor_epoch = "inline"
                    self._save_task(task)
                    reassigned += 1
                continue
            current = current_by_id.get(task.executor_id or "")
            if current is None:
                current = pool.select_client(task.task_id).identity
            if (
                task.executor_id == current.executor_id
                and task.executor_epoch == current.epoch
            ):
                continue
            task.executor_id = current.executor_id
            task.executor_epoch = current.epoch
            self._save_task(task)
            reassigned += 1
        return reassigned

    async def shutdown_running_tasks(self) -> None:
        """Cancel and reap local Task runners before releasing process ownership."""
        runners = [task for task in self._running_tasks.values() if not task.done()]
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)
        self._running_tasks.clear()
