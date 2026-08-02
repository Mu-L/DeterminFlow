"""WorkflowManager 的 Main 接管任务创建职责。"""

from __future__ import annotations

import logging
from datetime import datetime

from .definition import WorkflowDef, WorkflowTask, _now_iso

logger = logging.getLogger(f"{__package__}.manager")


class WorkflowMainTaskCreationMixin:
    """提供预启动与现有 Main Session 绑定的任务创建入口。"""

    async def pre_start_task(
        self,
        workflow_id: str,
        workspace_override: str | None = None,
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
            "message": f"Main 会话已就绪 (session={session.session_id})",
        }

    def create_and_attach_task_for_session(
        self,
        workflow_id: str,
        session_id: str,
        parameter_values: dict[str, str] | None = None,
    ) -> dict:
        """为已有的 chat main session 创建 pre_running task 并绑定。

        与 pre_start_task 的区别：
        - 不复用 pre_start_task（它会创建新 workflow-main session）
        - 直接创建 task、绑定 session.workflow_id/task_id、设置 main_session_id
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

        # 1. 创建 pre_running task
        task_parameter_values = {
            variable.key: variable.default
            for variable in definition.variables
            if variable.default
        }
        if parameter_values:
            task_parameter_values.update(parameter_values)
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
        )

        wf_dir = self._resolve_wf_dir(workflow_id)
        tasks_dir = wf_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        self._save_task(task)

        # 2. 创建工作区目录
        self._ws_manager.create_workflow_workspace(workflow_id)

        # 3. 绑定到现有 session
        session = self._session_manager.sessions.get(session_id)
        if session is None:
            return {"success": False, "message": f"会话 {session_id} 不存在"}

        session.workflow_id = workflow_id
        session.task_id = task.task_id

        logger.info(
            f"Chat main 已绑定工作流: session={session_id}, "
            f"workflow={workflow_id}, task={task.task_id}"
        )

        return {
            "success": True,
            "task_id": task.task_id,
            "workflow_id": workflow_id,
            "message": (
                f"已创建并绑定工作流任务 {task.task_id}。"
                f"变量: {', '.join(v.key for v in definition.variables) if definition.variables else '无'}。"
                f"请先用 set_workflow_variable 填充必要变量，再调用 start_workflow_task 启动。"
            ),
        }
