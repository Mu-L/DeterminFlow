"""节点序列、并行网关与条件分支调度。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import Callable

from .condition_parser import evaluate_condition
from .definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowRunRecord,
    WorkflowTask,
    _now_iso,
    resolve_placeholders,
)
from .failure_policy import (
    AUTO_RETRY_TRIGGER,
    apply_failure_skip,
    can_auto_retry,
    normalize_node_status,
    prepare_node_retry,
    should_auto_skip,
    VALIDATOR_RETRY_TRIGGER,
)

logger = logging.getLogger(f"{__package__}.engine")


def _retry_deadline(interval_seconds: int) -> str:
    now = datetime.fromisoformat(_now_iso())
    return (now + timedelta(seconds=max(0, interval_seconds))).isoformat()


def _with_rejection_feedback(node_def, node_state: NodeExecutionState):
    if node_def.node_type != "agent" or not node_state.rejection_history:
        return node_def
    event = node_state.rejection_history[-1]
    if event.get("resolution") != "retrying":
        return node_def
    retry_index = int(event.get("retry_index") or 0)
    max_retries = int(event.get("max_retries") or 0)
    reason = str(event.get("reason") or "")
    retry_message = (
        f"{node_def.first_message}\n\n"
        f"## 下游校验反馈\n\n"
        f"这是第 {retry_index}/{max_retries} 次重试。\n"
        f"拒绝原因: {reason}\n\n"
        "请根据反馈重新完成完整任务，并按原要求提交产出。"
    )
    return replace(node_def, first_message=retry_message)


def _update_rejection_retry_audit(
    state: NodeExecutionState,
    *,
    previous_call_ids: set[str],
    will_retry: bool,
) -> None:
    if not state.rejection_history:
        return
    event = state.rejection_history[-1]
    if event.get("resolution") != "retrying":
        return
    retry_calls = [
        item
        for item in state.token_usage_calls
        if str(item.get("call_id", "")) not in previous_call_ids
    ]
    known_call_ids = {
        str(item) for item in event.get("retry_call_ids") or [] if item
    }
    for item in retry_calls:
        call_id = str(item.get("call_id") or "")
        if call_id:
            known_call_ids.add(call_id)
    if state.session_id:
        event["retry_session_id"] = state.session_id
    event["retry_call_ids"] = sorted(known_call_ids)
    if will_retry:
        event["resolution"] = "retrying"
        event["resolved_at"] = None
        return
    event["resolution"] = (
        "failed" if normalize_node_status(state.status) == "failed" else "passed"
    )
    event["resolved_at"] = _now_iso()


class WorkflowFlowMixin:
    """为 WorkflowEngine 提供非循环控制流调度。"""

    async def _apply_failed_node_policy(
        self,
        *,
        definition: WorkflowDef,
        task: WorkflowTask,
        node_def,
        state: NodeExecutionState,
    ) -> tuple[str, NodeExecutionState]:
        if can_auto_retry(node_def, state):
            state = prepare_node_retry(
                state,
                trigger=AUTO_RETRY_TRIGGER,
                next_retry_at=_retry_deadline(
                    node_def.auto_retry_interval_seconds
                ),
            )
            outcome = "retry_waiting"
        elif should_auto_skip(node_def, state):
            failure_error = state.error
            state = apply_failure_skip(state, completed_at=_now_iso())
            outcome = "skipped"
            logger.info(
                "节点 %s 自动重试耗尽后跳过 (error=%s)",
                node_def.id,
                failure_error[:100],
            )
        else:
            outcome = "failed"
        async with self._node_state_lock:
            task.node_states[node_def.id] = state
        await self._save_task_state(definition.workflow_id, task)
        self._push_wf_task_update(definition.workflow_id, task)
        return outcome, state

    async def _execute_node_sequence(
        self, definition: WorkflowDef, task: WorkflowTask,
        node_ids: list[str], disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """执行一串串行节点序列。返回 "completed" | "failed"。

        与旧 execute_task 中的 while 循环逻辑相同，包括审批驳回回滚。
        """
        MAX_REJECTION_COUNT = 3
        ids = list(node_ids)
        i = 0
        while i < len(ids):
            node_id = ids[i]
            node_def = definition.get_node(node_id)
            if node_def is None: i += 1; continue

            if node_id in disabled_ids:
                ns = task.node_states.get(node_id, NodeExecutionState(node_id=node_id))
                ns.status = "skipped"; ns.completed_at = _now_iso()
                async with self._node_state_lock:
                    task.node_states[node_id] = ns
                await self._save_task_state(definition.workflow_id, task)
                self._push_wf_task_update(definition.workflow_id, task)
                i += 1; continue

            # 恢复总是从根执行计划进入；规范终态快速跳过，未被明确激活的
            # 失败/等待节点则保持阻塞，避免恢复时意外重放副作用。
            existing_state = task.node_states.get(node_id)
            existing_status = (
                normalize_node_status(existing_state.status)
                if existing_state else ""
            )
            if existing_state and existing_status in ("skipped", "completed"):
                existing_state.status = existing_status
                logger.debug(
                    "[ENGINE] 节点状态 %s，跳过: node=%s, task=%s",
                    existing_state.status, node_id, task.task_id,
                )
                i += 1; continue
            if existing_state and existing_state.status in {"failed", "rejected"}:
                return "failed"
            if existing_state and existing_state.status == "retry_waiting":
                return "retry_waiting"
            if existing_state and existing_state.status == "running":
                logger.error(
                    "恢复时发现未收尾的 running 节点: node=%s, task=%s",
                    node_id, task.task_id,
                )
                return "failed"

            task.current_node_id = node_id
            node_state = task.node_states.get(node_id, NodeExecutionState(node_id=node_id))
            node_state.status = "running"
            logger.debug(
                "[ENGINE] 节点开始: node=%s, task=%s",
                node_id, task.task_id,
            )
            async with self._node_state_lock:
                task.node_states[node_id] = node_state
            # 不在 begin_node_attempt 之前持久化半成品 running 状态。
            # _execute_node 会先冻结输入、递增 Attempt，再在插件执行前统一 checkpoint；
            # 否则此处与该 checkpoint 之间进程退出，会留下 attempt_count=0 且
            # input_snapshot 为空的 running 节点，后续重试将错误复用空输入。

            # 同一下游节点的一次执行只能接受一次 reject_upstream。
            # 回调为同步接口，可能被多个工具调用并发触发，因此用线程锁
            # 将“校验、记账、改状态”收敛为一次原子接受。
            reject_accept_lock = Lock()
            reject_accepted = False
            rejected_upstream_id = ""

            # 定义 reject_upstream 回调
            def _on_reject_upstream(downstream_session_id: str, reason: str, target_node_id: str = "") -> dict:
                nonlocal reject_accepted, rejected_upstream_id
                """处理下游节点拒绝上游产出的回调。"""
                with reject_accept_lock:
                    if reject_accepted:
                        message = (
                            "reject_upstream conflict: 当前下游节点的本次执行"
                            f"已接受对上游节点 {rejected_upstream_id} 的打回请求"
                        )
                        logger.warning(message)
                        return {
                            "success": False,
                            "conflict": True,
                            "error_code": "reject_upstream_conflict",
                            "message": message,
                        }

                    # 确定目标上游节点：优先显式 target_node_id，其次从 definition.edges 查找
                    if target_node_id:
                        upstream_id = target_node_id
                    else:
                        # 从 workflow definition 的 edges 中查找当前节点的上游
                        upstream_ids = [e.source for e in definition.edges if e.target == node_id]
                        # 排除 START 和网关节点
                        gateway_ids = {g.id for g in definition.gateways}
                        upstream_ids = [uid for uid in upstream_ids if uid != "__start__" and uid not in gateway_ids]
                        if upstream_ids:
                            upstream_id = upstream_ids[0]  # 取第一个上游可执行节点
                        else:
                            message = f"reject_upstream: 从 definition.edges 无法确定上游节点 (node={node_id})"
                            logger.warning(message)
                            return {"success": False, "message": message}

                    upstream_state = task.node_states.get(upstream_id)
                    if not upstream_state:
                        message = f"reject_upstream: 上游节点 {upstream_id} 尚未执行"
                        logger.warning(message)
                        return {"success": False, "message": message}

                    if definition.get_node(upstream_id) is None:
                        message = f"reject_upstream: 上游节点 {upstream_id} 不在工作流定义中"
                        logger.warning(message)
                        return {"success": False, "message": message}

                    # 检查拒绝次数限制
                    max_reject = self._get_max_reject_count(node_def)
                    if upstream_state.reject_upstream_count >= max_reject:
                        message = (
                            f"reject_upstream: 上游节点 {upstream_id} 已被拒绝 "
                            f"{upstream_state.reject_upstream_count} 次，达到上限 {max_reject}"
                        )
                        logger.warning(message)
                        return {"success": False, "message": message}

                    # 从这里开始才视为“已接受”；后续调用只返回 conflict。
                    upstream_state.reject_upstream_count += 1
                    reject_accepted = True
                    rejected_upstream_id = upstream_id
                    error_codes = sorted(set(re.findall(r"\[([a-z][a-z0-9_]*)\]", reason)))
                    prior_iteration_rejections = sum(
                        len(item.get("rejection_history") or [])
                        for item in upstream_state.iteration_history
                        if isinstance(item, dict)
                    )
                    rejection_sequence = (
                        prior_iteration_rejections
                        + len(upstream_state.rejection_history)
                        + 1
                    )
                    upstream_state.rejection_history.append({
                        "rejection_id": (
                            f"{task.task_id}:{node_id}:{upstream_id}:"
                            f"{rejection_sequence}"
                        ),
                        "occurred_at": _now_iso(),
                        "validator_node_id": node_id,
                        "target_node_id": upstream_id,
                        "retry_index": upstream_state.reject_upstream_count,
                        "max_retries": max_reject,
                        "error_codes": error_codes or ["unclassified"],
                        "reason": reason,
                        "resolution": "retrying",
                        "resolved_at": None,
                        "retry_session_id": "",
                        "retry_call_ids": [],
                    })
                    upstream_state.status = "waiting_retry"
                    upstream_state.error = ""
                    task.node_states[upstream_id] = upstream_state
                    task.node_states[node_id] = node_state

                    logger.info(
                        "reject_upstream: 上游节点 %s 被拒绝，将创建新 Session 重试",
                        upstream_id,
                    )
                    return {
                        "success": True,
                        "message": (
                            f"已打回上游节点 {upstream_id}，"
                            f"第 {upstream_state.reject_upstream_count}/{max_reject} 次"
                        ),
                        "upstream_id": upstream_id,
                        "attempt": upstream_state.reject_upstream_count,
                        "max_reject_count": max_reject,
                    }

            async def _on_node_checkpoint(
                checkpoint_state: NodeExecutionState,
            ) -> None:
                async with self._node_state_lock:
                    task.node_states[node_id] = checkpoint_state
                await self._save_task_state(definition.workflow_id, task)
                self._push_wf_task_update(definition.workflow_id, task)

            attempt_call_ids = {
                str(item.get("call_id", ""))
                for item in node_state.token_usage_calls
            }
            effective_node_def = _with_rejection_feedback(
                node_def,
                node_state,
            )
            exec_state = await self._execute_node(
                definition, effective_node_def, node_state, shared_ws,
                parent_id=parent_id, on_node_started=on_node_started,
                parameter_values=task.parameter_values,
                node_states=task.node_states,
                workflow_id=definition.workflow_id,
                task_id=task.task_id,
                task_name=task.name,
                execution_order=ids, node_index=i,
                needs_approval=needs_approval,
                on_reject_upstream=_on_reject_upstream,
                on_node_checkpoint=_on_node_checkpoint,
            )
            async with self._node_state_lock:
                task.node_states[node_id] = exec_state
            node_state = exec_state
            run_record.node_executions.append(exec_state)
            _update_rejection_retry_audit(
                exec_state,
                previous_call_ids=attempt_call_ids,
                will_retry=(
                    exec_state.status == "failed"
                    and can_auto_retry(node_def, exec_state)
                ),
            )
            logger.debug(
                "[ENGINE] 节点完成: node=%s, status=%s, session_id=%s, task=%s",
                node_id, exec_state.status,
                getattr(exec_state, "session_id", ""),
                task.task_id,
            )

            # 检查是否有 reject_upstream 调用，需要回滚
            if reject_accepted:
                # 从 workflow definition 查找真实上游节点
                upstream_id = rejected_upstream_id
                if not upstream_id:
                    all_upstream = [e.source for e in definition.edges if e.target == node_id]
                    gw_ids = {g.id for g in definition.gateways}
                    real_upstream_ids = [uid for uid in all_upstream if uid != "__start__" and uid not in gw_ids]
                    upstream_id = real_upstream_ids[0] if real_upstream_ids else ""

                if not upstream_id:
                    logger.warning(f"reject_upstream 回滚: 未找到上游节点 (node={node_id})，跳过回滚")
                else:
                    upstream_state = task.node_states.get(upstream_id)
                    upstream_node_def = definition.get_node(upstream_id)
                    if not upstream_state or upstream_node_def is None:
                        logger.warning(f"reject_upstream 回滚: 上游节点 {upstream_id} 不存在")
                        return "failed"
                    else:
                        old_call_ids = {
                            str(item.get("call_id", ""))
                            for item in upstream_state.token_usage_calls
                        }
                        retry_state = prepare_node_retry(
                            replace(upstream_state, status="failed"),
                            trigger=VALIDATOR_RETRY_TRIGGER,
                        )
                        retry_node_def = _with_rejection_feedback(
                            upstream_node_def,
                            retry_state,
                        )
                        task.node_states[upstream_id] = retry_state
                        await self._save_task_state(definition.workflow_id, task)
                        self._push_wf_task_update(definition.workflow_id, task)

                        async def _on_upstream_retry_checkpoint(
                            checkpoint_state: NodeExecutionState,
                        ) -> None:
                            async with self._node_state_lock:
                                task.node_states[upstream_id] = checkpoint_state
                            await self._save_task_state(
                                definition.workflow_id,
                                task,
                            )
                            self._push_wf_task_update(
                                definition.workflow_id,
                                task,
                            )

                        retry_state = await self._execute_node(
                            definition, retry_node_def, retry_state, shared_ws,
                            parent_id=parent_id,
                            on_node_started=on_node_started,
                            parameter_values=task.parameter_values,
                            node_states=task.node_states,
                            workflow_id=definition.workflow_id,
                            task_id=task.task_id,
                            task_name=task.name,
                            execution_order=ids,
                            node_index=ids.index(upstream_id) if upstream_id in ids else 0,
                            needs_approval=needs_approval,
                            on_node_checkpoint=_on_upstream_retry_checkpoint,
                        )
                        will_auto_retry = (
                            retry_state.status == "failed"
                            and can_auto_retry(
                                upstream_node_def,
                                retry_state,
                            )
                        )
                        _update_rejection_retry_audit(
                            retry_state,
                            previous_call_ids=old_call_ids,
                            will_retry=will_auto_retry,
                        )
                        task.node_states[upstream_id] = retry_state
                        run_record.node_executions.append(retry_state)
                        upstream_state = retry_state
                        logger.info(
                            "reject_upstream 回滚: 上游节点 %s 已通过新 Session 重试完成",
                            upstream_id,
                        )

                    # 下游节点等待上游真正完成；包括 provider 故障进入
                    # retry_waiting 的情况，恢复后会重新执行本校验节点。
                    node_state.status = "pending"
                    node_state.rejection_reason = ""
                    node_state.completed_at = None
                    node_state.session_id = ""
                    node_state.error = ""
                    task.node_states[node_id] = node_state

                    if upstream_state and upstream_state.status == "failed":
                        outcome, upstream_state = (
                            await self._apply_failed_node_policy(
                                definition=definition,
                                task=task,
                                node_def=upstream_node_def,
                                state=upstream_state,
                            )
                        )
                        if outcome == "retry_waiting":
                            return "retry_waiting"
                        if outcome == "failed":
                            return "failed"
                        if outcome == "skipped":
                            node_state.status = "skipped"
                            node_state.is_skipped = True
                            node_state.completed_at = _now_iso()
                            task.node_states[node_id] = node_state
                            await self._save_task_state(
                                definition.workflow_id,
                                task,
                            )
                            self._push_wf_task_update(
                                definition.workflow_id,
                                task,
                            )
                            i += 1
                            continue
                    await self._save_task_state(definition.workflow_id, task)
                    self._push_wf_task_update(definition.workflow_id, task)
                    continue

            if exec_state.status == "rejected":
                if node_def.node_type == "approval" and i > 0:
                    node_state.rejection_count += 1
                    if node_state.rejection_count < MAX_REJECTION_COUNT:
                        upstream_id = ids[i - 1]
                        upstream_state = task.node_states.get(upstream_id)
                        if upstream_state and upstream_state.session_id:
                            sub_sid = upstream_state.session_id
                            if sub_sid in self._session_manager.sessions:
                                sub_session = self._session_manager.sessions[sub_sid]
                                rejection_msg = (
                                    f"## 审批驳回\n\n"
                                    f"你的产出被审批人驳回了（第 {node_state.rejection_count}/{MAX_REJECTION_COUNT} 次）。\n"
                                    f"驳回原因: {exec_state.error}\n\n"
                                    f"请根据以上反馈重新完成任务，完成后再次调用 complete_node_task。"
                                )
                                await sub_session.enqueue(
                                    content=rejection_msg, priority=1,
                                    source=f"approval:{node_id}",
                                    event_callback=self._session_manager._make_event_callback(sub_sid),
                                )
                                upstream_state.status = "running"; upstream_state.error = ""
                                task.node_states[upstream_id] = upstream_state
                                node_state.status = "pending"; node_state.rejection_reason = exec_state.error
                                task.node_states[node_id] = node_state
                                await self._save_task_state(definition.workflow_id, task)
                                self._push_wf_task_update(definition.workflow_id, task)
                                logger.info(f"审批驳回，回滚到上游节点 {upstream_id}")
                                i -= 1; continue
                        logger.warning(f"审批驳回但无法找到上游 session: {upstream_id}")
                exec_state.status = "failed"
                exec_state.error = (
                    f"审批驳回已达到上限: {exec_state.error}".rstrip()
                )
                task.node_states[node_id] = exec_state
                await self._save_task_state(definition.workflow_id, task)
                self._push_wf_task_update(definition.workflow_id, task)
                return "failed"

            if exec_state.status == "failed":
                outcome, exec_state = await self._apply_failed_node_policy(
                    definition=definition,
                    task=task,
                    node_def=node_def,
                    state=exec_state,
                )
                if outcome == "retry_waiting":
                    return "retry_waiting"
                if outcome == "skipped":
                    i += 1
                    continue
                return "failed"
            await self._save_task_state(definition.workflow_id, task)
            self._push_wf_task_update(definition.workflow_id, task)
            i += 1
        return "completed"

    # ============================================================
    # 并行分支调度
    # ============================================================

    async def _execute_parallel_branches(
        self, definition: WorkflowDef, task: WorkflowTask,
        branches: list[dict], converge_step: dict,
        disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """并行执行所有分支。

        为每个分支创建独立 asyncio.Task，等待全部完成。
        任一分支失败即返回 True，但不主动停止其他分支。

        Returns:
            "completed" | "retry_waiting" | "failed"
        """
        async def _run_branch(branch_nodes: list[str]) -> str:
            logger.debug(
                "[ENGINE] 分支开始: nodes=%s, task=%s",
                branch_nodes, task.task_id,
            )
            return await self._execute_node_sequence(
                definition=definition, task=task,
                node_ids=branch_nodes, disabled_ids=disabled_ids,
                shared_ws=shared_ws, parent_id=parent_id,
                on_node_started=on_node_started,
                needs_approval=needs_approval,
                run_record=run_record,
            )

        branch_tasks = [asyncio.create_task(_run_branch(b.get("nodes", []))) for b in branches]
        logger.debug(
            "[ENGINE] 并行分支已创建: task=%s, 分支数=%d",
            task.task_id, len(branch_tasks),
        )
        outcomes: list[str] = []

        for coro in asyncio.as_completed(branch_tasks):
            try:
                result = await coro
                outcomes.append(result)
                logger.debug(
                    "[ENGINE] 分支完成: task=%s, result=%s, 已完成/总数=%d/%d",
                    task.task_id, result,
                    len([bt for bt in branch_tasks if bt.done()]),
                    len(branch_tasks),
                )
            except asyncio.CancelledError:
                outcomes.append("failed")
            except Exception:
                logger.exception("[ENGINE] 分支执行异常: task=%s", task.task_id)
                outcomes.append("failed")

        # 不因一条分支失败而取消其他分支。这样已在运行的副作用会自然收尾，
        # 成功分支形成持久 checkpoint，恢复时不会被重复执行。
        if "retry_waiting" in outcomes:
            return "retry_waiting"
        if "failed" in outcomes:
            logger.warning(f"[ENGINE] 并行分支有失败 (task={task.task_id})")
            return "failed"

        # 合并所有分支的最终节点摘要
        logger.debug(
            "[ENGINE] 并行分支全部完成，开始汇聚合并: task=%s, 分支数=%d",
            task.task_id, len(branches),
        )
        await self._merge_branch_summaries(definition, task, branches, converge_step)
        logger.debug(
            "[ENGINE] 汇聚合并完成: task=%s, converge_gateway=%s",
            task.task_id, converge_step.get("gateway_id", ""),
        )
        return "completed"

    async def _merge_branch_summaries(
        self, definition: WorkflowDef, task: WorkflowTask,
        branches: list[dict], converge_step: dict,
    ):
        """合并所有分支最终节点的摘要，写入汇聚网关的虚拟状态。

        字符串拼接格式，下游节点通过 _get_upstream_summary 读取。
        """
        cid = converge_step.get("gateway_id", "")
        if not cid:
            return
        parts: list[str] = []
        for bi, branch in enumerate(branches):
            b_nodes = branch.get("nodes", [])
            if not b_nodes:
                continue
            last_id = b_nodes[-1]
            last_state = task.node_states.get(last_id)
            if last_state and last_state.summary:
                parts.append(f"并行分支{bi + 1} (节点 {last_id}):\n{last_state.summary}")
        if not parts:
            return
        merged = "\n\n".join(parts)
        if cid not in task.node_states:
            task.node_states[cid] = NodeExecutionState(node_id=cid)
        task.node_states[cid].summary = merged
        task.node_states[cid].status = "completed"
        await self._save_task_state(definition.workflow_id, task)
        logger.info(f"并行分支摘要已合并 (converge={cid}, branches={len(parts)})")

    # ============================================================
    # 条件网关 & 循环调度
    # ============================================================

    @staticmethod
    def _ordered_condition_branch_nodes(
        definition: WorkflowDef,
        start_node: str,
        convergence_node: str,
    ) -> list[str]:
        """按边顺序收集排他分支节点，直到公共汇合点。"""
        ordered: list[str] = []
        visited: set[str] = set()
        current = start_node
        while current and current not in visited and current != convergence_node:
            visited.add(current)
            if definition.get_node(current):
                ordered.append(current)
            next_ids = [e.target for e in definition.edges if e.source == current]
            current = next_ids[0] if next_ids else ""
        return ordered

    async def _evaluate_condition_gateway(
        self, definition: WorkflowDef, task: WorkflowTask,
        step: dict, disabled_ids: set[str],
        shared_ws: Path | None, parent_id: str,
        on_node_started: Callable, needs_approval: bool,
        run_record: WorkflowRunRecord,
    ) -> str:
        """冻结分支选择并执行完整分支，支持从分支内失败点恢复。"""

        branches = step.get("branches", [])
        if not branches:
            logger.error("条件网关无分支定义")
            return "failed"

        gateway_id = step.get("gateway_id", "")
        convergence = step.get("convergence_node_id", "")
        condition_states = task.control_flow_state.setdefault("conditions", {})
        gateway_state = condition_states.get(gateway_id, {})
        selected_target = gateway_state.get("selected_target", "")

        if not selected_target:
            variable_pool = dict(task.parameter_values or {})
            for ns in task.node_states.values():
                if ns.status == "completed" and ns.outputs:
                    variable_pool.update(ns.outputs)

            default_branch = next(
                (
                    branch for branch in branches
                    if (branch.get("condition") or {}).get("is_default")
                ),
                None,
            )
            selected_branch: dict | None = None
            for branch in branches:
                condition = branch.get("condition") or {}
                expression = condition.get("expression", "")
                if condition.get("is_default") or not expression.strip():
                    continue
                try:
                    resolved = await resolve_placeholders(expression, variable_pool)
                    if evaluate_condition(resolved):
                        selected_branch = branch
                        break
                except Exception as exc:
                    logger.warning(
                        "条件分支评估失败，跳过: gateway=%s error=%s",
                        gateway_id, exc,
                    )
            selected_branch = selected_branch or default_branch
            if selected_branch is None:
                logger.error("条件网关：无满足条件且无默认分支")
                return "failed"
            selected_target = selected_branch["target"]
            gateway_state = {
                "selected_target": selected_target,
                "branch_nodes": self._ordered_condition_branch_nodes(
                    definition, selected_target, convergence,
                ),
                "status": "running",
            }
            condition_states[gateway_id] = gateway_state

            for branch in branches:
                if branch["target"] == selected_target:
                    continue
                for nid in self._ordered_condition_branch_nodes(
                    definition, branch["target"], convergence,
                ):
                    state = task.node_states.get(
                        nid, NodeExecutionState(node_id=nid),
                    )
                    state.status = "skipped"
                    state.completed_at = _now_iso()
                    state.outputs = {}
                    task.node_states[nid] = state
            await self._save_task_state(definition.workflow_id, task)

        branch_nodes = gateway_state.get("branch_nodes") or [selected_target]
        outcome = await self._execute_node_sequence(
            definition=definition, task=task,
            node_ids=branch_nodes, disabled_ids=disabled_ids,
            shared_ws=shared_ws, parent_id=parent_id,
            on_node_started=on_node_started,
            needs_approval=needs_approval,
            run_record=run_record,
        )
        gateway_state["status"] = outcome
        condition_states[gateway_id] = gateway_state
        await self._save_task_state(definition.workflow_id, task)
        return outcome
