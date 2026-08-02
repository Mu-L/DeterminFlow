"""工作流循环网关与循环体调度。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from pathlib import Path
from typing import Callable

from .condition_parser import evaluate_condition
from .definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowRunRecord,
    WorkflowTask,
    _try_parse_json,
    parse_loop_expression,
    resolve_placeholders,
)

logger = logging.getLogger(f"{__package__}.engine")

MAX_LOOP_ITERATIONS = 100


class WorkflowLoopMixin:
    """为 WorkflowEngine 提供循环调度。"""

    @staticmethod
    def _task_control_flow_state(task: WorkflowTask) -> dict:
        state = getattr(task, "control_flow_state", None)
        if not isinstance(state, dict):
            state = {}
            setattr(task, "control_flow_state", state)
        return state

    async def _execute_loop(
        self, definition: WorkflowDef, task: WorkflowTask,
        step: dict, disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """执行循环：重复运行 loop_body_nodes，每次迭代后评估条件网关节点的继续分支。

        Returns:
            "completed" | "failed"
        """
        MAX_ITERATIONS = MAX_LOOP_ITERATIONS
        loop_body_nodes = step.get("loop_body_nodes", [])
        continue_branch = step.get("continue_branch", {})
        exit_branch = step.get("exit_branch", {})
        gateway_id = step.get("gateway_id", "")
        loop_states = self._task_control_flow_state(task).setdefault("loops", {})
        cursor = loop_states.setdefault(gateway_id, {
            "type": "condition_loop",
            "next_iteration": 1,
            "active_iteration": None,
            "phase": "ready",
        })

        if not loop_body_nodes:
            logger.error("循环体为空")
            return "failed"

        if cursor.get("phase") == "completed":
            iteration = int(cursor.get("next_iteration", 1)) - 1
        else:
            active_iteration = cursor.get("active_iteration")
            iteration = (
                int(active_iteration)
                if isinstance(active_iteration, int)
                else int(cursor.get("next_iteration", 1))
            )
        while cursor.get("phase") != "completed" and iteration <= MAX_ITERATIONS:
            logger.info(f"循环迭代 #{iteration} (task={task.task_id})")

            if cursor.get("active_iteration") != iteration:
                for nid in loop_body_nodes:
                    state = task.node_states.setdefault(
                        nid, NodeExecutionState(node_id=nid),
                    )
                    await self._reset_node_for_loop_iteration(state, iteration)
            cursor.update({
                "active_iteration": iteration,
                "phase": "running",
            })
            loop_states[gateway_id] = cursor
            await self._save_task_state(definition.workflow_id, task)

            # 执行循环体内所有节点（串行）
            seq_result = await self._execute_node_sequence(
                definition=definition, task=task,
                node_ids=loop_body_nodes, disabled_ids=disabled_ids,
                shared_ws=shared_ws, parent_id=parent_id,
                on_node_started=on_node_started,
                needs_approval=needs_approval,
                run_record=run_record,
            )
            if seq_result in {"failed", "retry_waiting"}:
                cursor["phase"] = seq_result
                loop_states[gateway_id] = cursor
                await self._save_task_state(definition.workflow_id, task)
                logger.error(
                    "循环迭代未完成: iteration=%s status=%s",
                    iteration, seq_result,
                )
                return seq_result

            for nid in loop_body_nodes:
                if nid in task.node_states:
                    self._snapshot_node_for_iteration(
                        task.node_states[nid], iteration, task.parameter_values,
                    )

            # 评估是否继续循环
            if not continue_branch:
                logger.warning("循环无继续分支，退出")
                cursor["phase"] = "completed"
                break

            cond = continue_branch.get("condition")
            if not cond or cond.get("is_default"):
                cursor["phase"] = "completed"
                break

            expression = cond.get("expression", "")
            if not expression.strip():
                cursor["phase"] = "completed"
                break

            # 收集本轮最新的变量池
            variable_pool = dict(task.parameter_values or {})
            for nid, ns in task.node_states.items():
                if ns.status == "completed" and ns.outputs:
                    for k, v in ns.outputs.items():
                        if k and v:
                            variable_pool[k] = v

            try:
                resolved = await resolve_placeholders(expression, variable_pool)
                if not evaluate_condition(resolved):
                    logger.info(f"循环条件不满足，退出 (expr={expression}, resolved={resolved})")
                    cursor["phase"] = "completed"
                    break
            except Exception as e:
                logger.error(f"循环条件评估失败: {e}")
                cursor["phase"] = "completed"
                break

            cursor.update({
                "active_iteration": None,
                "next_iteration": iteration + 1,
                "phase": "ready",
            })
            loop_states[gateway_id] = cursor
            await self._save_task_state(definition.workflow_id, task)
            self._push_wf_task_update(definition.workflow_id, task)
            iteration += 1

        if iteration > MAX_ITERATIONS:
            logger.warning(
                f"循环达到最大迭代次数 {MAX_ITERATIONS}，强制退出 "
                f"(task={task.task_id})"
            )
            cursor["phase"] = "completed"

        cursor["active_iteration"] = None
        cursor["next_iteration"] = iteration + 1
        loop_states[gateway_id] = cursor
        await self._save_task_state(definition.workflow_id, task)

        # 执行退出分支的第一个节点
        if exit_branch:
            exit_target = exit_branch.get("target", "")
            if exit_target and exit_target not in disabled_ids:
                node_def = definition.get_node(exit_target)
                if node_def:
                    return await self._execute_node_sequence(
                        definition=definition, task=task,
                        node_ids=[exit_target], disabled_ids=disabled_ids,
                        shared_ws=shared_ws, parent_id=parent_id,
                        on_node_started=on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )
        return "completed"

    def _snapshot_node_for_iteration(
        self, node_state: NodeExecutionState, iteration: int,
        parameter_values: dict[str, str],
    ):
        """将节点当前状态快照推入 iteration_history。"""
        snapshot = {
            "iteration": iteration,
            "status": node_state.status,
            "summary": node_state.summary,
            "outputs": dict(node_state.outputs),
            "started_at": node_state.started_at,
            "completed_at": node_state.completed_at,
            "error": node_state.error,
            "rejection_count": node_state.rejection_count,
            "rejection_reason": node_state.rejection_reason,
            "reject_upstream_count": node_state.reject_upstream_count,
            "rejection_history": [
                dict(item) for item in node_state.rejection_history
            ],
        }
        node_state.iteration_history.append(snapshot)

    @staticmethod
    def _reset_rejection_state_for_iteration(
        node_state: NodeExecutionState,
    ) -> None:
        """Give each loop item an independent validation/retry budget."""
        node_state.rejection_count = 0
        node_state.rejection_reason = ""
        node_state.reject_upstream_count = 0
        node_state.rejection_history = []

    async def _reset_node_for_loop_iteration(
        self,
        node_state: NodeExecutionState,
        iteration: int,
    ) -> None:
        """开始新迭代；保留累计 Attempt/Token 账本，清除本轮瞬态。"""
        await self._cleanup_loop_session(node_state, iteration)
        node_state.status = "pending"
        node_state.started_at = None
        node_state.completed_at = None
        node_state.summary = ""
        node_state.error = ""
        node_state.session_id = ""
        node_state.outputs = {}
        node_state.stdout = ""
        node_state.stderr = ""
        node_state.child_states = {}
        node_state.is_skipped = False
        node_state.automatic_retry_count = 0
        node_state.next_retry_at = None
        node_state.input_snapshot = {}
        node_state.upstream_summary_snapshot = ""
        node_state.next_attempt_trigger = "initial"
        self._reset_rejection_state_for_iteration(node_state)

    async def _cleanup_loop_session(
        self,
        node_state: NodeExecutionState,
        iteration: int,
    ) -> None:
        """Persist and detach the prior non-main session before a loop reset.

        The completion callback can wake the workflow before the sub-session's
        ``finally`` block performs its last save.  Waiting for that task first
        prevents us from unregistering a dirty session before its history has
        reached disk.
        """
        session_id = node_state.session_id
        if not session_id or session_id == self._session_manager.main_session_id:
            return

        sub_tasks = getattr(self._session_manager, "_sub_tasks", {})
        sub_task = sub_tasks.get(session_id)
        current_task = asyncio.current_task()
        if sub_task is not None and sub_task is not current_task:
            try:
                await asyncio.shield(sub_task)
            except asyncio.CancelledError:
                # A cancelled sub-session is already terminal.  Cancellation
                # of this workflow task, however, must still propagate.
                if not sub_task.cancelled():
                    raise
            except Exception:
                logger.debug(
                    "循环旧 session 任务异常结束: %s", session_id,
                    exc_info=True,
                )

        from src.agent.session import _persistence_manager

        session = self._session_manager.sessions.get(session_id)
        if session is None:
            session = _persistence_manager._sessions.get(session_id)
        if session is not None:
            # loop session 自带一个永久 inbox consumer；旧迭代退出后必须先停止，
            # 否则即使 registry 已移除，Task 仍会长期持有整个 AgentSession。
            await session.stop_consumer()
            # 最终保存是注销前的事务边界：失败时保留 dirty 与两套 registry，
            # 让下一次 cleanup 或全局 persistence manager 可以安全重试。
            await session.async_save(force=True, strict=True)

        self._session_manager.sessions.pop(session_id, None)
        sub_tasks.pop(session_id, None)
        _persistence_manager.unregister(session_id)
        logger.debug(
            f"循环迭代 #{iteration} 清理旧 session: {session_id}"
        )

    # ============================================================
    # 循环网关调度
    # ============================================================

    async def _execute_loop_gateway(
        self, definition: WorkflowDef, task: WorkflowTask,
        step: dict, disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """执行循环网关：从 continue 出边的条件表达式解析循环语义。

        支持三种表达式：
          for item in chapters         → 列表遍历
          for key, value in config    → 字典遍历
          for i in range(5)           → 0..4
          for i in range(1, 5)        → 1..4

        每轮迭代将迭代变量写入 parameter_values，循环体内通过 {{var}} 引用。
        """
        loop_body_nodes: list[str] = step.get("loop_body_nodes", [])
        exit_target = step.get("exit_target")
        continue_target = step.get("continue_target")
        gateway_id = step.get("gateway_id", "")
        loop_states = self._task_control_flow_state(task).setdefault("loops", {})
        cursor = loop_states.get(gateway_id, {})

        if not loop_body_nodes:
            logger.error("循环体为空 (loop_gateway)")
            return "failed"
        if not exit_target:
            logger.error("循环网关缺少退出分支")
            return "failed"

        # 获取 continue 边的条件表达式
        continue_edge = next(
            (e for e in definition.edges
             if e.source == gateway_id and e.target == continue_target),
            None,
        )
        if not continue_edge or not continue_edge.condition:
            logger.error("循环网关 continue 边未配置循环表达式")
            return "failed"

        expression = continue_edge.condition.get("expression", "")
        if not expression.strip():
            logger.error("循环网关 continue 边表达式为空")
            return "failed"

        # 解析循环表达式
        try:
            loop_info = parse_loop_expression(expression)
        except ValueError as e:
            logger.error(f"循环表达式解析失败: {e}")
            return "failed"

        mode = loop_info["mode"]
        logger.info(
            f"循环网关: mode={mode}, expr='{expression}' (task={task.task_id})"
        )

        # 构建迭代序列
        frozen_items = cursor.get("items")
        if frozen_items is not None:
            items = deepcopy(frozen_items)
            mode = cursor.get("mode", mode)
            iter_var = loop_info["iter_var"]
            if mode == "dict":
                key_var, val_var = loop_info["iter_var"]
                iter_var = key_var
        elif mode == "range":
            iter_var = loop_info["iter_var"]
            items = list(range(loop_info["range_start"], loop_info["range_end"]))
            logger.info(f"   range: {loop_info['range_start']}..{loop_info['range_end']-1} ({len(items)} 次)")
        elif mode == "list":
            iter_var = loop_info["iter_var"]
            source = loop_info["source"]
            raw = task.parameter_values.get(source, "")
            if not raw:
                for nid, ns in task.node_states.items():
                    if ns.status == "completed" and source in ns.outputs:
                        raw = ns.outputs[source]
                        break
            ok, parsed = _try_parse_json(raw)
            if not ok or not isinstance(parsed, list):
                logger.error(f"循环网关：变量 {source} 不是合法的 JSON 列表")
                return "failed"
            items = parsed
            logger.info(f"   list: source={source}, len={len(items)}, var={iter_var}")
        elif mode == "dict":
            key_var, val_var = loop_info["iter_var"]
            source = loop_info["source"]
            iter_var = key_var  # for logging
            raw = task.parameter_values.get(source, "")
            if not raw:
                for nid, ns in task.node_states.items():
                    if ns.status == "completed" and source in ns.outputs:
                        raw = ns.outputs[source]
                        break
            ok, parsed = _try_parse_json(raw)
            if not ok or not isinstance(parsed, dict):
                logger.error(f"循环网关：变量 {source} 不是合法的 JSON 字典")
                return "failed"
            items = list(parsed.items())  # [(key, value), ...]
            logger.info(
                f"   dict: source={source}, len={len(items)}, "
                f"key={key_var}, value={val_var}"
            )
        else:
            logger.error(f"循环网关：未知模式 {mode}")
            return "failed"

        if len(items) > MAX_LOOP_ITERATIONS:
            logger.error(
                "循环网关：迭代数 %s 超过上限 %s，拒绝不完整执行",
                len(items),
                MAX_LOOP_ITERATIONS,
            )
            return "failed"

        if not cursor:
            cursor = {
                "type": "loop",
                "mode": mode,
                "items": deepcopy(items),
                "next_index": 0,
                "active_index": None,
                "phase": "ready",
            }
            loop_states[gateway_id] = cursor
            await self._save_task_state(definition.workflow_id, task)

        if not items:
            logger.info("循环网关：迭代序列为空，跳过循环体")
        elif cursor.get("phase") != "completed":
            active_index = cursor.get("active_index")
            start_index = (
                active_index
                if isinstance(active_index, int)
                else int(cursor.get("next_index", 0))
            )
            for idx in range(start_index, len(items)):
                item = items[idx]
                logger.info(
                    f"循环网关 迭代 #{idx} (task={task.task_id})"
                )

                # 注入迭代变量到 parameter_values
                if mode == "range":
                    task.parameter_values[iter_var] = str(item)
                elif mode == "list":
                    task.parameter_values[iter_var] = (
                        json.dumps(item, ensure_ascii=False)
                        if isinstance(item, (dict, list))
                        else str(item)
                    )
                elif mode == "dict":
                    k, v = item
                    task.parameter_values[key_var] = str(k)
                    task.parameter_values[val_var] = (
                        json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (dict, list))
                        else str(v)
                    )

                # active_index 存在表示从当前失败迭代恢复，此时仅重试被
                # Manager 激活的节点；新迭代才清理上一轮瞬态。
                if cursor.get("active_index") != idx:
                    for nid in loop_body_nodes:
                        state = task.node_states.setdefault(
                            nid, NodeExecutionState(node_id=nid),
                        )
                        await self._reset_node_for_loop_iteration(state, idx)

                cursor.update({
                    "active_index": idx,
                    "phase": "running",
                    "iteration_values": {
                        key: value for key, value in task.parameter_values.items()
                        if key in (
                            [iter_var]
                            if mode != "dict"
                            else [key_var, val_var]
                        )
                    },
                })
                loop_states[gateway_id] = cursor
                await self._save_task_state(definition.workflow_id, task)

                # 执行循环体
                body_result = await self._execute_loop_body(
                    definition=definition, task=task,
                    body_nodes=loop_body_nodes,
                    disabled_ids=disabled_ids,
                    shared_ws=shared_ws,
                    parent_id=parent_id,
                    on_node_started=on_node_started,
                    needs_approval=needs_approval,
                    run_record=run_record,
                )
                if body_result in {"failed", "retry_waiting"}:
                    cursor["phase"] = body_result
                    loop_states[gateway_id] = cursor
                    await self._save_task_state(definition.workflow_id, task)
                    logger.error(
                        "循环网关迭代未完成: index=%s status=%s task=%s",
                        idx, body_result, task.task_id,
                    )
                    return body_result

                for nid in loop_body_nodes:
                    if nid in task.node_states:
                        self._snapshot_node_for_iteration(
                            task.node_states[nid], idx, task.parameter_values,
                        )
                cursor.update({
                    "active_index": None,
                    "next_index": idx + 1,
                    "phase": "ready",
                })
                loop_states[gateway_id] = cursor

                await self._save_task_state(definition.workflow_id, task)
                self._push_wf_task_update(definition.workflow_id, task)

            cursor["phase"] = "completed"
            cursor["active_index"] = None
            loop_states[gateway_id] = cursor
            await self._save_task_state(definition.workflow_id, task)

        # 执行退出分支
        if exit_target and exit_target not in disabled_ids:
            node_def = definition.get_node(exit_target)
            if node_def:
                return await self._execute_node_sequence(
                    definition=definition, task=task,
                    node_ids=[exit_target], disabled_ids=disabled_ids,
                    shared_ws=shared_ws, parent_id=parent_id,
                    on_node_started=on_node_started,
                    needs_approval=needs_approval,
                    run_record=run_record,
                )
        return "completed"

    async def _execute_loop_body(
        self, definition: WorkflowDef, task: WorkflowTask,
        body_nodes: list[str], disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """执行循环体内的节点序列，支持并行网关和条件网关（不嵌套循环）。

        生成仅为 body_nodes 范围内的子执行计划，逐个步骤执行。
        """
        if not body_nodes:
            return "completed"

        # 构建邻接表（限制在 body_nodes + 循环网关自身范围）
        adj: dict[str, list[str]] = {}
        all_ids = set(body_nodes) | {g.id for g in definition.gateways}
        for e in definition.edges:
            if e.source in all_ids and e.target in all_ids:
                adj.setdefault(e.source, []).append(e.target)

        # 找 body_nodes 中没有入边（或入边来自循环网关）的入口节点
        rev_adj: dict[str, list[str]] = {}
        for e in definition.edges:
            rev_adj.setdefault(e.target, []).append(e.source)

        entry_nodes: list[str] = []
        for nid in body_nodes:
            upstream = rev_adj.get(nid, [])
            # 入口节点的上游要么为空，要么只连接到网关
            if not upstream or all(
                uid in {g.id for g in definition.gateways} for uid in upstream
            ):
                entry_nodes.append(nid)
        if not entry_nodes:
            entry_nodes = body_nodes[:1]

        # 遍历 body_nodes 及其内部的网关，逐个执行
        executed: set[str] = set()
        gateway_ids_set = {g.id for g in definition.gateways}
        current = entry_nodes[0]

        while current and current not in executed:
            if current in disabled_ids:
                executed.add(current)
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes else None
                continue

            # 判断当前是否网关
            gw = definition.get_gateway(current)
            if gw and gw.gateway_type == "parallel":
                # 找到起止并行/汇聚分支
                cid = gw.converge_gateway_id
                out_targets = adj.get(current, [])
                branches: list[list[str]] = []
                for target in out_targets:
                    bc = target
                    branch_nodes: list[str] = []
                    branch_visited: set[str] = set()
                    while bc and bc not in branch_visited and bc != cid:
                        branch_visited.add(bc)
                        if definition.get_node(bc) and bc not in gateway_ids_set:
                            branch_nodes.append(bc)
                        next_nodes = adj.get(bc, [])
                        bc = next_nodes[0] if next_nodes else None
                    branches.append(branch_nodes)

                # 并行执行各分支
                async def _run_branch(bn: list[str]) -> str:
                    return await self._execute_node_sequence(
                        definition=definition, task=task,
                        node_ids=bn, disabled_ids=disabled_ids,
                        shared_ws=shared_ws, parent_id=parent_id,
                        on_node_started=on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )

                branch_tasks = [
                    asyncio.create_task(_run_branch(bn))
                    for bn in branches
                ]
                outcomes: list[str] = []
                for coro in asyncio.as_completed(branch_tasks):
                    try:
                        outcomes.append(await coro)
                    except Exception:
                        outcomes.append("failed")
                if "retry_waiting" in outcomes:
                    return "retry_waiting"
                if "failed" in outcomes:
                    return "failed"

                for bn in branches:
                    executed.update(bn)
                if cid:
                    executed.add(cid)
                    next_after = adj.get(cid, [])
                    current = next_after[0] if next_after else None
                else:
                    current = None

            elif gw and gw.gateway_type == "condition":
                # 条件分支：排他执行
                out_targets = adj.get(current, [])
                variable_pool = dict(task.parameter_values or {})
                for nid, ns in task.node_states.items():
                    if ns.status == "completed" and ns.outputs:
                        for k, v in ns.outputs.items():
                            if k and v:
                                variable_pool[k] = v

                default_target: str | None = None
                selected_target: str | None = None
                for target in out_targets:
                    edge = next(
                        (e for e in definition.edges
                         if e.source == current and e.target == target),
                        None,
                    )
                    cond = edge.condition if edge else None
                    if cond and cond.get("is_default"):
                        default_target = target
                        continue
                    if cond and cond.get("expression", "").strip():
                        try:
                            resolved = await resolve_placeholders(
                                cond["expression"], variable_pool,
                            )
                            if evaluate_condition(resolved):
                                selected_target = target
                                break
                        except Exception:
                            continue
                if selected_target is None:
                    selected_target = default_target

                if selected_target is None:
                    return "failed"

                node_def = definition.get_node(selected_target)
                if node_def and selected_target not in disabled_ids:
                    seq_result = await self._execute_node_sequence(
                        definition=definition, task=task,
                        node_ids=[selected_target],
                        disabled_ids=disabled_ids,
                        shared_ws=shared_ws, parent_id=parent_id,
                        on_node_started=on_node_started,
                        needs_approval=needs_approval,
                        run_record=run_record,
                    )
                    if seq_result in {"failed", "retry_waiting"}:
                        return seq_result
                executed.add(current)
                next_after = adj.get(selected_target, [])
                current = next_after[0] if next_after and next_after[0] not in executed else None

            elif definition.get_node(current):
                # 普通可执行节点
                seq_result = await self._execute_node_sequence(
                    definition=definition, task=task,
                    node_ids=[current], disabled_ids=disabled_ids,
                    shared_ws=shared_ws, parent_id=parent_id,
                    on_node_started=on_node_started,
                    needs_approval=needs_approval,
                    run_record=run_record,
                )
                if seq_result in {"failed", "retry_waiting"}:
                    return seq_result
                executed.add(current)
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes and next_nodes[0] not in executed else None
            else:
                executed.add(current)
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes and next_nodes[0] not in executed else None

        return "completed"
