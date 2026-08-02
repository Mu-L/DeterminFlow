"""
SubprocessNode — 子流程节点插件。

将另一个已定义的工作流作为子流程嵌套执行。
采用内联展开模式：子流程的节点 inline 执行，变量作用域隔离。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import json
import logging
import re
from typing import TYPE_CHECKING

from .base import BaseNodePlugin, NodeContext, NodeResult
from ..definition import (
    resolve_placeholders, NodeExecutionState, WorkflowDef, _now_iso,
)
from ..failure_policy import (
    AUTO_RETRY_TRIGGER,
    MANUAL_RETRY_TRIGGER,
    begin_node_attempt,
    normalize_node_status,
    prepare_node_retry,
    record_attempt,
)

if TYPE_CHECKING:
    from src.workflow.definition import WorkflowNode

logger = logging.getLogger(__name__)

_PUBLIC_EXECUTION_POLICY = "public"
_INTERNAL_ONLY_EXECUTION_POLICY = "internal_only"
_VALID_EXECUTION_POLICIES = {
    _PUBLIC_EXECUTION_POLICY,
    _INTERNAL_ONLY_EXECUTION_POLICY,
}
_SUBPROCESS_SNAPSHOT_KEY = "__ai_company_subprocess_checkpoint_v1__"


class SubprocessNode(BaseNodePlugin):
    node_type = "subprocess"
    label = "子流程节点"
    default_icon = "subprocess"

    @property
    def params_schema(self) -> list[dict]:
        return [
            {
                "key": "sub_workflow_id",
                "label": "目标流程",
                "type": "select",
                "required": True,
                "default": "",
                "description": "选择要嵌套的子流程模板",
                # options 由前端从 /api/workflows/list 动态加载
            },
            {
                "key": "sub_scheme_id",
                "label": "执行方案",
                "type": "select",
                "required": False,
                "default": "",
                "description": "子流程的执行方案（空=全部节点执行）",
                # options 由前端根据选中的 sub_workflow_id 动态加载其 schemes
            },
            {
                "key": "label",
                "label": "节点名称",
                "type": "text",
                "required": False,
                "default": "",
                "description": "节点在画布上的显示名称",
            },
        ]

    async def execute(self, ctx: NodeContext) -> NodeResult:
        """执行子流程：加载定义 → 构建独立参数池 → inline 展开执行。"""
        node_def = ctx.node_def
        sub_wf_id = node_def.sub_workflow_id
        if not sub_wf_id:
            return NodeResult(status="failed", error="子流程节点未指定目标流程")

        snapshot = ctx.node_state.input_snapshot.get(_SUBPROCESS_SNAPSHOT_KEY)
        if snapshot is None:
            child_def = self._load_child_definition(sub_wf_id)
            if child_def is None:
                return NodeResult(status="failed", error=f"子流程 {sub_wf_id} 不存在")
        else:
            try:
                if not isinstance(snapshot, dict):
                    raise ValueError("快照不是对象")
                if snapshot.get("sub_workflow_id") != sub_wf_id:
                    raise ValueError("目标子流程 ID 不一致")
                definition_data = snapshot["child_definition"]
                sub_params = deepcopy(snapshot["resolved_child_params"])
                child_disabled_ids = set(snapshot["disabled_node_ids"])
                condition_choices = snapshot["condition_choices"]
                if not isinstance(definition_data, dict):
                    raise ValueError("child_definition 无效")
                if not isinstance(sub_params, dict):
                    raise ValueError("resolved_child_params 无效")
                if not isinstance(condition_choices, dict):
                    raise ValueError("condition_choices 无效")
                child_def = WorkflowDef.from_dict(deepcopy(definition_data))
            except (KeyError, TypeError, ValueError) as exc:
                return NodeResult(
                    status="failed",
                    error=f"subprocess_snapshot_invalid: {exc}",
                )

        policy_error = self._child_policy_error(ctx.definition, child_def)
        if policy_error:
            logger.warning(
                "拒绝子流程执行: parent=%s parent_policy=%s child=%s child_policy=%s",
                ctx.definition.workflow_id,
                ctx.definition.http_execution_policy,
                child_def.workflow_id,
                child_def.http_execution_policy,
            )
            return NodeResult(status="failed", error=policy_error)

        if snapshot is None:
            child_disabled_ids: set[str] = set()
            sub_scheme_id = node_def.sub_scheme_id
            if sub_scheme_id:
                scheme = next((s for s in child_def.execution_schemes
                               if s.id == sub_scheme_id), None)
                if scheme:
                    executable = {n.id for n in child_def.nodes
                                  if not (n.id.startswith("__") and n.id.endswith("__"))}
                    child_disabled_ids = executable - set(scheme.selected_node_ids)
                else:
                    logger.warning(f"子流程方案 {sub_scheme_id} 不存在，将执行全部节点")

            sub_params = {}
            parent_pv = ctx.parameter_values or {}
            variables = ctx.definition.variables
            shared_ws_str = str(ctx.shared_ws) if ctx.shared_ws else ""
            for var_key, param_info in (node_def.sub_workflow_params or {}).items():
                if param_info.get("use_default", False):
                    variable = next((v for v in child_def.variables
                                     if v.key == var_key), None)
                    sub_params[var_key] = variable.default or "" if variable else ""
                    continue
                user_val = param_info.get("value", "")
                if user_val and "{{" in user_val:
                    user_val = await resolve_placeholders(
                        user_val, parent_pv, variables, shared_ws_str)
                sub_params[var_key] = user_val or ""

            snapshot = {
                "sub_workflow_id": sub_wf_id,
                "child_definition": child_def.to_dict(),
                "resolved_child_params": deepcopy(sub_params),
                "disabled_node_ids": sorted(child_disabled_ids),
                "condition_choices": {},
            }
            ctx.node_state.input_snapshot[_SUBPROCESS_SNAPSHOT_KEY] = snapshot
            await self._checkpoint(ctx)

        condition_choices = snapshot["condition_choices"]

        # 3. 注入参数到子流程变量默认值
        for v in child_def.variables:
            if v.key in sub_params:
                v.default = sub_params[v.key]

        # 4. 获取子流程执行计划
        execution_plan = child_def.get_execution_plan()
        if not execution_plan:
            return NodeResult(status="failed", error=f"子流程 {sub_wf_id} 无节点")
        for gateway in child_def.gateways:
            if gateway.gateway_type != "condition":
                return NodeResult(
                    status="failed",
                    error=(
                        "subprocess_control_flow_unsupported: 子流程暂不支持可恢复的 "
                        f"{gateway.gateway_type} 网关 ({gateway.id})"
                    ),
                )
        if any(step.get("type") == "condition_gateway" and step.get("loop")
               for step in execution_plan):
            return NodeResult(
                status="failed",
                error="subprocess_control_flow_unsupported: 子流程暂不支持 condition loop",
            )

        # 初始化子流程内部节点状态
        if not ctx.node_state.child_states:
            ctx.node_state.child_states = {}

        all_child_node_ids = {node.id for node in child_def.nodes
                              if not (node.id.startswith("__")
                                      and node.id.endswith("__"))}

        # 初始化子流程节点的状态
        for cid in all_child_node_ids:
            if cid not in ctx.node_state.child_states:
                ctx.node_state.child_states[cid] = NodeExecutionState(node_id=cid)

        # 5. 执行子流程节点（复用引擎核心，但用子流程的 isolated 参数池）
        wf_id = ctx.workflow_id or ""
        task_id = ctx.task_id or ""

        try:
            child_error = await self._execute_child_plan(
                child_def=child_def,
                execution_plan=execution_plan,
                sub_params=sub_params,
                sub_variables=child_def.variables,
                child_disabled_ids=child_disabled_ids,
                condition_choices=condition_choices,
                parent_ctx=ctx,
                child_states=ctx.node_state.child_states,
                workflow_id=wf_id,
                task_id=task_id,
            )
        except Exception as e:
            logger.exception(f"子流程 {sub_wf_id} 执行异常: {e}")
            return NodeResult(status="failed", error=str(e))

        if child_error:
            return NodeResult(status="failed", error=child_error, outputs={})

        # 6. 合并子流程 outputs 到父流程（带节点 ID 前缀防止冲突）
        merged_outputs: dict[str, str] = {}
        for cid, cs in ctx.node_state.child_states.items():
            if cs.status == "completed" and cs.outputs:
                for k, v in cs.outputs.items():
                    key = f"{node_def.id}_{k}"
                    merged_outputs[key] = v

        # 子流程最后一个节点的 summary
        summary = ""
        for step in reversed(execution_plan):
            if step["type"] == "node":
                last_nid = step["node_id"]
                if last_nid in ctx.node_state.child_states:
                    summary = ctx.node_state.child_states[last_nid].summary
                break

        return NodeResult(
            summary=summary or "子流程执行完成",
            status="success",
            outputs=merged_outputs,
        )

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    @staticmethod
    async def _checkpoint(ctx: NodeContext) -> None:
        callback = ctx.checkpoint
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _child_policy_error(
        parent_def: WorkflowDef,
        child_def: WorkflowDef,
    ) -> str:
        """逐层约束子流程调用，防止 public 流程内联绕过内部入口策略。"""
        child_policy = child_def.http_execution_policy
        if child_policy not in _VALID_EXECUTION_POLICIES:
            return (
                "workflow_execution_policy_invalid: 子流程 "
                f"{child_def.workflow_id} 的 http_execution_policy 无效"
            )
        if (
            child_policy == _INTERNAL_ONLY_EXECUTION_POLICY
            and parent_def.http_execution_policy
            != _INTERNAL_ONLY_EXECUTION_POLICY
        ):
            return (
                "workflow_internal_only: public 工作流 "
                f"{parent_def.workflow_id} 不能执行 internal_only 子流程 "
                f"{child_def.workflow_id}"
            )
        return ""

    @staticmethod
    def _load_child_definition(sub_workflow_id: str) -> WorkflowDef | None:
        """从磁盘加载子流程定义。"""
        from src.config import WORKFLOWS_DIR
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", sub_workflow_id)
        if not safe_id:
            logger.warning(f"子流程 ID 包含非法字符: {sub_workflow_id}")
            return None
        wf_dir = (WORKFLOWS_DIR / safe_id).resolve()
        if not wf_dir.is_relative_to(WORKFLOWS_DIR.resolve()):
            logger.warning(f"子流程路径越界: {sub_workflow_id}")
            return None
        def_file = wf_dir / "definition.json"
        if not def_file.exists():
            logger.warning(f"子流程定义不存在: {sub_workflow_id}")
            return None
        try:
            return WorkflowDef.from_dict(json.loads(def_file.read_text(encoding='utf-8')))
        except Exception:
            logger.exception(f"加载子流程定义失败: {sub_workflow_id}")
            return None

    async def _execute_child_plan(
        self,
        child_def: WorkflowDef,
        execution_plan: list[dict],
        sub_params: dict[str, str],
        sub_variables: list,
        child_disabled_ids: set[str],
        condition_choices: dict[str, str],
        parent_ctx: NodeContext,
        child_states: dict[str, NodeExecutionState],
        workflow_id: str,
        task_id: str,
    ) -> str:
        """执行子流程的串行/条件计划，成功返回空字符串。"""
        sm = parent_ctx.session_manager
        shared_ws = parent_ctx.shared_ws
        shared_ws_str = str(shared_ws) if shared_ws else ""
        node_def = parent_ctx.node_def

        # 收集已完成的子流程节点 outputs（子流程内部变量池）
        child_pv: dict[str, str] = dict(sub_params)

        plan_index = 0
        while plan_index < len(execution_plan):
            step = execution_plan[plan_index]

            if step["type"] == "node":
                child_node_id = step["node_id"]
                child_node_def = child_def.get_node(child_node_id)
                if child_node_def is None:
                    plan_index += 1
                    continue

                # 跳过 START/END
                if child_node_id.startswith("__") and child_node_id.endswith("__"):
                    plan_index += 1
                    continue

                # 跳过被禁用的节点（来自执行方案裁剪）
                if child_node_id in child_disabled_ids:
                    child_state = child_states.get(
                        child_node_id, NodeExecutionState(node_id=child_node_id))
                    child_state.status = "skipped"
                    child_state.completed_at = _now_iso()
                    child_states[child_node_id] = child_state
                    self._push_child_node_status(
                        workflow_id, node_def.id, child_node_id,
                        "", "", "skipped", "",
                    )
                    await self._checkpoint(parent_ctx)
                    plan_index += 1
                    continue

                child_state = child_states.get(child_node_id,
                    NodeExecutionState(node_id=child_node_id))
                normalized = normalize_node_status(child_state.status)
                if normalized in {"completed", "skipped"}:
                    child_state.status = normalized
                    child_states[child_node_id] = child_state
                    if normalized == "completed":
                        child_pv.update(child_state.outputs)
                    plan_index += 1
                    continue
                if normalized not in {"pending", "failed"}:
                    return (
                        "subprocess_child_state_uncertain: 子节点状态不可安全重放: "
                        f"{child_node_id}={normalized}"
                    )
                previous_session_id = child_state.session_id
                previous_token_usage = child_state.token_usage
                previous_token_usage_calls = list(child_state.token_usage_calls)

                # 计算上游摘要
                upstream_ids = [e.source for e in child_def.edges if e.target == child_node_id]
                upstream_summary_parts: list[str] = []
                for uid in upstream_ids:
                    if uid in child_states and child_states[uid].summary:
                        upstream_summary_parts.append(
                            f"节点 [{uid}] 的任务产出：\n{child_states[uid].summary}"
                        )
                upstream_summary = "\n\n".join(upstream_summary_parts)

                if normalized == "failed":
                    parent_trigger = parent_ctx.node_state.next_attempt_trigger
                    retry_trigger = (
                        parent_trigger
                        if parent_trigger in {AUTO_RETRY_TRIGGER, MANUAL_RETRY_TRIGGER}
                        else MANUAL_RETRY_TRIGGER
                    )
                    child_state = prepare_node_retry(
                        child_state,
                        trigger=retry_trigger,
                    )
                child_state = begin_node_attempt(
                    child_state,
                    started_at=_now_iso(),
                    input_snapshot=dict(child_pv),
                    upstream_summary_snapshot=upstream_summary,
                )
                child_states[child_node_id] = child_state

                # 推送子流程节点状态（带 parent_node_id）
                self._push_child_node_status(
                    workflow_id, node_def.id, child_node_id,
                    child_state.session_id, "", "running", ""
                )

                # 构建子流程内部节点的 NodeContext
                from .base import NodeContext as _NodeContext
                workflow_environment = parent_ctx.workflow_environment
                child_owner_environment = (
                    workflow_environment(child_def.workflow_id)
                    if workflow_environment is not None
                    else {}
                )
                child_ctx = _NodeContext(
                    definition=child_def,
                    node_def=child_node_def,
                    node_state=child_state,
                    shared_ws=shared_ws,
                    session_ws_path=shared_ws_str,
                    parameter_values=deepcopy(child_state.input_snapshot),
                    upstream_summary=child_state.upstream_summary_snapshot,
                    needs_approval=False,
                    parent_id=parent_ctx.parent_id or "",
                    workflow_id=workflow_id,
                    task_id=task_id,
                    execution_order=[],
                    node_index=0,
                    session_manager=sm,
                    on_reject_upstream=None,
                    checkpoint=parent_ctx.checkpoint,
                    owner_environment=child_owner_environment,
                    workflow_environment=workflow_environment,
                )
                await self._checkpoint(parent_ctx)

                # 分发到对应节点类型的插件执行
                from . import registry as node_registry
                child_node_type = child_node_def.node_type or "agent"
                plugin_cls = node_registry.get(child_node_type)
                if plugin_cls is None:
                    result = NodeResult(
                        status="failed",
                        error=f"未知节点类型: {child_node_type}",
                    )
                else:
                    plugin = plugin_cls()
                    try:
                        result = await plugin.execute(child_ctx)
                    except Exception as e:
                        logger.exception(f"子流程节点 {child_node_id} 执行异常: {e}")
                        result = NodeResult(status="failed", error=str(e))

                child_state.completed_at = _now_iso()
                child_state.summary = result.summary
                child_state.status = normalize_node_status(result.status)
                child_state.error = result.error
                child_state.outputs = (
                    result.outputs or {}
                    if child_state.status == "completed" else {}
                )
                child_state.stdout = result.stdout or ""
                child_state.stderr = result.stderr or ""

                # 子流程节点绕过 WorkflowEngine._execute_node，必须在这里完成
                # 与顶层节点相同的 Token 用量和调用账本回写。AgentNode 当前在
                # NodeResult 中返回累计用量，调用级账本则以已完成 session 为准。
                result_token_usage = result.token_usage
                result_token_usage_calls = list(result.token_usage_calls)
                if result.session_id:
                    session = getattr(sm, "sessions", {}).get(result.session_id)
                    if session and hasattr(session, "get_cumulative_token_usage"):
                        current_usage = session.get_cumulative_token_usage()
                        if current_usage:
                            result_token_usage = current_usage
                    if session and hasattr(session, "get_token_usage_calls"):
                        result_token_usage_calls = session.get_token_usage_calls()

                if previous_token_usage and previous_session_id != result.session_id:
                    child_state.token_usage = self._merge_token_usage(
                        previous_token_usage,
                        result_token_usage,
                    )
                    child_state.token_usage_calls = self._merge_token_usage_calls(
                        previous_token_usage_calls,
                        result_token_usage_calls,
                    )
                else:
                    if result_token_usage:
                        child_state.token_usage = result_token_usage
                    if result_token_usage_calls:
                        child_state.token_usage_calls = result_token_usage_calls

                if result.session_id:
                    child_state.session_id = result.session_id
                child_state = record_attempt(
                    child_state,
                    status=child_state.status,
                    completed_at=child_state.completed_at,
                )
                child_states[child_node_id] = child_state

                self._push_child_node_status(
                    workflow_id, node_def.id, child_node_id,
                    child_state.session_id, child_state.summary,
                    child_state.status,
                    child_state.error,
                )
                await self._checkpoint(parent_ctx)

                # 子流程内部 outputs 回写到子流程参数池
                if child_state.status == "completed":
                    for k, v in child_state.outputs.items():
                        child_pv[k] = v

                if child_state.status not in {"completed", "skipped"}:
                    child_state.status = "failed"
                    child_state.outputs = {}
                    return child_state.error or f"子流程节点执行失败: {child_node_id}"

                plan_index += 1

            elif step["type"] == "condition_gateway":
                branches = step.get("branches", [])
                if not branches:
                    return "subprocess_condition_invalid: 条件网关没有分支"
                gateway_id = str(step.get("gateway_id", ""))
                frozen_target = condition_choices.get(gateway_id)
                selected = next((b for b in branches
                                 if b.get("target") == frozen_target), None)
                if selected is None:
                    default_branch = next((b for b in branches
                                           if (b.get("condition") or {}).get("is_default")), None)
                    from ..condition_parser import evaluate_condition
                    for branch in branches:
                        condition = branch.get("condition") or {}
                        expression = str(condition.get("expression", ""))
                        if condition.get("is_default") or not expression.strip():
                            continue
                        try:
                            resolved = await resolve_placeholders(
                                expression, child_pv, sub_variables, shared_ws_str)
                            if evaluate_condition(resolved):
                                selected = branch
                                break
                        except Exception as exc:
                            logger.warning("子流程条件分支评估失败: %s", exc)
                    selected = selected or default_branch
                if selected is None:
                    return "subprocess_condition_invalid: 条件网关没有可用分支"

                target = str(selected.get("target", ""))
                condition_choices[gateway_id] = target
                convergence = str(step.get("convergence_node_id") or "__end__")
                path: list[dict] = []
                current = target
                visited: set[str] = set()
                while current not in {convergence, "__end__"}:
                    if current in visited or child_def.get_node(current) is None:
                        return f"subprocess_condition_invalid: 分支路径无效: {current}"
                    visited.add(current)
                    path.append({"type": "node", "node_id": current})
                    targets = [e.target for e in child_def.edges if e.source == current]
                    if len(targets) != 1:
                        return f"subprocess_condition_invalid: 分支不是串行路径: {current}"
                    current = targets[0]

                for branch in branches:
                    branch_target = str(branch.get("target", ""))
                    if branch_target == target:
                        continue
                    for node_id in child_def._collect_branch_nodes(
                        child_def.edges, set(child_states), branch_target, convergence):
                        state = child_states[node_id]
                        if state.status == "pending":
                            state.status = "skipped"
                            state.completed_at = _now_iso()
                tail_index = len(execution_plan)
                if convergence != "__end__":
                    for idx in range(plan_index + 1, len(execution_plan)):
                        if execution_plan[idx].get("node_id") == convergence:
                            tail_index = idx
                            break
                    if tail_index == len(execution_plan):
                        return f"subprocess_condition_invalid: 缺少汇合节点 {convergence}"
                execution_plan = (
                    execution_plan[:plan_index + 1]
                    + path + execution_plan[tail_index:]
                )
                await self._checkpoint(parent_ctx)
                plan_index += 1

            elif step["type"] == "parallel_gateway":
                # 子流程暂不支持并行网关
                logger.warning("子流程不支持并行网关，跳过")
                plan_index += 1
                return "subprocess_control_flow_unsupported: parallel_gateway"

            else:
                plan_index += 1

        return ""

    @staticmethod
    def _merge_token_usage(previous: dict | None, current: dict | None) -> dict | None:
        """合并子节点多次独立 Session 的累计 Token 用量。"""
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
        """按稳定 call_id 合并子节点调用账本。"""
        merged: list[dict] = []
        seen: set[str] = set()
        for item in [*previous, *current]:
            if not isinstance(item, dict):
                continue
            call_id = str(item.get("call_id", ""))
            if not call_id:
                call_id = json.dumps(
                    item, ensure_ascii=False, sort_keys=True, default=str,
                )
            if call_id in seen:
                continue
            seen.add(call_id)
            merged.append(dict(item))
        return merged

    @staticmethod
    def _push_child_node_status(
        workflow_id: str,
        parent_node_id: str,
        child_node_id: str,
        session_id: str,
        summary: str,
        status: str,
        error: str,
    ) -> None:
        """推送子流程内部节点状态事件（带 parent_node_id 标记）。"""
        try:
            from src.web.event_bus import event_bus
            asyncio.create_task(event_bus.emit_event({
                "type": "wf_node_status",
                "workflow_id": workflow_id,
                "node_id": child_node_id,
                "parent_node_id": parent_node_id,
                "session_id": session_id,
                "status": status,
                "summary": summary,
                "error": error,
            }))
        except Exception:
            logger.debug("wf_node_status (child) 事件推送失败", exc_info=True)
