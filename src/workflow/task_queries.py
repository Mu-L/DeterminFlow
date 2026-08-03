"""WorkflowManager 的任务与历史查询职责。"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path

from src.config import WORKFLOWS_DIR

from .definition import WorkflowDef, WorkflowTask

logger = logging.getLogger(f"{__package__}.manager")


class TaskQueryMixin:
    """提供任务详情、历史列表和节点消息等只读查询。"""

    def get_task(self, workflow_id: str, task_id: str) -> dict | None:
        """获取任务运行状态。"""
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None
        return task.to_dict()

    def get_task_with_definition(self, workflow_id: str, task_id: str) -> dict | None:
        """获取任务状态 + 工作流定义快照。

        优先使用任务自身的 snapshot_definition，确保历史任务不受后续编辑影响。
        仅当旧任务缺失快照时才回退到当前定义。
        """
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None

        if task.snapshot_definition:
            # 使用任务创建时的快照
            definition = task.snapshot_definition
        else:
            # 兼容旧数据：直接读取当前定义。历史任务即使所属扩展已禁用也可查看。
            try:
                def_file = self._resolve_wf_dir(workflow_id) / "definition.json"
                definition = WorkflowDef.from_dict(
                    json.loads(def_file.read_text(encoding="utf-8"))
                ).to_dict()
            except (ValueError, OSError, json.JSONDecodeError):
                return None

        return {
            "task": task.to_dict(),
            "definition": definition,
        }

    @staticmethod
    def _apply_task_filters_sort_paginate(
        all_tasks: list[dict],
        status: str = "",
        search: str = "",
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 20,
        extra_search_fields: list[str] | None = None,
    ) -> dict:
        """对任务列表应用状态过滤、搜索、排序和分页。

        Args:
            all_tasks: 原始任务字典列表
            status: 状态过滤（空字符串表示全部）
            search: 搜索关键词（匹配 name、task_id 及 extra_search_fields）
            sort_by: 排序字段
            sort_order: asc 或 desc
            page: 页码（从 1 开始）
            page_size: 每页条数
            extra_search_fields: 额外搜索字段（如 ["workflow_name", "workflow_id"]）

        Returns:
            {"tasks": [...], "total": N, "page": P, "page_size": S}
        """
        if status:
            all_tasks = [task for task in all_tasks if task.get("status") == status]

        if search:
            search_lower = search.lower()
            search_fields = ["name", "task_id"] + (extra_search_fields or [])
            all_tasks = [
                task
                for task in all_tasks
                if any(
                    search_lower in (task.get(field, "") or "").lower()
                    for field in search_fields
                )
            ]

        reverse = sort_order == "desc"
        status_order = {
            "running": 0,
            "pending": 1,
            "failed": 2,
            "completed": 3,
            "stopped": 4,
        }
        if sort_by == "created_at":
            all_tasks.sort(key=lambda task: task.get("created_at", ""), reverse=reverse)
        elif sort_by == "updated_at":
            all_tasks.sort(
                key=lambda task: task.get("updated_at") or task.get("created_at", ""),
                reverse=reverse,
            )
        elif sort_by == "started_at":
            all_tasks.sort(
                key=lambda task: task.get("started_at") or "", reverse=reverse
            )
        elif sort_by == "completed_at":
            all_tasks.sort(
                key=lambda task: task.get("completed_at") or "", reverse=reverse
            )
        elif sort_by == "status":
            all_tasks.sort(
                key=lambda task: status_order.get(task.get("status", ""), 99),
                reverse=reverse,
            )
        elif sort_by in ("name", "workflow_name"):
            all_tasks.sort(
                key=lambda task: (task.get(sort_by, "") or "").lower(),
                reverse=reverse,
            )
        else:
            all_tasks.sort(
                key=lambda task: task.get("created_at", ""), reverse=True
            )

        total = len(all_tasks)
        start = (page - 1) * page_size
        end = start + page_size
        paged_tasks = all_tasks[start:end] if start < total else []

        return {
            "tasks": paged_tasks,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_tasks(
        self,
        workflow_id: str,
        limit: int = 50,
        status: str = "",
        search: str = "",
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 20,
        main_session_id: str = "",
    ) -> dict:
        """列出工作流的所有任务，支持筛选、排序、搜索和分页。

        不返回 snapshot_definition 以保持列表轻量。
        """
        try:
            wf_dir = self._resolve_wf_dir(workflow_id)
        except ValueError:
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}
        tasks_dir = wf_dir / "tasks"
        if not tasks_dir.exists():
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}

        all_tasks: list[dict] = []
        for task_file in tasks_dir.iterdir():
            if task_file.suffix != ".json":
                continue
            try:
                task = WorkflowTask.from_dict(
                    json.loads(task_file.read_text(encoding="utf-8"))
                )
                task_data = task.to_dict()
                if main_session_id and task.main_session_id != main_session_id:
                    continue
                task_data["progress"] = self.build_task_progress(task)
                task_data.pop("snapshot_definition", None)
                all_tasks.append(task_data)
            except Exception:
                logger.exception(f"加载任务失败: {task_file.name}")

        return self._apply_task_filters_sort_paginate(
            all_tasks, status, search, sort_by, sort_order, page, page_size
        )

    def list_all_tasks(
        self,
        status: str = "",
        search: str = "",
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 20,
        workflow_id: str = "",
        main_session_id: str = "",
    ) -> dict:
        """列出全部工作流的任务，支持筛选、排序、搜索和分页。"""
        if not WORKFLOWS_DIR.exists():
            return {"tasks": [], "total": 0, "page": page, "page_size": page_size}

        workflow_names: dict[str, str] = {}
        for workflow_dir in WORKFLOWS_DIR.iterdir():
            if not workflow_dir.is_dir():
                continue
            definition_file = workflow_dir / "definition.json"
            if definition_file.exists():
                try:
                    workflow_data = json.loads(
                        definition_file.read_text(encoding="utf-8")
                    )
                    workflow_names[workflow_dir.name] = workflow_data.get("name", "")
                except Exception:
                    pass

        all_tasks: list[dict] = []
        for workflow_dir in WORKFLOWS_DIR.iterdir():
            if not workflow_dir.is_dir():
                continue
            current_workflow_id = workflow_dir.name
            if workflow_id and current_workflow_id != workflow_id:
                continue

            tasks_dir = workflow_dir / "tasks"
            if not tasks_dir.exists():
                continue

            workflow_name = workflow_names.get(current_workflow_id, "")
            for task_file in tasks_dir.iterdir():
                if task_file.suffix != ".json":
                    continue
                try:
                    task = WorkflowTask.from_dict(
                        json.loads(task_file.read_text(encoding="utf-8"))
                    )
                    task_data = task.to_dict()
                    if main_session_id and task.main_session_id != main_session_id:
                        continue
                    task_data["progress"] = self.build_task_progress(task)
                    task_data.pop("snapshot_definition", None)
                    task_data["workflow_name"] = workflow_name
                    all_tasks.append(task_data)
                except Exception:
                    logger.exception(f"加载任务失败: {task_file}")

        return self._apply_task_filters_sort_paginate(
            all_tasks,
            status,
            search,
            sort_by,
            sort_order,
            page,
            page_size,
            extra_search_fields=["workflow_name", "workflow_id"],
        )

    @staticmethod
    def build_task_progress(task: WorkflowTask) -> dict:
        """基于任务快照计算稳定的节点进度。"""
        nodes = (task.snapshot_definition or {}).get("nodes", [])
        executable_ids = {
            node.get("id")
            for node in nodes
            if node.get("id") and node.get("id") not in task.disabled_node_ids
        }
        completed_statuses = {"completed", "success", "skipped"}
        completed = sum(
            1
            for node_id in executable_ids
            if task.node_states.get(node_id)
            and task.node_states[node_id].status in completed_statuses
        )
        return {"completed": completed, "total": len(executable_ids)}

    def get_task_result(self, workflow_id: str, task_id: str) -> dict | None:
        """返回任务的稳定结果摘要与工作空间内的产物描述符。"""
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None

        terminal = task.status in {
            "completed", "failed", "stopped", "cancelled",
        }
        workspace = self._ws_manager.resolve_workflow_workspace(
            workflow_id,
            override=task.workspace_override,
        ).resolve()
        artifacts: list[dict] = []
        artifact_paths: set[Path] = set()
        nodes: dict[str, dict] = {}
        for node_id, state in task.node_states.items():
            public_outputs = {
                key: value
                for key, value in state.outputs.items()
                if not key.startswith("_")
            }
            nodes[node_id] = {
                "status": state.status,
                "summary": state.summary,
                "error": state.error,
                "outputs": public_outputs,
                "attempt_count": state.attempt_count,
                "token_usage": state.token_usage,
            }
            for output_key in ("_output_file", "_json_output_file"):
                raw_path = state.outputs.get(output_key)
                if not raw_path:
                    continue
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = workspace / candidate
                resolved = candidate.resolve()
                try:
                    relative_path = resolved.relative_to(workspace)
                except ValueError:
                    logger.warning(
                        "忽略工作空间外的 Workflow 产物: task=%s node=%s path=%s",
                        task_id,
                        node_id,
                        resolved,
                    )
                    continue
                if resolved in artifact_paths:
                    continue
                artifact_paths.add(resolved)
                exists = resolved.is_file()
                artifacts.append({
                    "artifact_ref": f"{task_id}:{node_id}:{output_key[1:]}",
                    "node_id": node_id,
                    "kind": "file",
                    "label": resolved.name,
                    "relative_path": str(relative_path),
                    "mime_type": mimetypes.guess_type(resolved.name)[0]
                    or "application/octet-stream",
                    "size_bytes": resolved.stat().st_size if exists else 0,
                    "exists": exists,
                })

        return {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "name": task.name,
            "status": task.status,
            "terminal": terminal,
            "progress": self.build_task_progress(task),
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "updated_at": task.updated_at,
            "nodes": nodes,
            "artifacts": artifacts,
        }

    def read_task_artifact(
        self,
        workflow_id: str,
        task_id: str,
        artifact_ref: str,
        *,
        offset: int = 0,
        limit: int = 20_000,
    ) -> dict | None:
        """按 get_task_result 返回的引用读取受控文本产物。"""
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None
        result = self.get_task_result(workflow_id, task_id)
        assert result is not None
        artifact = next(
            (
                item for item in result["artifacts"]
                if item["artifact_ref"] == artifact_ref
            ),
            None,
        )
        if artifact is None:
            return {
                "success": False,
                "error": "artifact_not_found",
                "message": f"任务中不存在产物引用 {artifact_ref}",
            }
        if not artifact["exists"]:
            return {
                "success": False,
                "error": "artifact_missing",
                "message": f"产物文件不存在: {artifact['relative_path']}",
            }
        if artifact["size_bytes"] > 10_000_000:
            return {
                "success": False,
                "error": "artifact_too_large",
                "message": "文本产物超过 10 MB 读取上限",
                "artifact": artifact,
            }
        workspace = self._ws_manager.resolve_workflow_workspace(
            workflow_id,
            override=task.workspace_override,
        ).resolve()
        artifact_path = (workspace / artifact["relative_path"]).resolve()
        try:
            artifact_path.relative_to(workspace)
        except ValueError:
            return {
                "success": False,
                "error": "artifact_path_invalid",
                "message": "产物路径已逃逸任务工作空间",
            }
        safe_offset = max(0, offset)
        safe_limit = max(1, min(limit, 50_000))
        try:
            content = artifact_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": "artifact_decode_failed",
                "message": "产物不是 UTF-8 文本",
            }
        excerpt = content[safe_offset:safe_offset + safe_limit]
        return {
            "success": True,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "artifact": artifact,
            "content": excerpt,
            "offset": safe_offset,
            "returned_chars": len(excerpt),
            "total_chars": len(content),
            "truncated": safe_offset + len(excerpt) < len(content),
        }

    def get_node_messages(
        self, workflow_id: str, task_id: str, node_id: str
    ) -> dict | None:
        """获取任务中某个节点的 Agent 会话消息。"""
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return None

        node_state = task.node_states.get(node_id)
        if node_state is None or not node_state.session_id:
            return {
                "node_id": node_id,
                "session_id": "",
                "messages": [],
                "message_count": 0,
                "node_status": node_state.status if node_state else "unknown",
            }

        session = self._session_manager.get_session(node_state.session_id)
        if session is None:
            return {
                "node_id": node_id,
                "session_id": node_state.session_id,
                "messages": [],
                "message_count": 0,
                "node_status": node_state.status,
                "error": "会话不存在或已清理",
            }

        from src.core.utils import is_visible_to_frontend

        visible_messages = [
            message for message in session.record if is_visible_to_frontend(message)
        ]
        return {
            "node_id": node_id,
            "session_id": node_state.session_id,
            "messages": visible_messages,
            "message_count": len(visible_messages),
            "node_status": node_state.status,
            "summary": node_state.summary,
            "error": node_state.error,
            "agent_type": session.agent_type,
        }
