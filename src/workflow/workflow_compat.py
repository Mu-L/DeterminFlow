"""旧版 Workflow 级运行控制 API 的兼容层。"""

from __future__ import annotations

import json

from .definition import WorkflowTask


class WorkflowCompatibilityMixin:
    """把旧 Workflow API 委托到当前 Task 生命周期。"""

    async def run_workflow(
        self,
        workflow_id: str,
        from_node_id: str | None = None,
    ) -> dict:
        """启动工作流运行（兼容旧版 API，内部创建任务后立即启动）。"""
        return await self.create_and_run_task(workflow_id, from_node_id)

    async def stop_workflow(self, workflow_id: str) -> dict:
        """停止工作流中正在执行或等待自动重试的全部任务。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        try:
            tasks_dir = self._resolve_wf_dir(workflow_id) / "tasks"
        except ValueError:
            return {"success": False, "message": "没有正在运行的任务"}

        running_tasks: list[WorkflowTask] = []
        if tasks_dir.exists():
            for task_file in tasks_dir.iterdir():
                if task_file.suffix != ".json":
                    continue
                try:
                    task = WorkflowTask.from_dict(
                        json.loads(task_file.read_text(encoding="utf-8"))
                    )
                except Exception:
                    continue
                if task.status in {"retry_waiting", "resume_pending"} or (
                    task.status == "running"
                    and task.task_id in self._running_tasks
                ):
                    running_tasks.append(task)

        if not running_tasks:
            return {"success": False, "message": "没有正在运行的任务"}

        results = [
            await self.stop_task(workflow_id, task.task_id)
            for task in running_tasks
        ]
        return {
            "success": True,
            "message": f"已停止 {len(results)} 个任务",
            "results": results,
        }

    def get_workflow_status(self, workflow_id: str) -> dict | None:
        """查询工作流当前状态（兼容旧版 API）。"""
        result = self.list_tasks(workflow_id, limit=1)
        tasks = result["tasks"] if isinstance(result, dict) else result
        return tasks[0] if tasks else None

    def get_run_history(
        self,
        workflow_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """获取运行历史；优先读取当前 Task 记录。"""
        result = self.list_tasks(workflow_id, limit=limit)
        tasks = result["tasks"] if isinstance(result, dict) else result
        if tasks:
            return tasks

        try:
            runs_dir = self._resolve_wf_dir(workflow_id) / "runs"
        except ValueError:
            return []
        if not runs_dir.exists():
            return []

        records: list[dict] = []
        for run_file in sorted(runs_dir.iterdir(), reverse=True):
            if run_file.suffix != ".json":
                continue
            try:
                records.append(json.loads(run_file.read_text(encoding="utf-8")))
            except Exception:
                continue
            if len(records) >= limit:
                break
        return records
