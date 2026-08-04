"""WorkflowManager 的 Main 接管任务创建职责。"""

from __future__ import annotations

import logging
from datetime import datetime

from .definition import WorkflowDef, WorkflowTask, _now_iso
from .runtime_models import NodeExecutionState

logger = logging.getLogger(f"{__package__}.manager")


class WorkflowMainTaskCreationMixin:
    """提供预启动与现有 Main Session 绑定的任务创建入口。"""

    async def pre_start_task(
        self,
        workflow_id: str,
        workspace_override: str | None = None,
        main_takeover: bool = False,
    ) -> dict:
        """预启动工作流：创建 pending task + workspace + workflow-main session。

        返回 task_id 和 session_id，前端随后可以与 main 对话填参。
        """
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return {"success": False, "message": f"工作流 {workflow_id} 不存在"}

        definition = WorkflowDef.from_dict(wf_data["definition"])
        # 确保并行/汇聚网关配对正确
        pairing_errors = definition.auto_pair_gateways()
        if pairing_errors:
            logger.warning(f"网关配对警告 (workflow={workflow_id}): {pairing_errors}")
        default_name = (
            f"{definition.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        # 工作空间覆盖
        ws_override = workspace_override.strip() if workspace_override else None

        # 1. 创建 pending task
        def_dict = definition.to_dict()
        initial_parameter_values = {
            variable.key: variable.default
            for variable in definition.variables
            if variable.default
        }
        try:
            self._freeze_snapshot_definition(
                workflow_id,
                def_dict,
                initial_parameter_values,
            )
        except Exception as exc:
            logger.exception(
                "冻结 Workflow Task 运行身份失败: workflow=%s",
                workflow_id,
            )
            return {"success": False, "message": str(exc)}
        task = WorkflowTask(
            workflow_id=workflow_id,
            name=default_name,
            status="pending",
            created_at=_now_iso(),
            snapshot_definition=def_dict,
            parameter_values=initial_parameter_values,
            snapshot_variables=definition.to_dict().get("variables", []),
            workspace_override=ws_override,
            main_takeover=main_takeover,
        )
        wf_dir = self._resolve_wf_dir(workflow_id)
        tasks_dir = wf_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        self._save_task(task)

        # 2. 创建工作区目录
        if ws_override:
            self._ws_manager.resolve_workflow_workspace(
                workflow_id,
                override=ws_override,
            )
        else:
            self._ws_manager.create_workflow_workspace(workflow_id)

        # 3. 创建 workflow-main session（含 workflow 信息注入）
        from src.core.llm_client import create_llm

        session = await self._session_manager.init_workflow_main_for_pre_start(
            llm_client=create_llm(streaming=True),
            workflow_id=workflow_id,
            task_id=task.task_id,
            definition=definition,
            parameter_values=task.parameter_values,
        )
        self._session_manager.sessions[session.session_id] = session

        # 4. 关联 task 与 main session
        task.status = "pre_running"
        task.main_session_id = session.session_id
        self._save_task(task)

        logger.info(f"预启动完成: task={task.task_id}, main={session.session_id}")

        return {
            "success": True,
            "task_id": task.task_id,
            "session_id": session.session_id,
            "main_takeover": task.main_takeover,
            "message": f"Main 会话已就绪 (session={session.session_id})",
        }

    def create_and_attach_task_for_session(
        self,
        workflow_id: str,
        session_id: str,
        parameter_values: dict[str, str] | None = None,
        scheme_id: str | None = None,
        selected_node_ids: list[str] | None = None,
        workspace_mode: str = "task_isolated",
        workspace_ref: str | None = None,
        main_takeover: bool = False,
    ) -> dict:
        """为已有的 Chat Main 创建可独立寻址的 pre_running task。

        与 pre_start_task 的区别：
        - 不复用 pre_start_task（它会创建新 workflow-main session）
        - 直接创建 task、设置 main_session_id
        - main_takeover 默认关闭，仅显式开启时逐 Agent 节点审批
        - session.workflow_id/task_id 只记录最近任务，作为省略 TaskRef 时的兼容默认值
        - 默认使用任务隔离工作空间；可通过安全名称在同一 Main 下显式共享
        - session 对象通过 session_id 从 _session_manager 获取
        """
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return {"success": False, "message": f"工作流 {workflow_id} 不存在"}

        definition = WorkflowDef.from_dict(wf_data["definition"])
        # 确保并行/汇聚网关配对正确
        pairing_errors = definition.auto_pair_gateways()
        if pairing_errors:
            logger.warning(f"网关配对警告 (workflow={workflow_id}): {pairing_errors}")
        default_name = (
            f"{definition.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        session = self._session_manager.sessions.get(session_id)
        if session is None:
            return {"success": False, "message": f"会话 {session_id} 不存在"}

        if workspace_mode not in {"task_isolated", "named_shared", "legacy_shared"}:
            return {
                "success": False,
                "error": "workspace_mode_invalid",
                "message": f"不支持的工作空间模式: {workspace_mode}",
            }
        if workspace_mode == "named_shared" and not workspace_ref:
            return {
                "success": False,
                "error": "workspace_ref_required",
                "message": "named_shared 模式必须提供 workspace_ref",
            }

        # 1. 创建 pre_running task
        task_parameter_values = {
            variable.key: variable.default
            for variable in definition.variables
            if variable.default
        }
        if parameter_values:
            known_parameter_keys = {variable.key for variable in definition.variables}
            unknown_parameter_keys = sorted(
                set(parameter_values) - known_parameter_keys
            )
            if unknown_parameter_keys:
                return {
                    "success": False,
                    "error": "workflow_parameter_unknown",
                    "message": (
                        "未知 Workflow 参数: "
                        + ", ".join(unknown_parameter_keys)
                    ),
                }
            task_parameter_values.update(parameter_values)
        final_disabled: list[str] = []
        resolved_scheme_id: str | None = None
        if selected_node_ids is not None:
            executable_node_ids = {
                node.id
                for node in definition.nodes
                if not (node.id.startswith("__") and node.id.endswith("__"))
            }
            unknown_node_ids = sorted(
                set(selected_node_ids) - executable_node_ids
            )
            if unknown_node_ids:
                return {
                    "success": False,
                    "error": "workflow_node_unknown",
                    "message": "未知 Workflow 节点: " + ", ".join(unknown_node_ids),
                }
            final_disabled = sorted(self._resolve_disabled_nodes(
                definition,
                selected_node_ids=selected_node_ids,
            ))
        elif scheme_id:
            if not any(
                scheme.id == scheme_id
                for scheme in definition.execution_schemes
            ):
                return {
                    "success": False,
                    "error": "workflow_scheme_not_found",
                    "message": f"Workflow 执行方案不存在: {scheme_id}",
                }
            final_disabled = sorted(self._resolve_disabled_nodes(
                definition,
                scheme_id=scheme_id,
            ))
            resolved_scheme_id = scheme_id

        def_dict = definition.to_dict()
        try:
            self._freeze_snapshot_definition(
                workflow_id,
                def_dict,
                task_parameter_values,
            )
        except Exception as exc:
            logger.exception(
                "冻结 Workflow Task 运行身份失败: workflow=%s",
                workflow_id,
            )
            return {"success": False, "message": str(exc)}

        task = WorkflowTask(
            workflow_id=workflow_id,
            name=default_name,
            status="pre_running",
            created_at=_now_iso(),
            snapshot_definition=def_dict,
            parameter_values=task_parameter_values,
            snapshot_variables=def_dict.get("variables", []),
            main_session_id=session_id,
            main_takeover=main_takeover,
            disabled_node_ids=final_disabled,
            scheme_id=resolved_scheme_id,
            workspace_mode=workspace_mode,
            workspace_ref=workspace_ref,
        )

        for node_id in final_disabled:
            task.node_states[node_id] = NodeExecutionState(
                node_id=node_id,
                status="skipped",
                completed_at=_now_iso(),
            )

        try:
            if workspace_mode == "legacy_shared":
                workspace = self._ws_manager.create_workflow_workspace(workflow_id)
            else:
                workspace = self._ws_manager.create_main_task_workspace(
                    session_id,
                    task.task_id,
                    mode=workspace_mode,
                    workspace_ref=workspace_ref,
                )
            task.workspace_override = str(workspace)
        except ValueError as exc:
            return {
                "success": False,
                "error": "workspace_invalid",
                "message": str(exc),
            }

        wf_dir = self._resolve_wf_dir(workflow_id)
        tasks_dir = wf_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        self._save_task(task)

        session.workflow_id = workflow_id
        session.task_id = task.task_id
        self._push_task_update(workflow_id, task)

        logger.info(
            f"Chat main 已绑定工作流: session={session_id}, "
            f"workflow={workflow_id}, task={task.task_id}"
        )

        return {
            "success": True,
            "task_id": task.task_id,
            "workflow_id": workflow_id,
            "workspace_mode": task.workspace_mode,
            "workspace_ref": task.workspace_ref,
            "main_takeover": task.main_takeover,
            "selected_node_ids": selected_node_ids,
            "message": (
                f"已创建工作流任务 {task.task_id}。"
                f"变量: {', '.join(v.key for v in definition.variables) if definition.variables else '无'}。"
                f"后续修改和启动请显式携带 workflow_id 与 task_id。"
            ),
        }
