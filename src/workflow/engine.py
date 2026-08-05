"""
WorkflowEngine — 工作流执行引擎

负责按拓扑顺序执行工作流中的节点。支持串行、并行（ParallelGateway/ConvergeGateway）
和条件分支/循环（ConditionGateway）。
节点行为由 NodeRegistry 插件体系驱动，引擎本身只做调度。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .definition import (
    WorkflowDef, WorkflowNode, WorkflowState, WorkflowTask,
    NodeExecutionState, WorkflowRunRecord,
    _now_iso, _generate_id,
)
from .nodes import registry, NodeContext, NodeResult
from .execution_flow import WorkflowFlowMixin
from .execution_loop import MAX_LOOP_ITERATIONS, WorkflowLoopMixin
from .failure_policy import (
    begin_node_attempt,
    normalize_node_status,
    record_attempt,
)

if TYPE_CHECKING:
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)

# 全局引擎引用，供节点插件获取审批字典
_current_engine: "WorkflowEngine | None" = None

def get_current_engine() -> "WorkflowEngine | None":
    """获取当前引擎实例（供节点插件和 REST API 使用）。"""
    return _current_engine


class WorkflowEngine(WorkflowFlowMixin, WorkflowLoopMixin):
    """工作流执行引擎（支持串行 + 一层并行）。"""

    def __init__(
        self,
        session_manager: "SessionManager",
        *,
        workflow_environment: Callable[[str], dict[str, str]] | None = None,
    ):
        global _current_engine
        if _current_engine is not None and _current_engine is not self:
            logger.warning(
                "WorkflowEngine 实例被覆盖：已有引擎实例 %s 将被新实例 %s 替换，"
                "get_current_engine() 将返回新实例。这在并发创建多个引擎时可能导致问题。",
                id(_current_engine), id(self),
            )
        _current_engine = self
        self._session_manager = session_manager
        self._workflow_environment = workflow_environment or (lambda _workflow_id: {})
        self._ws_manager = None
        # 审批等待：key = (workflow_id, task_id, node_id)
        self._pending_approvals: dict[tuple[str, str, str], asyncio.Event] = {}
        self._approval_results: dict[tuple[str, str, str], dict] = {}
        # 节点状态锁：并行分支并发写入 node_states 时使用
        self._node_state_lock = asyncio.Lock()
        # 同一任务的检查点必须按调用顺序落盘，避免旧快照覆盖新快照。
        self._task_save_locks: dict[str, asyncio.Lock] = {}
        self._task_update_listener: Callable[[str, WorkflowTask], None] | None = None

    def set_workspace_manager(self, ws_manager):
        self._ws_manager = ws_manager

    def set_task_update_listener(
        self,
        listener: Callable[[str, WorkflowTask], None] | None,
    ) -> None:
        """注册进程内任务变化监听器，仅用于唤醒状态等待者。"""
        self._task_update_listener = listener

    # ============================================================
    # 核心执行方法
    # ============================================================

    async def execute(self, definition: WorkflowDef, state: WorkflowState,
                      from_node_id: str | None = None,
                      pre_created_session_id: str | None = None) -> WorkflowState:
        run_id = _generate_id("run")
        state.status = "running"
        state.current_run_id = run_id
        state.started_at = state.started_at or _now_iso()
        state.current_node_id = None

        execution_order = state.get_execution_order(definition)
        if not execution_order:
            state.status = "completed"; state.completed_at = _now_iso()
            return state

        if from_node_id:
            try:
                idx = execution_order.index(from_node_id)
                execution_order = execution_order[idx:]
            except ValueError:
                pass

        run_record = WorkflowRunRecord(run_id=run_id, workflow_id=definition.workflow_id)

        # 使用预创建的 session 或新建
        if pre_created_session_id and pre_created_session_id in self._session_manager.sessions:
            wf_main_id = pre_created_session_id
        else:
            from src.core.llm_client import create_llm
            wf_main = await self._session_manager.init_workflow_main(
                llm_client=create_llm(streaming=True),
                workflow_id=definition.workflow_id,
                task_description=f"Workflow: {definition.workflow_id}",
            )
            self._session_manager.sessions[wf_main.session_id] = wf_main
            wf_main_id = wf_main.session_id

        shared_ws = self._ws_manager.resolve_workflow_workspace(
            definition.workflow_id,
        )

        try:
            for node_id in execution_order:
                node_def = definition.get_node(node_id)
                if node_def is None: continue
                state.current_node_id = node_id
                node_state = state.node_states.get(node_id, NodeExecutionState(node_id=node_id))
                node_state.status = "running"; node_state.started_at = _now_iso()
                state.node_states[node_id] = node_state
                exec_state = await self._execute_node(definition, node_def, node_state, shared_ws,
                                                       parent_id=wf_main_id, node_states=state.node_states)
                state.node_states[node_id] = exec_state
                run_record.node_executions.append(exec_state)
                if exec_state.status == "failed":
                    state.status = "failed"; state.current_node_id = node_id
                    run_record.status = "failed"; break
            else:
                state.status = "completed"; state.current_node_id = None
                run_record.status = "completed"
        except Exception as e:
            state.status = "failed"; run_record.status = "failed"
            logger.exception(f"工作流 {definition.workflow_id} 执行异常: {e}")

        state.completed_at = _now_iso()
        if wf_main_id in self._session_manager.sessions:
            self._session_manager.sessions[wf_main_id].status = "completed"
            await self._session_manager.sessions[wf_main_id].async_save()
        self._save_run_record(definition.workflow_id, run_record)
        return state

    async def execute_task(self, definition: WorkflowDef, task: WorkflowTask,
                           from_node_id: str | None = None,
                           pre_created_session_id: str | None = None) -> WorkflowTask:
        run_id = _generate_id("run")
        task.status = "running"; task.run_id = run_id
        task.started_at = task.started_at or _now_iso()
        task.current_node_id = None

        # 使用预创建的 session 或新建
        if pre_created_session_id and pre_created_session_id in self._session_manager.sessions:
            wf_main_id = pre_created_session_id
            logger.info(f"复用预创建 main: {wf_main_id} (task={task.task_id})")
        else:
            from src.core.llm_client import create_llm
            wf_main = await self._session_manager.init_workflow_main(
                llm_client=create_llm(streaming=True),
                workflow_id=definition.workflow_id,
                task_description=f"Workflow: {definition.workflow_id}",
            )
            self._session_manager.sessions[wf_main.session_id] = wf_main
            wf_main_id = wf_main.session_id
            logger.info(f"Workflow main 已创建: {wf_main_id} (task={task.task_id})")

        run_record = WorkflowRunRecord(run_id=run_id, workflow_id=definition.workflow_id)
        shared_ws = self._ws_manager.resolve_workflow_workspace(
            definition.workflow_id,
            override=getattr(task, "workspace_override", None),
        )

        def _on_node_started(node_state: NodeExecutionState):
            # 节点启动时不再落盘（仅推送 WS 事件），减少并发文件 I/O
            self._push_wf_task_update(definition.workflow_id, task)

        needs_approval = task.main_takeover
        node_parent_id = (
            task.main_session_id
            if needs_approval and task.main_session_id
            else wf_main_id
        )

        # 批量标记禁用的节点为 skipped
        disabled_ids = set(task.disabled_node_ids or [])
        for node_id in disabled_ids:
            if node_id not in task.node_states:
                task.node_states[node_id] = NodeExecutionState(node_id=node_id)
            task.node_states[node_id].status = "skipped"
            task.node_states[node_id].completed_at = _now_iso()
        if disabled_ids:
            await self._save_task_state(definition.workflow_id, task)
            self._push_wf_task_update(definition.workflow_id, task)

        # 获取执行计划（串行或带并行标记）
        execution_plan = definition.get_execution_plan()

        try:
            # 如果指定了 from_node_id，跳过之前已执行的步骤
            plan_index = 0
            if from_node_id:
                plan_index = self._skip_to_node(execution_plan, from_node_id, disabled_ids)

            while plan_index < len(execution_plan):
                step = execution_plan[plan_index]

                if step["type"] == "parallel_gateway":
                    # 收集后续的 branch 和 converge_gateway 步骤
                    plan_index += 1
                    branches: list[dict] = []
                    converge_step: dict | None = None
                    while plan_index < len(execution_plan):
                        s = execution_plan[plan_index]
                        if s["type"] == "branch":
                            branches.append(s)
                            plan_index += 1
                        elif s["type"] == "converge_gateway":
                            converge_step = s
                            plan_index += 1
                            break
                        else:
                            break

                    # 并行执行所有分支
                    logger.debug(
                        "[ENGINE] 并行网关开始: gateway=%s, 分支数=%d, task=%s",
                        step.get("gateway_id", ""), len(branches), task.task_id,
                    )
                    parallel_result = await self._execute_parallel_branches(
                        definition=definition, task=task,
                        branches=branches, converge_step=converge_step or {},
                        disabled_ids=disabled_ids,
                        shared_ws=shared_ws,
                        parent_id=node_parent_id,
                        on_node_started=_on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )
                    if parallel_result in {"failed", "retry_waiting"}:
                        task.status = parallel_result
                        run_record.status = parallel_result
                        self._push_wf_task_update(definition.workflow_id, task)
                        break

                elif step["type"] == "converge_gateway":
                    # 不应该直接到达这里（已在 parallel_gateway 块中处理）
                    plan_index += 1

                elif step["type"] == "condition_gateway":
                    if step.get("loop"):
                        # 循环调度
                        loop_result = await self._execute_loop(
                            definition=definition, task=task,
                            step=step,
                            disabled_ids=disabled_ids,
                            shared_ws=shared_ws,
                            parent_id=node_parent_id,
                            on_node_started=_on_node_started,
                            needs_approval=needs_approval,
                            run_record=run_record,
                        )
                        if loop_result in {"failed", "retry_waiting"}:
                            task.status = loop_result
                            run_record.status = loop_result
                            self._push_wf_task_update(definition.workflow_id, task)
                            break
                    else:
                        # 分支调度
                        branch_result = await self._evaluate_condition_gateway(
                            definition=definition, task=task,
                            step=step,
                            disabled_ids=disabled_ids,
                            shared_ws=shared_ws,
                            parent_id=node_parent_id,
                            on_node_started=_on_node_started,
                            needs_approval=needs_approval,
                            run_record=run_record,
                        )
                        if branch_result in {"failed", "retry_waiting"}:
                            task.status = branch_result
                            run_record.status = branch_result
                            self._push_wf_task_update(definition.workflow_id, task)
                            break
                    plan_index += 1

                elif step["type"] == "loop_gateway":
                    loop_result = await self._execute_loop_gateway(
                        definition=definition, task=task,
                        step=step, disabled_ids=disabled_ids,
                        shared_ws=shared_ws, parent_id=node_parent_id,
                        on_node_started=_on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )
                    if loop_result in {"failed", "retry_waiting"}:
                        task.status = loop_result
                        run_record.status = loop_result
                        self._push_wf_task_update(definition.workflow_id, task)
                        break
                    plan_index += 1

                elif step["type"] == "node":
                    node_id = step["node_id"]
                    node_def = definition.get_node(node_id)
                    if node_def is None: plan_index += 1; continue

                    if node_id in disabled_ids:
                        plan_index += 1; continue

                    task_seq_result = await self._execute_node_sequence(
                        definition=definition, task=task,
                        node_ids=[node_id],
                        disabled_ids=disabled_ids,
                        shared_ws=shared_ws,
                        parent_id=node_parent_id,
                        on_node_started=_on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )
                    if task_seq_result in {"failed", "retry_waiting"}:
                        task.status = task_seq_result
                        run_record.status = task_seq_result
                        self._push_wf_task_update(definition.workflow_id, task)
                        break
                    plan_index += 1

                else:
                    plan_index += 1
            else:
                task.status = "completed"; task.current_node_id = None
                run_record.status = "completed"
        except Exception as e:
            task.status = "failed"; run_record.status = "failed"
            logger.exception(f"任务 {task.task_id} 执行异常: {e}")
            self._push_wf_task_update(definition.workflow_id, task)

        task.completed_at = None if task.status == "retry_waiting" else _now_iso()
        if wf_main_id in self._session_manager.sessions:
            self._session_manager.sessions[wf_main_id].status = "completed"
            await self._session_manager.sessions[wf_main_id].async_save()
        self._save_run_record(definition.workflow_id, run_record)
        return task

    def _do_save_task_state(
        self,
        workflow_id: str,
        task_id: str,
        task_data: dict,
    ):
        """同步写入任务状态到磁盘（内部方法，由 _save_task_state 在线程池中调用）。

        使用唯一临时文件名避免并行分支写入时的竞态条件：
        每个线程写入自己的独占 .tmp 文件，再原子替换目标文件。
        """
        from src.config import WORKFLOWS_DIR
        import uuid
        tasks_dir = WORKFLOWS_DIR / workflow_id / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_file = tasks_dir / f"{task_id}.json"
        tmp_file = tasks_dir / f"{task_id}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            tmp_file.write_text(
                json.dumps(task_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_file.replace(task_file)
        except (IOError, OSError):
            logger.exception(f"任务状态持久化失败: {task_file}")
            raise

    async def _save_task_state(self, workflow_id: str, task: WorkflowTask):
        """按任务串行持久化不可变快照，避免并行分支发生旧写覆盖。"""
        from src.agent.session import _io_executor
        from .model_utils import _now_iso
        save_lock = self._task_save_locks.setdefault(task.task_id, asyncio.Lock())
        async with save_lock:
            # 在 event loop 中同步快照，线程池只接收不再变化的数据。
            task.updated_at = _now_iso()
            task_data = task.to_dict()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                _io_executor,
                self._do_save_task_state,
                workflow_id,
                task.task_id,
                task_data,
            )
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                # shield 会让外层立刻收到取消；仍需持锁等原子替换完成。
                await asyncio.shield(future)
                raise

    def _push_wf_task_update(self, workflow_id: str, task: WorkflowTask):
        """推送任务状态更新到 events 通道（非阻塞队列投递）。

        注意：EventBus 已重构为纯队列投递（put_nowait），下方 emit_event
        内部无任何 await。create_task 仅用于将调用置于事件循环中运行，
        这是引擎中唯一保留的 create_task 调用（远不如 token 流路径频繁）。
        """
        try:
            if self._task_update_listener is not None:
                self._task_update_listener(workflow_id, task)
            loop = asyncio.get_running_loop()
            from src.web.event_bus import event_bus
            from .runtime_models import _node_state_to_dict
            actions_enabled = task.status in {"failed", "retry_waiting"}
            node_states_dict = {
                nid: _node_state_to_dict(
                    ns,
                    actions_enabled=actions_enabled,
                )
                for nid, ns in task.node_states.items()
            }
            executable_node_ids = {
                node.get("id")
                for node in (task.snapshot_definition or {}).get("nodes", [])
                if node.get("id") and node.get("id") not in task.disabled_node_ids
            }
            terminal_statuses = {"completed", "success", "skipped"}
            completed_nodes = sum(
                1
                for node_id in executable_node_ids
                if task.node_states.get(node_id)
                and task.node_states[node_id].status in terminal_statuses
            )
            payload = {
                "type": "wf_task_update", "workflow_id": workflow_id,
                "task_id": task.task_id, "status": task.status,
                "name": task.name,
                "main_session_id": task.main_session_id,
                "main_takeover": task.main_takeover,
                "current_node_id": task.current_node_id, "node_states": node_states_dict,
                "started_at": task.started_at, "completed_at": task.completed_at,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "workspace_mode": task.workspace_mode,
                "workspace_ref": task.workspace_ref,
                "progress": {
                    "completed": completed_nodes,
                    "total": len(executable_node_ids),
                },
            }
            loop.create_task(event_bus.emit_event(payload))
            if task.main_session_id:
                loop.create_task(event_bus.emit_chat({
                    **payload,
                    "type": "workflow_task_update",
                    "session_id": task.main_session_id,
                }))
        except Exception:
            logger.debug("wf_task_update 事件推送失败", exc_info=True)

    # ============================================================
    # 单节点执行（插件分发）
    # ============================================================

    async def _execute_node(self, definition: WorkflowDef, node_def: WorkflowNode,
                            node_state: NodeExecutionState, shared_ws: Path | None,
                            parent_id: str | None = None,
                            on_node_started: Callable | None = None,
                            parameter_values: dict[str, str] | None = None,
                            node_states: dict[str, NodeExecutionState] | None = None,
                            workflow_id: str = "",
                            task_id: str = "",
                            task_name: str = "",
                            execution_order: list[str] | None = None,
                            node_index: int = 0,
                            needs_approval: bool = False,
                            on_reject_upstream: Callable | None = None,
                            on_node_checkpoint: Callable | None = None) -> NodeExecutionState:
        """分发节点执行 — 根据 node_type 查找插件并调用 execute()。"""
        node_type = node_def.node_type or "agent"
        plugin_cls = registry.get(node_type)
        if plugin_cls is None:
            node_state.status = "failed"
            node_state.error = f"未知节点类型: {node_type}"
            node_state.completed_at = _now_iso()
            return node_state

        # 统一使用共享 workspace 作为主工作目录
        # 不再为 agent 节点创建私有 session workspace
        session_ws_path = str(shared_ws) if shared_ws else ""

        # 首次 Attempt 冻结本节点实际收到的参数和上游摘要。后续自动/人工
        # 重试都复用该快照，避免循环变量、上游输出或系统时间漂移。
        if (
            node_state.attempt_count > 0
            and node_state.next_attempt_trigger != "initial"
        ):
            enriched_pv = deepcopy(node_state.input_snapshot)
            upstream_summary = node_state.upstream_summary_snapshot
        else:
            upstream_summary = self._get_upstream_summary(
                definition, node_def.id, node_states or {},
            )
            enriched_pv = self._enrich_parameter_values(
                parameter_values or {}, definition, node_def.id, node_states or {},
            )
            self._inject_system_variables(
                enriched_pv, definition.workflow_id,
                task_id=task_id, task_name=task_name,
                shared_ws_path=session_ws_path,
            )

        node_state = begin_node_attempt(
            node_state,
            started_at=_now_iso(),
            input_snapshot=enriched_pv,
            upstream_summary_snapshot=upstream_summary,
        )

        async def _checkpoint() -> None:
            if on_node_checkpoint is not None:
                await on_node_checkpoint(node_state)

        # 构建上下文
        ctx = NodeContext(
            definition=definition,
            node_def=node_def,
            node_state=node_state,
            shared_ws=shared_ws,
            session_ws_path=session_ws_path,
            parameter_values=enriched_pv,
            upstream_summary=upstream_summary,
            needs_approval=needs_approval,
            parent_id=parent_id or "",
            workflow_id=workflow_id,
            task_id=task_id,
            execution_order=execution_order or [],
            node_index=node_index,
            session_manager=self._session_manager,
            on_reject_upstream=on_reject_upstream,
            checkpoint=_checkpoint,
            owner_environment=self._workflow_environment(workflow_id),
            workflow_environment=self._workflow_environment,
        )

        plugin = plugin_cls()
        previous_session_id = node_state.session_id
        previous_token_usage = node_state.token_usage
        previous_token_usage_calls = list(node_state.token_usage_calls)
        await _checkpoint()
        if on_node_started:
            try:
                on_node_started(node_state)
            except Exception:
                logger.debug("on_node_started 回调异常: node=%s", node_def.id, exc_info=True)

        try:
            result = await plugin.execute(ctx)
        except Exception as e:
            logger.exception(f"节点 {node_def.id} 执行异常: {e}")
            result = NodeResult(status="failed", error=str(e))

        node_state.completed_at = _now_iso()
        node_state.summary = result.summary
        node_state.status = normalize_node_status(result.status)
        node_state.error = result.error
        node_state.outputs = result.outputs or {}
        node_state.stdout = result.stdout or ""
        node_state.stderr = result.stderr or ""
        result_token_usage = result.token_usage
        result_token_usage_calls = list(result.token_usage_calls)
        if result.session_id:
            session = getattr(self._session_manager, "sessions", {}).get(result.session_id)
            if session and hasattr(session, "get_cumulative_token_usage"):
                current = session.get_cumulative_token_usage()
                if current:
                    result_token_usage = current
            if session and hasattr(session, "get_token_usage_calls"):
                result_token_usage_calls = session.get_token_usage_calls()

        if previous_token_usage and previous_session_id != result.session_id:
            node_state.token_usage = self._merge_token_usage(
                previous_token_usage,
                result_token_usage,
            )
            node_state.token_usage_calls = self._merge_token_usage_calls(
                previous_token_usage_calls,
                result_token_usage_calls,
            )
        else:
            if result_token_usage:
                node_state.token_usage = result_token_usage
            if result_token_usage_calls:
                node_state.token_usage_calls = result_token_usage_calls
        if result.session_id:
            node_state.session_id = result.session_id

        node_state = record_attempt(
            node_state,
            status=node_state.status,
            completed_at=node_state.completed_at,
        )

        # 将节点产出写入运行时变量池（供下游节点引用）
        if node_state.status == "completed" and result.outputs:
            for var_key, var_value in result.outputs.items():
                parameter_values[var_key] = var_value

        return node_state

    @staticmethod
    def _get_max_reject_count(node_def: WorkflowNode) -> int:
        """读取节点最大打回次数，脚本节点可从 node_params 覆盖。"""
        raw = (node_def.node_params or {}).get(
            "max_reject_count",
            getattr(node_def, "max_reject_count", 3),
        )
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 3

    @staticmethod
    def _merge_token_usage(previous: dict | None, current: dict | None) -> dict | None:
        """合并同一节点多次独立 Session 的 Token 用量。"""
        if not previous:
            return current
        if not current:
            return previous

        merged = {model_id: dict(values) for model_id, values in previous.items()}
        for model_id, values in current.items():
            target = merged.setdefault(model_id, {})
            for key, value in values.items():
                if isinstance(value, (int, float)):
                    target[key] = target.get(key, 0) + value
                else:
                    target[key] = value
        return merged

    @staticmethod
    def _merge_token_usage_calls(previous: list[dict], current: list[dict]) -> list[dict]:
        """按稳定 call_id 合并新旧调用账本，保留原有顺序。"""
        merged: list[dict] = []
        seen: set[str] = set()
        for item in [*previous, *current]:
            if not isinstance(item, dict):
                continue
            call_id = str(item.get("call_id", ""))
            if not call_id:
                call_id = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if call_id in seen:
                continue
            seen.add(call_id)
            merged.append(dict(item))
        return merged

    # ============================================================
    # 串行节点序列执行（分支内 / 主线）
    # ============================================================


    @staticmethod
    def _skip_to_node(execution_plan: list[dict], from_node_id: str,
                       disabled_ids: set[str]) -> int:
        """跳过执行计划中 from_node_id 之前的所有步骤，返回起始 plan_index。"""
        for idx, step in enumerate(execution_plan):
            if step["type"] == "node" and step["node_id"] == from_node_id:
                return idx
            if step["type"] == "branch" and from_node_id in step.get("nodes", []):
                return idx
        return 0

    def resolve_approval(self, workflow_id: str, task_id: str,
                          node_id: str, approved: bool, feedback: str = "") -> dict:
        """解决审批等待 — 由 approve_node 工具和审批节点 REST API 共用。"""
        key = (workflow_id, task_id, node_id)
        if key not in self._pending_approvals:
            return {"success": False, "message": f"节点 {node_id} 没有待处理的审批请求"}
        self._approval_results[key] = {"approved": approved, "feedback": feedback}
        self._pending_approvals[key].set()
        action = "通过" if approved else "拒绝"
        return {"success": True, "message": f"节点 {node_id} 已{action}",
                "node_id": node_id, "approved": approved}

    # ============================================================
    # 辅助方法
    # ============================================================

    def _load_task_from_engine(self, workflow_id: str, task_id: str) -> "WorkflowTask | None":
        from src.config import WORKFLOWS_DIR
        task_file = WORKFLOWS_DIR / workflow_id / "tasks" / f"{task_id}.json"
        if not task_file.exists(): return None
        return WorkflowTask.from_dict(json.loads(task_file.read_text(encoding='utf-8')))

    def _get_upstream_summary(self, definition: WorkflowDef, node_id: str,
                               node_states: dict[str, NodeExecutionState]) -> str:
        upstream_ids = [e.source for e in definition.edges if e.target == node_id]
        if not upstream_ids: return ""
        parts = []
        for uid in upstream_ids:
            if uid in node_states:
                u = node_states[uid]
                if u.summary: parts.append(f"节点 [{uid}] 的任务产出：\n{u.summary}")
        return "\n\n".join(parts)

    @staticmethod
    def _enrich_parameter_values(
        base_values: dict[str, str],
        definition: WorkflowDef,
        current_node_id: str,
        node_states: dict[str, NodeExecutionState],
    ) -> dict[str, str]:
        """合并已完成节点的 outputs 到 parameter_values，供当前节点的占位符解析使用。

        严格按 DAG 拓扑序：只收集上游已完成节点的 outputs，
        确保当前节点只能引用已执行完成的节点的产出。
        """
        enriched: dict[str, str] = dict(base_values)  # 复制避免修改原 dict

        # 构建反向邻接表：target → [sources]，用于 BFS 收集上游节点
        rev_adj: dict[str, list[str]] = {}
        for e in definition.edges:
            rev_adj.setdefault(e.target, []).append(e.source)

        # BFS 收集所有上游节点
        upstream_ids: set[str] = set()
        queue = [current_node_id]
        visited: set[str] = {current_node_id}
        while queue:
            node_id = queue.pop(0)
            for source in rev_adj.get(node_id, []):
                if source not in visited:
                    visited.add(source)
                    upstream_ids.add(source)
                    queue.append(source)

        # 合并已完成节点的 outputs
        for nid in upstream_ids:
            state = node_states.get(nid)
            if state and state.status == "completed" and state.outputs:
                for k, v in state.outputs.items():
                    if k and v:
                        enriched[k] = v

        return enriched

    @staticmethod
    def _inject_system_variables(
        values: dict[str, str],
        workflow_id: str,
        task_id: str = "",
        task_name: str = "",
        shared_ws_path: str = "",
    ) -> None:
        """向 parameter_values 注入系统变量（_system.xxx）。

        _system.current_time 每次调用动态计算（每个节点执行时刻不同）。
        其他变量（workspace_path、workflow_id 等）在任务创建时固定。
        """
        from datetime import datetime, timezone, timedelta

        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)

        system_vars = {
            "_system.workspace_path": shared_ws_path,
            "_system.workflow_id": workflow_id,
            "_system.task_id": task_id,
            "_system.task_name": task_name,
            "_system.current_time": now.isoformat(),
            "_system.operator": "system",
        }
        for k, v in system_vars.items():
            values[k] = v

    def _push_node_status(self, workflow_id: str, node_id: str, session_id: str,
                           summary: str, status: str, error: str):
        """推送节点状态事件 — 委托给 nodes/base.py 公共函数。"""
        from src.workflow.nodes.base import push_node_status
        push_node_status(workflow_id, node_id, session_id, summary, status, error)

    def _save_run_record(self, workflow_id: str, record: WorkflowRunRecord):
        from src.config import WORKFLOWS_DIR
        runs_dir = WORKFLOWS_DIR / workflow_id / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_file = runs_dir / f"{record.run_id}.json"
        tmp_file = run_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(str(tmp_file), str(run_file))
        except (IOError, OSError) as e:
            logger.error(f"保存运行记录 {record.run_id} 失败: {e}")
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise
