"""
WorkflowManager — 工作流生命周期管理器

负责工作流定义的 CRUD、任务创建/运行/停止、状态查询和历史记录管理。

编辑与运行分离架构：
- 工作流定义 (definition.json) 是可编辑的模板
- 任务实例 (tasks/{task_id}.json) 是独立的运行实例，同一工作流可同时运行多个任务
- 运行记录 (runs/{run_id}.json) 是每次运行的完整记录
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import WORKFLOWS_DIR
from src.core.change_broadcaster import ChangeBroadcaster
from .definition import (
    WorkflowDef, WorkflowState, WorkflowTask, NodeExecutionState,
    WorkflowRunRecord, WorkflowVariable, ExecutionScheme, _now_iso, _generate_id,
)
from .engine import WorkflowEngine
from .main_task_creation import WorkflowMainTaskCreationMixin
from .task_queries import TaskQueryMixin
from .task_recovery import WorkflowTaskRecoveryMixin
from .workflow_compat import WorkflowCompatibilityMixin
from src.core.workspace_manager import WorkspaceManager

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)


class WorkflowManager(
    WorkflowMainTaskCreationMixin,
    WorkflowTaskRecoveryMixin,
    WorkflowCompatibilityMixin,
    TaskQueryMixin,
):
    """管理工作流定义、任务执行、状态与运行记录。"""

    def __init__(self, session_manager: "SessionManager", extension_manager=None):
        self._session_manager = session_manager
        self._extension_manager = extension_manager
        self._ws_manager = WorkspaceManager()
        workflow_environment = (
            extension_manager.workflow_environment
            if extension_manager is not None
            else None
        )
        self._engine = WorkflowEngine(
            session_manager,
            workflow_environment=workflow_environment,
        )
        self._task_changes = ChangeBroadcaster()
        self._engine.set_task_update_listener(self._signal_task_update)
        self._engine.set_workspace_manager(self._ws_manager)
        self._running_tasks: dict[str, asyncio.Task] = {}  # key: task_id
        self._init_task_recovery()
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _task_change_key(workflow_id: str, task_id: str) -> str:
        return f"{workflow_id}\0{task_id}"

    def _signal_task_update(self, workflow_id: str, task: WorkflowTask) -> None:
        broadcaster = getattr(self, "_task_changes", None)
        if broadcaster is not None:
            broadcaster.publish(self._task_change_key(workflow_id, task.task_id))

    async def wait_for_task_update(
        self,
        workflow_id: str,
        task_id: str,
        timeout_seconds: float | None,
    ) -> bool:
        """等待任务一次更新；返回后调用方必须重读持久化快照。"""
        return await self._task_changes.wait(
            self._task_change_key(workflow_id, task_id),
            timeout_seconds,
        )

    def _freeze_snapshot_definition(
        self,
        workflow_id: str,
        definition: dict,
        parameter_values: dict | None,
    ) -> dict:
        """Bind a Task snapshot to the process inputs it will execute."""
        resolver = self._get_effective_agent_resolver()
        from .runtime_guards import freeze_workflow_runtime_guards

        return freeze_workflow_runtime_guards(
            definition,
            workflow_dir=self._resolve_wf_dir(workflow_id),
            parameter_values=parameter_values,
            effective_agent_resolver=resolver,
        )

    def _get_effective_agent_resolver(self):
        resolver = getattr(
            self._session_manager,
            "get_effective_agent_definition",
            None,
        )
        if not callable(resolver):
            def missing_resolver(*_args, **_kwargs):
                raise RuntimeError(
                    "SessionManager 未提供 Agent 运行身份解析器，"
                    "拒绝创建含 Agent 节点的 Workflow Task"
                )
            return missing_resolver
        return resolver

    def _refresh_snapshot_agent_guards(
        self,
        definition: dict,
        parameter_values: dict,
    ) -> dict:
        from .runtime_guards import refresh_agent_runtime_guards

        return refresh_agent_runtime_guards(
            definition,
            parameter_values=parameter_values,
            effective_agent_resolver=self._get_effective_agent_resolver(),
        )

    def is_workflow_owner_enabled(self, workflow_id: str) -> bool:
        """扩展拥有的工作流仅在对应扩展运行时允许写入。"""
        try:
            wf_dir = self._resolve_wf_dir(workflow_id)
        except ValueError:
            return False
        return (
            self._extension_manager is None
            or self._extension_manager.workflow_owner_enabled(wf_dir)
        )

    @staticmethod
    def _workflow_read_only_result(workflow_id: str) -> dict:
        return {
            "success": False,
            "message": (
                f"工作流 {workflow_id} 所属扩展未处于运行状态，"
                "当前仅允许读取历史任务"
            ),
        }

    # ============================================================
    # 工作流定义 CRUD
    # ============================================================

    def list_workflows(self) -> list[dict]:
        """列出所有工作流定义（概要）。"""
        workflows = []
        if not WORKFLOWS_DIR.exists():
            return workflows
        for wf_dir in sorted(WORKFLOWS_DIR.iterdir()):
            if not wf_dir.is_dir():
                continue
            if not self.is_workflow_owner_enabled(wf_dir.name):
                continue
            def_file = wf_dir / "definition.json"
            if not def_file.exists():
                continue
            try:
                wf_def = WorkflowDef.from_dict(json.loads(def_file.read_text(encoding='utf-8')))
                # 统计该工作流下运行中的任务数（预构建 running set，遍历目录）
                running_ids = {tid for tid, t in self._running_tasks.items() if not t.done()}
                tasks_dir = wf_dir / "tasks"
                running_task_count = 0
                if tasks_dir.exists():
                    for f in tasks_dir.iterdir():
                        if f.stem in running_ids:
                            running_task_count += 1
                workflows.append({
                    "workflow_id": wf_def.workflow_id,
                    "name": wf_def.name,
                    "node_count": len(wf_def.nodes),
                    "version": wf_def.version,
                    "created_at": wf_def.created_at,
                    "updated_at": wf_def.updated_at,
                    "status": "running" if running_task_count > 0 else "idle",
                    "running_tasks": running_task_count,
                })
            except Exception:
                logger.exception(f"加载工作流定义失败: {wf_dir.name}")
        return workflows

    def get_workflow(self, workflow_id: str) -> dict | None:
        """获取单个工作流的完整定义。"""
        try:
            wf_dir = self._resolve_wf_dir(workflow_id)
        except ValueError:
            return None
        def_file = wf_dir / "definition.json"
        if not def_file.exists():
            return None
        if not self.is_workflow_owner_enabled(workflow_id):
            return None
        try:
            wf_def = WorkflowDef.from_dict(json.loads(def_file.read_text(encoding='utf-8')))
            return {"definition": wf_def.to_dict()}
        except Exception:
            logger.exception(f"加载工作流失败: {workflow_id}")
            return None

    def get_workflow_execution_identity(self, workflow_id: str) -> dict | None:
        """Return hashes for the actual inline scripts Core would execute."""
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return None
        from .runtime_guards import build_workflow_execution_identity

        return build_workflow_execution_identity(
            workflow["definition"],
            workflow_dir=self._resolve_wf_dir(workflow_id),
        )

    def get_workflow_execution_policy(self, workflow_id: str) -> str:
        """Read policy without collapsing disabled/corrupt definitions to missing."""
        try:
            definition_path = self._resolve_wf_dir(workflow_id) / "definition.json"
        except ValueError:
            return "invalid"
        if not definition_path.exists():
            return "not_found"
        try:
            raw = json.loads(definition_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("读取 Workflow 执行策略失败: %s", workflow_id)
            return "unavailable"
        policy = raw.get("http_execution_policy")
        if policy in (None, ""):
            return "public"
        return policy if policy in {"public", "internal_only"} else "invalid"
    def create_workflow(self, data: dict) -> dict:
        """创建新工作流定义。"""
        wf_def = WorkflowDef.from_dict(data)
        wf_def.workflow_id = data.get("workflow_id", _generate_id("wf"))
        if not self.is_workflow_owner_enabled(wf_def.workflow_id):
            return self._workflow_read_only_result(wf_def.workflow_id)
        wf_def.version = 1
        # 自动配对并行/汇聚网关（如有）
        wf_def.auto_pair_gateways()

        wf_dir = self._resolve_wf_dir(wf_def.workflow_id)
        if wf_dir.exists():
            return {
                "success": False,
                "error": "workflow_already_exists",
                "message": f"工作流 {wf_def.workflow_id} 已存在",
            }
        # 确保 workflow 目录存在
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "tasks").mkdir(exist_ok=True)
        (wf_dir / "runs").mkdir(exist_ok=True)
        (wf_dir / "script").mkdir(exist_ok=True)

        # 保存定义
        self._save_definition(wf_def)

        # 创建 workspace 目录结构
        self._ws_manager.create_workflow_workspace(wf_def.workflow_id)

        logger.info(f"工作流已创建: {wf_def.workflow_id} [{wf_def.name}]")
        return {"definition": wf_def.to_dict()}

    def update_workflow(self, workflow_id: str, data: dict) -> dict | None:
        """更新工作流定义。"""
        existing = self.get_workflow(workflow_id)
        if not existing:
            return None

        wf_def = WorkflowDef.from_dict(data)
        wf_def.workflow_id = workflow_id
        old_version = existing["definition"].get("version", 1)
        if existing["definition"].get("http_execution_policy") == "internal_only":
            wf_def.http_execution_policy = "internal_only"
        try:
            old_version = int(old_version)
        except (ValueError, TypeError):
            old_version = 0
        wf_def.version = old_version + 1
        wf_def.updated_at = _now_iso()
        # 自动配对并行/汇聚网关
        wf_def.auto_pair_gateways()

        self._save_definition(wf_def)
        logger.info(f"工作流已更新: {workflow_id} v{wf_def.version}")
        return {"definition": wf_def.to_dict()}

    def validate_workflow(self, data: dict) -> dict:
        """校验工作流定义的连线完整性（不保存）。"""
        try:
            wf_def = WorkflowDef.from_dict(data)
            # 自动配对并行/汇聚网关
            pairing_errors = wf_def.auto_pair_gateways()
            errors = wf_def.validate()
            all_errors = errors + pairing_errors
            return {"valid": len(all_errors) == 0, "errors": all_errors, "workflow_id": data.get("workflow_id", "")}
        except Exception as e:
            logger.exception("校验工作流定义失败")
            return {"valid": False, "errors": [str(e)], "workflow_id": ""}

    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流定义和 workspace。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return False
        try:
            wf_dir = self._resolve_wf_dir(workflow_id)
        except ValueError:
            return False
        if not wf_dir.exists():
            return False

        # 清理 workspace
        self._ws_manager.cleanup_workflow_workspace(workflow_id)

        # 删除定义目录
        try:
            shutil.rmtree(wf_dir)
        except OSError as e:
            logger.error(f"删除工作流目录失败 {wf_dir}: {e}")
            raise
        logger.info(f"工作流已删除: {workflow_id}")
        return True

    # ============================================================
    # 执行方案管理
    # ============================================================

    def get_schemes(self, workflow_id: str) -> list[dict] | None:
        """获取工作流的所有执行方案列表。"""
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return None
        wf_def = WorkflowDef.from_dict(wf_data["definition"])
        return [s.to_dict() for s in wf_def.execution_schemes]

    def create_scheme(self, workflow_id: str, name: str,
                       selected_node_ids: list[str]) -> dict | None:
        """创建新的执行方案（持久化到 definition.json）。"""
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return None
        wf_def = WorkflowDef.from_dict(wf_data["definition"])

        # 校验选中节点 ID 是否有效
        valid_ids = {n.id for n in wf_def.nodes}
        invalid_ids = [nid for nid in selected_node_ids if nid not in valid_ids]
        if invalid_ids:
            raise ValueError(f"以下节点不存在: {', '.join(invalid_ids)}")

        scheme = ExecutionScheme(name=name, selected_node_ids=selected_node_ids)
        wf_def.execution_schemes.append(scheme)
        wf_def.bump_version()
        self._save_definition(wf_def)
        logger.info(f"执行方案已创建: {scheme.id} [{scheme.name}] (wf={workflow_id})")
        return scheme.to_dict()

    def update_scheme(self, workflow_id: str, scheme_id: str,
                       name: str | None = None,
                       selected_node_ids: list[str] | None = None) -> dict | None:
        """更新执行方案（名称或选中节点列表）。"""
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return None
        wf_def = WorkflowDef.from_dict(wf_data["definition"])

        scheme = next((s for s in wf_def.execution_schemes if s.id == scheme_id), None)
        if scheme is None:
            raise ValueError(f"执行方案不存在: {scheme_id}")

        if name is not None:
            scheme.name = name
        if selected_node_ids is not None:
            valid_ids = {n.id for n in wf_def.nodes}
            invalid_ids = [nid for nid in selected_node_ids if nid not in valid_ids]
            if invalid_ids:
                raise ValueError(f"以下节点不存在: {', '.join(invalid_ids)}")
            scheme.selected_node_ids = selected_node_ids

        scheme.updated_at = _now_iso()
        wf_def.bump_version()
        self._save_definition(wf_def)
        logger.info(f"执行方案已更新: {scheme_id} (wf={workflow_id})")
        return scheme.to_dict()

    def delete_scheme(self, workflow_id: str, scheme_id: str) -> bool:
        """删除执行方案。"""
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return False
        wf_def = WorkflowDef.from_dict(wf_data["definition"])

        before_count = len(wf_def.execution_schemes)
        wf_def.execution_schemes = [s for s in wf_def.execution_schemes if s.id != scheme_id]
        if len(wf_def.execution_schemes) == before_count:
            return False  # 方案不存在，视为删除失败

        wf_def.bump_version()
        self._save_definition(wf_def)
        logger.info(f"执行方案已删除: {scheme_id} (wf={workflow_id})")
        return True

    def _resolve_disabled_nodes(self, definition: WorkflowDef,
                                  scheme_id: str | None = None,
                                  selected_node_ids: list[str] | None = None) -> set[str]:
        """根据方案 ID 或选中节点列表，计算出需要禁用的节点 ID 集合。

        逻辑: disabled_node_ids = 全量可执行节点 - 选中节点
        起点/终点标记和不包括在 nodes 中的网关 ID 永远不会被禁用。
        """
        all_executable = {n.id for n in definition.nodes
                          if not (n.id.startswith("__") and n.id.endswith("__"))}

        if selected_node_ids is not None:
            selected_set = set(selected_node_ids) & all_executable
            return all_executable - selected_set

        if scheme_id:
            scheme = next((s for s in definition.execution_schemes if s.id == scheme_id), None)
            if scheme:
                selected_set = set(scheme.selected_node_ids) & all_executable
                return all_executable - selected_set

        return set()  # 不选方案也不传列表 → 全部执行

    # ============================================================
    # 任务管理（编辑与运行分离）
    # ============================================================

    def create_task(self, workflow_id: str, from_node_id: str | None = None,
                    parameter_values: dict[str, str] | None = None,
                    disabled_node_ids: list[str] | None = None,
                    workspace_override: str | None = None,
                    scheme_id: str | None = None,
                    selected_node_ids: list[str] | None = None) -> dict | None:
        """从工作流定义创建一个新任务实例（不启动，仅创建）。

        创建时保存当前工作流定义的快照，确保任务不受后续编辑影响（bk-sops 模式）。
        同时保存用户填写的参数值和变量定义快照。

        Args:
            workspace_override: 用户指定的工作空间覆盖路径（空则使用默认路径）
            scheme_id: 执行方案 ID（与 selected_node_ids 互斥，优先 selected_node_ids）
            selected_node_ids: 直接指定的选中节点列表（与 scheme_id 互斥，优先此项）

        Returns:
            包含 task_id 的字典，失败返回 None
        """
        if not self.is_workflow_owner_enabled(workflow_id):
            return None
        wf_data = self.get_workflow(workflow_id)
        if not wf_data:
            return None

        definition = WorkflowDef.from_dict(wf_data["definition"])
        # 确保并行/汇聚网关配对正确后再保存快照
        pairing_errors = definition.auto_pair_gateways()
        if pairing_errors:
            logger.warning(f"网关配对警告 (workflow={workflow_id}): {pairing_errors}")
        # 生成默认任务名称: workflow名 + 时间戳
        from datetime import datetime
        default_name = f"{definition.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        def_dict = definition.to_dict()  # 只序列化一次，后续复用
        try:
            self._freeze_snapshot_definition(
                workflow_id,
                def_dict,
                parameter_values,
            )
        except Exception:
            logger.exception(
                "冻结 Workflow Task 运行身份失败: workflow=%s",
                workflow_id,
            )
            return None

        # 解析 disabled_node_ids：scheme_id / selected_node_ids / disabled_node_ids 三选一
        final_disabled: list[str] = []
        resolved_scheme_id: str | None = None
        if selected_node_ids is not None:
            final_disabled = sorted(self._resolve_disabled_nodes(
                definition, selected_node_ids=selected_node_ids))
        elif scheme_id:
            final_disabled = sorted(self._resolve_disabled_nodes(
                definition, scheme_id=scheme_id))
            resolved_scheme_id = scheme_id
        elif disabled_node_ids is not None:
            final_disabled = list(disabled_node_ids)

        # 工作空间覆盖：保留原始用户输入，引擎运行时解析
        ws_override = workspace_override.strip() if workspace_override else None

        task = WorkflowTask(
            workflow_id=workflow_id,
            name=default_name,
            status="pending",
            created_at=_now_iso(),
            snapshot_definition=def_dict,  # 保存配对后的定义快照
            parameter_values=parameter_values or {},
            snapshot_variables=def_dict.get("variables", []),
            disabled_node_ids=final_disabled,
            scheme_id=resolved_scheme_id,
            workspace_override=ws_override,
        )

        # 预先标记被禁用的节点为 skipped，确保任务加载时就反映正确状态
        for nid in final_disabled:
            task.node_states[nid] = NodeExecutionState(
                node_id=nid, status="skipped", completed_at=_now_iso(),
            )

        # 确保 tasks 目录存在
        wf_dir = self._resolve_wf_dir(workflow_id)
        tasks_dir = wf_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        self._save_task(task)
        logger.info(f"任务已创建: {task.task_id} (工作流: {workflow_id})")
        return {
            "task_id": task.task_id,
            "workflow_id": workflow_id,
            "status": "pending",
            "definition": wf_data["definition"],
        }

    async def run_task(self, workflow_id: str, task_id: str,
                       from_node_id: str | None = None) -> dict:
        """启动一个已创建的任务。

        Args:
            workflow_id: 工作流 ID
            task_id: 任务 ID
            from_node_id: 从指定节点开始（用于回滚/重试）
        """
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return {"success": False, "message": f"任务 {task_id} 不存在"}

        if task.status not in {"pending", "resume_pending"}:
            return {
                "success": False,
                "error": "task_state_conflict",
                "message": (
                    f"任务状态为 {task.status}；仅待启动任务可以启动。"
                    "失败节点请使用节点重试或跳过操作"
                ),
            }

        if task.main_takeover and task.main_session_id not in self._session_manager.sessions:
            return {
                "success": False,
                "error": "main_takeover_unavailable",
                "message": "Main 接管审批不可用：任务所属 Main Session 不在运行期",
            }

        if task_id in self._running_tasks:
            t = self._running_tasks[task_id]
            if not t.done():
                return {"success": False, "message": "任务已在运行中"}

        # 优先使用任务快照定义，回退到当前定义
        if task.snapshot_definition:
            definition = WorkflowDef.from_dict(task.snapshot_definition)
        else:
            wf_data = self.get_workflow(workflow_id)
            if not wf_data:
                return {"success": False, "message": f"工作流 {workflow_id} 不存在"}
            definition = WorkflowDef.from_dict(wf_data["definition"])

        # 确保并行/汇聚网关配对正确（快照或文件可能丢失 converge_gateway_id）
        pairing_errors = definition.auto_pair_gateways()
        if pairing_errors:
            logger.warning(f"网关配对警告 (task={task_id}): {pairing_errors}")

        # 局部重跑使用新的节点状态对象，避免上一轮的 Session、
        # 跳过标记、控制计数、子节点和迭代状态泄漏到新一轮。只保留
        # 累计 Token 账本与结构化打回审计；执行引擎会按 call_id 合并账本。
        # 旧的“从节点重跑”是一轮新的局部执行，不能沿用上一轮冻结的
        # 条件选择或循环游标；精确失败恢复由 retry_node API 负责。
        if from_node_id:
            task.control_flow_state = {}
        if from_node_id and task.node_states:
            execution_order = task.get_execution_order(definition)
            try:
                idx = execution_order.index(from_node_id)
                for nid in execution_order[idx:]:
                    state = task.node_states.get(nid)
                    task.node_states[nid] = NodeExecutionState(
                        node_id=nid,
                        token_usage=deepcopy(state.token_usage) if state else None,
                        token_usage_calls=(
                            deepcopy(state.token_usage_calls) if state else []
                        ),
                        rejection_history=(
                            deepcopy(state.rejection_history) if state else []
                        ),
                        iteration_history=(
                            deepcopy(state.iteration_history) if state else []
                        ),
                    )
            except ValueError:
                pass

        task.status = "running"
        task.started_at = _now_iso()
        task.completed_at = None
        task.current_node_id = None
        task.run_id = None
        self._save_task(task)
        self._push_task_update(workflow_id, task)

        # 异步启动
        coro = asyncio.create_task(
            self._run_task_coroutine(workflow_id, task_id, definition, task, from_node_id)
        )
        self._running_tasks[task_id] = coro

        return {
            "success": True,
            "message": "任务已启动",
            "task_id": task_id,
            "workflow_id": workflow_id,
        }

    async def create_and_run_task(self, workflow_id: str,
                                   from_node_id: str | None = None,
                                   parameter_values: dict[str, str] | None = None) -> dict:
        """创建并立即启动任务（便捷方法，兼容旧的 run_workflow 行为）。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        result = self.create_task(workflow_id, from_node_id, parameter_values=parameter_values)
        if result is None:
            return {"success": False, "message": f"工作流 {workflow_id} 不存在"}
        return await self.run_task(workflow_id, result["task_id"], from_node_id)

    async def stop_task(self, workflow_id: str, task_id: str) -> dict:
        """停止正在运行的任务。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        async with self._task_control_lock(task_id):
            coro = self._running_tasks.get(task_id)
            if coro is None:
                task = self._load_task(workflow_id, task_id)
                if task and task.status == "pre_running":
                    task.status = "stopped"
                    task.current_node_id = None
                    task.completed_at = _now_iso()
                    self._save_task(task)
                    self._push_task_update(workflow_id, task)
                    return {
                        "success": True,
                        "message": "预启动任务已停止",
                        "task_id": task_id,
                    }
                if task and task.status == "pending":
                    self._get_task_path(workflow_id, task_id).unlink(missing_ok=True)
                    logger.info("未启动任务已丢弃: %s", task_id)
                    return {
                        "success": True, "message": "未启动任务已丢弃",
                        "task_id": task_id,
                    }
                if task and task.status in {
                    "running", "retry_waiting", "resume_pending",
                }:
                    self._cancel_retry_timer(task_id)
                    task.status = "stopped"
                    task.current_node_id = None
                    task.completed_at = _now_iso()
                    self._save_task(task)
                    self._push_task_update(workflow_id, task)
                    logger.info(
                        "无本地执行器的任务已停止: %s (工作流: %s)",
                        task_id,
                        workflow_id,
                    )
                    return {
                        "success": True,
                        "message": "进程重启遗留任务已停止",
                        "task_id": task_id,
                    }
                return {"success": False, "message": "任务未在运行"}

            if coro.done():
                self._running_tasks.pop(task_id, None)
                return {"success": True, "message": "任务已完成"}

            self._cancel_retry_timer(task_id)
            coro.cancel()
            try:
                await coro
            except asyncio.CancelledError:
                pass

            self._running_tasks.pop(task_id, None)

            task = self._load_task(workflow_id, task_id)
            if task and task.status not in {
                "completed", "failed", "stopped", "cancelled",
            }:
                task.status = "stopped"
                task.completed_at = _now_iso()
                self._save_task(task)
                self._push_task_update(workflow_id, task)

            logger.info(f"任务已停止: {task_id} (工作流: {workflow_id})")
            return {"success": True, "message": "任务已停止", "task_id": task_id}

    # ============================================================
    # 内部方法
    # ============================================================

    async def _run_task_coroutine(self, workflow_id: str, task_id: str,
                                   definition: WorkflowDef, task: WorkflowTask,
                                   from_node_id: str | None):
        """后台运行任务的协程。"""
        result_task: WorkflowTask | None = None
        try:
            if task.main_takeover and task.main_session_id not in self._session_manager.sessions:
                raise RuntimeError(
                    "Main 接管审批不可用：任务所属 Main Session 不在运行期"
                )
            result_task = await self._engine.execute_task(
                definition,
                task,
                from_node_id,
            )
            self._save_task(result_task)
            self._push_task_update(workflow_id, result_task)
        except asyncio.CancelledError:
            task.status = "stopped"
            task.completed_at = _now_iso()
            self._save_task(task)
            self._push_task_update(workflow_id, task)
            raise
        except Exception:
            logger.exception(f"任务 {task_id} (工作流 {workflow_id}) 运行异常")
            task.status = "failed"
            task.completed_at = _now_iso()
            self._save_task(task)
            self._push_task_update(workflow_id, task)
        finally:
            self._running_tasks.pop(task_id, None)
            if result_task is not None and result_task.status == "retry_waiting":
                self._schedule_retry_for_task(result_task)

    def _save_definition(self, wf_def: WorkflowDef):
        """持久化工作流定义（原子写入，防崩溃损坏）。"""
        def_file = self._resolve_wf_dir(wf_def.workflow_id) / "definition.json"
        def_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = def_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(json.dumps(wf_def.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            tmp_file.replace(def_file)
        except (IOError, OSError):
            logger.exception(f"工作流定义持久化失败: {def_file}")
            raise

    def _save_task(self, task: WorkflowTask):
        """持久化任务状态（原子写入，防崩溃损坏）。"""
        task.updated_at = _now_iso()
        task_file = self._get_task_path(task.workflow_id, task.task_id)
        task_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = task_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            tmp_file.replace(task_file)
        except (IOError, OSError):
            logger.exception(f"任务状态持久化失败: {task_file}")
            raise

    def _push_task_update(self, workflow_id: str, task: WorkflowTask) -> None:
        """在引擎已初始化时推送任务快照，保留轻量测试与迁移兼容性。"""
        self._signal_task_update(workflow_id, task)
        engine = getattr(self, "_engine", None)
        push_update = getattr(engine, "_push_wf_task_update", None)
        if callable(push_update):
            push_update(workflow_id, task)

    def _load_task(self, workflow_id: str, task_id: str) -> WorkflowTask | None:
        """从磁盘加载任务。"""
        task_file = self._get_task_path(workflow_id, task_id)
        if not task_file.exists():
            return None
        try:
            task = WorkflowTask.from_dict(
                json.loads(task_file.read_text(encoding="utf-8"))
            )
            if task.workflow_id != workflow_id or task.task_id != task_id:
                logger.error(
                    "任务文件身份与请求路径不一致: expected=%s/%s actual=%s/%s",
                    workflow_id,
                    task_id,
                    task.workflow_id,
                    task.task_id,
                )
                return None
            return task
        except Exception:
            logger.exception(f"加载任务失败: {task_id}")
            return None

    @staticmethod
    def _sanitize_id(value: str) -> str:
        """清理 ID，仅保留字母数字下划线短横线，防止路径穿越。"""
        return re.sub(r"[^a-zA-Z0-9_\-]", "", value)

    def _resolve_wf_dir(self, workflow_id: str) -> Path:
        """获取工作流目录路径（带路径穿越防护）。

        所有使用 WORKFLOWS_DIR / workflow_id 的位置应统一调用此方法。
        """
        safe_id = self._sanitize_id(workflow_id)
        if not safe_id:
            raise ValueError(f"无效的 workflow_id: {workflow_id!r}")
        path = WORKFLOWS_DIR / safe_id
        if not path.resolve().is_relative_to(WORKFLOWS_DIR.resolve()):
            raise ValueError(f"工作流路径逃逸出工作流目录: {path}")
        return path

    def _get_task_path(self, workflow_id: str, task_id: str) -> Path:
        """获取任务文件路径（带路径穿越防护）。"""
        safe_wf = self._sanitize_id(workflow_id)
        safe_task = self._sanitize_id(task_id)
        if not safe_wf or not safe_task:
            raise ValueError(f"无效的 workflow_id 或 task_id: {workflow_id!r}, {task_id!r}")
        path = WORKFLOWS_DIR / safe_wf / "tasks" / f"{safe_task}.json"
        if not path.resolve().is_relative_to(WORKFLOWS_DIR.resolve()):
            raise ValueError(f"任务路径逃逸出工作流目录: {path}")
        return path

    # ============================================================
    # Public Task 操作 API
    # ============================================================

    def update_task_variables(self, workflow_id: str, task_id: str, parameter_values: dict) -> bool:
        """更新任务的全局变量参数值（公开 API，替代外部直接调用 _load_task/_save_task）。

        Returns:
            True if updated successfully, False if task not found.
        """
        if not self.is_workflow_owner_enabled(workflow_id):
            return False
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return False
        if task.status in {"pending", "pre_running"} and task.snapshot_definition:
            candidate_definition = deepcopy(task.snapshot_definition)
            try:
                self._refresh_snapshot_agent_guards(
                    candidate_definition,
                    parameter_values,
                )
            except Exception:
                logger.exception(
                    "更新 Task Agent 运行身份失败: task=%s",
                    task_id,
                )
                return False
            task.snapshot_definition = candidate_definition
        task.parameter_values = parameter_values
        self._save_task(task)
        return True

    # ============================================================
    # Main 接管模式 API
    # ============================================================

    def set_workflow_variable(self, workflow_id: str, task_id: str,
                               key: str, value: str, session_id: str = "") -> dict:
        """修改任务的全局变量值，并推送 WebSocket 事件到前端。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        task = self._load_task(workflow_id, task_id)
        if not task:
            return {"success": False, "message": f"任务 {task_id} 不存在"}

        # 检查变量是否存在
        snapshot_vars = task.snapshot_variables or []
        var_defs = [WorkflowVariable.from_dict(v) for v in snapshot_vars]
        known_keys = {v.key for v in var_defs}
        if key not in known_keys:
            return {"success": False, "message": f"未知变量: {key}，可用变量: {', '.join(sorted(known_keys))}"}

        # 更新参数值；pre-running 阶段同时重新冻结动态 Agent/model 输入，
        # inline script 的创建时哈希保持不变。
        candidate_values = dict(task.parameter_values)
        candidate_values[key] = value
        if task.status in {"pending", "pre_running"} and task.snapshot_definition:
            candidate_definition = deepcopy(task.snapshot_definition)
            try:
                self._refresh_snapshot_agent_guards(
                    candidate_definition,
                    candidate_values,
                )
            except Exception as exc:
                logger.exception(
                    "更新 Task Agent 运行身份失败: task=%s",
                    task_id,
                )
                return {"success": False, "message": str(exc)}
            task.snapshot_definition = candidate_definition
        task.parameter_values = candidate_values
        self._save_task(task)

        # 推送 WebSocket 事件
        try:
            from src.web.event_bus import event_bus
            asyncio.create_task(event_bus.emit_chat({
                "type": "wf_variable_update",
                "workflow_id": workflow_id,
                "task_id": task_id,
                "key": key,
                "value": value,
                "session_id": session_id,
            }))
        except Exception:
            logger.exception("推送 wf_variable_update 事件失败")

        return {"success": True, "key": key, "value": value,
                "message": f"变量 {key} 已更新为 \"{value}\""}

    async def start_pre_running_task(self, workflow_id: str, task_id: str) -> dict:
        """从预启动状态正式启动任务执行。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        task = self._load_task(workflow_id, task_id)
        if task is None:
            return {"success": False, "message": f"任务 {task_id} 不存在"}
        if task.status != "pre_running":
            return {"success": False, "message": f"任务状态为 {task.status}，不是预启动状态"}
        if task.main_takeover and task.main_session_id not in self._session_manager.sessions:
            return {
                "success": False,
                "error": "main_takeover_unavailable",
                "message": "Main 接管审批不可用：任务所属 Main Session 不在运行期",
            }
        if task_id in self._running_tasks and not self._running_tasks[task_id].done():
            return {"success": False, "message": "任务已在运行中"}

        # 使用快照定义
        if task.snapshot_definition:
            definition = WorkflowDef.from_dict(task.snapshot_definition)
        else:
            wf_data = self.get_workflow(workflow_id)
            if not wf_data:
                return {"success": False, "message": f"工作流 {workflow_id} 不存在"}
            definition = WorkflowDef.from_dict(wf_data["definition"])

        # 确保并行/汇聚网关配对正确
        pairing_errors = definition.auto_pair_gateways()
        if pairing_errors:
            logger.warning(f"网关配对警告 (task={task_id}): {pairing_errors}")

        task.status = "running"
        task.started_at = _now_iso()
        self._save_task(task)
        self._push_task_update(workflow_id, task)

        # 使用已有的 main session
        pre_created_session_id = task.main_session_id

        coro = asyncio.create_task(
            self._run_task_with_session(workflow_id, task_id, definition, task,
                                         pre_created_session_id)
        )
        self._running_tasks[task_id] = coro

        logger.info(f"预启动任务已正式启动: {task_id} (main={pre_created_session_id})")
        return {"success": True, "message": "任务已启动", "task_id": task_id}

    async def _run_task_with_session(self, workflow_id: str, task_id: str,
                                      definition: WorkflowDef, task: WorkflowTask,
                                      pre_created_session_id: str):
        """后台运行任务（使用预创建的 main session）。"""
        result_task: WorkflowTask | None = None
        try:
            result_task = await self._engine.execute_task(
                definition, task, pre_created_session_id=pre_created_session_id,
            )
            self._save_task(result_task)
            self._push_task_update(workflow_id, result_task)
        except asyncio.CancelledError:
            task.status = "stopped"; task.completed_at = _now_iso(); self._save_task(task)
            self._push_task_update(workflow_id, task)
            raise
        except Exception:
            logger.exception(f"任务 {task_id} 运行异常")
            task.status = "failed"; task.completed_at = _now_iso(); self._save_task(task)
            self._push_task_update(workflow_id, task)
        finally:
            self._running_tasks.pop(task_id, None)
            if result_task is not None and result_task.status == "retry_waiting":
                self._schedule_retry_for_task(result_task)

    def approve_node(self, workflow_id: str, task_id: str,
                      node_id: str, approved: bool, feedback: str = "",
                      expected_attempt_count: int | None = None) -> dict:
        """审批节点完成结果。由 approve_node 工具调用。"""
        if not self.is_workflow_owner_enabled(workflow_id):
            return self._workflow_read_only_result(workflow_id)
        if expected_attempt_count is not None:
            task = self._load_task(workflow_id, task_id)
            if task is None:
                return {
                    "success": False,
                    "error": "task_not_found",
                    "message": f"任务 {task_id} 不存在",
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "node_id": node_id,
                }
            state = task.node_states.get(node_id)
            if state is None:
                return {
                    "success": False,
                    "error": "node_not_found",
                    "message": f"任务中不存在节点 {node_id}",
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "node_id": node_id,
                }
            if state.attempt_count != expected_attempt_count:
                return {
                    "success": False,
                    "error": "node_control_stale",
                    "message": (
                        f"节点 attempt_count 已变为 {state.attempt_count}，"
                        f"请求值为 {expected_attempt_count}"
                    ),
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "node_id": node_id,
                    "attempt_count": state.attempt_count,
                }
        engine = self._engine
        result = engine.resolve_approval(
            workflow_id=workflow_id, task_id=task_id,
            node_id=node_id, approved=approved, feedback=feedback,
        )
        return {
            **result,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "node_id": node_id,
        }
