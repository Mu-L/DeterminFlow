"""
工作流 REST API 路由

提供工作流定义 CRUD、运行控制、状态查询和历史记录接口。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

from ..config import SESSIONS_DIR, WORKFLOWS_DIR
from ..workflow.definition import WorkflowDef
from .workflow_access import (
    _ensure_http_mutation_allowed,
    _ensure_workflow_writable,
    _get_manager,
)
from .workflow_request_models import (
    PreStartRequest,
    TaskCreateRequest,
    WorkflowCreateRequest,
    WorkflowRunRequest,
    WorkflowUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# 变量 key 不允许以 _ 开头（保留给 _system.xxx 系统变量）
_VAR_KEY_RE = re.compile(r"^[a-zA-Z0-9][\w\-]*$")


def _load_session_file(session_id: str) -> dict | None:
    """从持久化文件读取 session，避免跨进程时只能看到当前内存态。"""
    if not session_id:
        return None
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", session_id)
    if not safe_id:
        return None
    path = SESSIONS_DIR / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("读取 session 文件失败: session_id=%s path=%s", session_id, path, exc_info=True)
        return None


def _last_assistant_message(record: list[dict]) -> str:
    for msg in reversed(record or []):
        if msg.get("type") == "assistant" and msg.get("content"):
            return str(msg.get("content", ""))[:200]
    return ""


def _session_summary_from_data(session_id: str, data: dict | None) -> dict | None:
    if not data:
        return None
    record = data.get("record") or []
    msg_count = len([m for m in record if m.get("type") not in ("system_prompt", "compression_divider")])
    summary = {
        "session_id": session_id,
        "type": data.get("session_type", ""),
        "parent_id": data.get("parent_id"),
        "status": data.get("status", ""),
        "task": str(data.get("task_description", ""))[:100],
        "message_count": msg_count,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "last_message": _last_assistant_message(record),
        "agent_type": data.get("agent_type", ""),
        "workspace_path": data.get("workspace_path"),
        "workflow_id": data.get("workflow_id", ""),
        "task_id": data.get("task_id", ""),
        "node_id": data.get("node_id", ""),
    }
    return {k: v for k, v in summary.items() if v not in (None, "")}


def _node_meta_from_definition(definition: dict) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for node in definition.get("nodes", []) or []:
        node_id = node.get("id")
        if not node_id:
            continue
        meta[node_id] = {
            "node_id": node_id,
            "label": node.get("label", node_id),
            "node_type": node.get("node_type", ""),
            "agent_type": node.get("agent_type", ""),
        }
    return meta


def _validate_variable_keys(variables: list[dict]) -> None:
    """校验变量 key：不允许以 _ 开头（保留给 _system.xxx 系统变量）。"""
    for v in variables:
        key = v.get("key", "")
        if key and key.startswith("_"):
            raise HTTPException(
                status_code=400,
                detail=f"变量 key 不能以下划线开头（保留给系统变量）: {key}",
            )


# ============================================================
# 工作流定义 API
# ============================================================

@router.get("")
async def list_workflows(request: Request):
    """列出所有工作流。"""
    mgr = _get_manager(request)
    return mgr.list_workflows()


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request):
    """获取单个工作流定义和状态。"""
    mgr = _get_manager(request)
    result = mgr.get_workflow(workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return result


@router.post("")
async def create_workflow(request: Request, body: WorkflowCreateRequest):
    """创建新工作流。"""
    mgr = _get_manager(request)
    requested_id = str(getattr(body, "workflow_id", "") or "")
    if requested_id:
        _ensure_http_mutation_allowed(request, requested_id)
    _validate_variable_keys(body.variables)
    data = body.model_dump()
    validation = mgr.validate_workflow(data)
    if not validation.get("valid"):
        raise HTTPException(
            status_code=400,
            detail="\n".join(validation.get("errors", ["校验失败"])),
        )
    result = mgr.create_workflow(data)
    if not result.get("definition"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, request: Request, body: WorkflowUpdateRequest):
    """更新工作流定义。保存前自动校验连线完整性。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)

    _validate_variable_keys(body.variables)

    data = body.model_dump()

    # 校验
    result = mgr.validate_workflow(data)
    if not result.get("valid"):
        raise HTTPException(status_code=400, detail="\n".join(result.get("errors", ["校验失败"])))

    result2 = mgr.update_workflow(workflow_id, data)
    if result2 is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return result2


@router.post("/validate")
async def validate_workflow(request: Request, body: WorkflowUpdateRequest):
    """校验工作流定义完整性（不保存）。返回 valid 和 errors 列表。"""
    mgr = _get_manager(request)
    _validate_variable_keys(body.variables)
    return mgr.validate_workflow(body.model_dump())


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request):
    """删除工作流。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    success = mgr.delete_workflow(workflow_id)
    if not success:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return {"success": True}


# ============================================================
# 运行控制 API
# ============================================================

@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: str, request: Request, body: WorkflowRunRequest = WorkflowRunRequest()):
    """启动工作流运行。可指定 from_node_id 从某节点重新开始（回滚）。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = await mgr.run_workflow(workflow_id, from_node_id=body.from_node_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "启动失败"))
    return result


@router.post("/{workflow_id}/stop")
async def stop_workflow(workflow_id: str, request: Request):
    """停止正在运行的工作流。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    return await mgr.stop_workflow(workflow_id)


@router.get("/{workflow_id}/status")
async def get_workflow_status(workflow_id: str, request: Request):
    """查询工作流当前运行状态。"""
    mgr = _get_manager(request)
    result = mgr.get_workflow_status(workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="工作流状态不存在")
    return result


@router.get("/{workflow_id}/runs")
async def get_run_history(workflow_id: str, request: Request, limit: int = 20):
    """获取工作流运行历史记录。"""
    mgr = _get_manager(request)
    return mgr.get_run_history(workflow_id, limit=limit)


# ============================================================
# 任务管理 API（编辑与运行分离架构）
# ============================================================

@router.post("/{workflow_id}/tasks")
async def create_task(workflow_id: str, request: Request,
                      body: TaskCreateRequest = TaskCreateRequest()):
    """创建新任务（不启动）。接受 parameter_values 用于变量填参。
    返回 task_id，前端随后调用 run 接口启动。
    如果 workflow 没有定义变量，则 parameter_values 可留空。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = mgr.create_task(workflow_id, from_node_id=body.from_node_id,
                              parameter_values=body.parameter_values,
                              disabled_node_ids=body.disabled_node_ids,
                              scheme_id=body.scheme_id,
                              selected_node_ids=body.selected_node_ids,
                              workspace_override=body.workspace_override)
    if result is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return result


@router.post("/{workflow_id}/tasks/{task_id}/run")
async def run_task(workflow_id: str, task_id: str, request: Request,
                   body: WorkflowRunRequest = WorkflowRunRequest()):
    """启动一个已创建的任务。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = await mgr.run_task(workflow_id, task_id, from_node_id=body.from_node_id)
    if not result.get("success"):
        status_code = 409 if result.get("error") == "task_state_conflict" else 400
        raise HTTPException(
            status_code=status_code,
            detail=result.get("message", "启动失败"),
        )
    return result


@router.get("/{workflow_id}/variables")
async def get_workflow_variables(workflow_id: str, request: Request):
    """获取工作流定义的变量列表（用于任务填参表单）。"""
    mgr = _get_manager(request)
    wf_data = mgr.get_workflow(workflow_id)
    if wf_data is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return wf_data["definition"].get("variables", [])


@router.get("/{workflow_id}/variable-references")
async def get_variable_references(workflow_id: str, request: Request):
    """获取工作流的变量→节点引用映射（用于参数过滤和引用计数显示）。"""
    mgr = _get_manager(request)
    wf_data = mgr.get_workflow(workflow_id)
    if wf_data is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    definition = WorkflowDef.from_dict(wf_data["definition"])
    return definition.get_variable_references()


@router.get("/{workflow_id}/tasks")
async def list_tasks(workflow_id: str, request: Request,
                     limit: int = 50,
                     status: str = "",
                     search: str = "",
                     sort_by: str = "created_at",
                     sort_order: str = "desc",
                     page: int = 1,
                     page_size: int = 20):
    """列出工作流的所有任务（支持筛选/排序/搜索/分页）。

    Query params:
        status: 状态过滤（空字符串表示全部）
        search: 搜索关键词（匹配名称和 task_id）
        sort_by: 排序字段（created_at/started_at/completed_at/status/name）
        sort_order: asc 或 desc
        page: 页码（从 1 开始）
        page_size: 每页条数（新参数，替代旧的 limit）
    """
    mgr = _get_manager(request)
    return mgr.list_tasks(
        workflow_id,
        limit=limit,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.get("/{workflow_id}/tasks/{task_id}")
async def get_task(workflow_id: str, task_id: str, request: Request):
    """获取任务运行状态 + 工作流定义。"""
    mgr = _get_manager(request)
    result = mgr.get_task_with_definition(workflow_id, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result


@router.get("/{workflow_id}/tasks/{task_id}/sessions")
async def get_task_sessions(workflow_id: str, task_id: str, request: Request):
    """获取工作流任务关联的持久化 session 列表。

    与 `/api/sessions` 不同，本接口不依赖当前进程内存态，优先从任务文件
    的 `main_session_id` / `node_states[*].session_id` 反查 `data/sessions/*.json`。
    这使任务历史页即使在多进程或重启后，也能稳定跳转到对应 workflow 会话。
    """
    task_file = WORKFLOWS_DIR / workflow_id / "tasks" / f"{task_id}.json"
    if not task_file.exists():
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        task_data = json.loads(task_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(status_code=500, detail="任务文件损坏")

    definition = task_data.get("snapshot_definition") or {}
    if not definition:
        def_file = WORKFLOWS_DIR / workflow_id / "definition.json"
        if def_file.exists():
            try:
                definition = json.loads(def_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                definition = {}
    node_meta = _node_meta_from_definition(definition)

    node_sessions: list[dict] = []
    parent_ids: set[str] = set()
    for node_id, state in (task_data.get("node_states") or {}).items():
        session_id = state.get("session_id") or ""
        meta = node_meta.get(node_id, {"node_id": node_id, "label": node_id})
        entry = {
            **meta,
            "status": state.get("status", ""),
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
            "summary": str(state.get("summary", ""))[:500],
            "error": str(state.get("error", ""))[:500],
            "session_id": session_id,
            "session_found": False,
        }
        if session_id:
            session_data = _load_session_file(session_id)
            session_summary = _session_summary_from_data(session_id, session_data)
            if session_summary:
                entry["session_found"] = True
                entry["session"] = session_summary
                parent_id = session_summary.get("parent_id")
                if parent_id:
                    parent_ids.add(parent_id)
        node_sessions.append({k: v for k, v in entry.items() if v not in (None, "")})

    main_session_id = task_data.get("main_session_id") or ""
    if not main_session_id and parent_ids:
        # API 直接启动的任务历史上未把 workflow-main session_id 写回 task；
        # 用节点 sub session 的 parent_id 反推。
        main_session_id = sorted(parent_ids)[0]

    main_session = None
    if main_session_id:
        main_session = _session_summary_from_data(main_session_id, _load_session_file(main_session_id))

    return {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "task_name": task_data.get("name", ""),
        "status": task_data.get("status", ""),
        "main_session_id": main_session_id or None,
        "main_session_found": bool(main_session),
        "main_session": main_session,
        "node_sessions": node_sessions,
        "node_session_count": len([n for n in node_sessions if n.get("session_id")]),
    }


@router.get("/{workflow_id}/tasks/{task_id}/token-usage")
async def get_task_token_usage(workflow_id: str, task_id: str, request: Request):
    """获取任务的 Token 消耗详情。

    从任务持久化 JSON 中读取各节点的 token_usage 数据，
    按节点、模型和 agent 类型分别汇总返回。

    Returns:
        {
            "workflow_id": str,
            "task_id": str,
            "task_name": str,
            "status": str,
            "calls": [{timestamp, provider, model, token counts, cost, ...}],
            "rejections": [{error_codes, reason, retry_usage, retry_cost, ...}],
            "nodes": [{node_id, agent_type, status, token_usage, calls, cost}],
            "by_model": {model_id: {token counts, cost}},
            "by_agent_type": {agent_type: {token counts, cost}},
            "total": {token counts, cost},
            "cost_status": "priced | partially_priced | unpriced | no_usage",
            "currency": str,
            "pricing_snapshot": {...}
        }
    """
    from src.config import WORKFLOWS_DIR
    from src.workflow.token_usage import aggregate_token_usage
    import json

    task_file = WORKFLOWS_DIR / workflow_id / "tasks" / f"{task_id}.json"
    if not task_file.exists():
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        task_data = json.loads(task_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        raise HTTPException(status_code=500, detail="任务文件损坏")

    # 优先使用任务创建时的定义快照，确保工作流后续编辑不会改变历史统计。
    def_file = WORKFLOWS_DIR / workflow_id / "definition.json"
    node_agent_map: dict[str, str] = {}
    snapshot_definition = task_data.get("snapshot_definition")
    if isinstance(snapshot_definition, dict):
        for node in snapshot_definition.get("nodes", []):
            node_id = node.get("id", "")
            if node_id:
                node_agent_map[node_id] = node.get("agent_type", "unknown")
    elif def_file.exists():
        try:
            def_data = json.loads(def_file.read_text(encoding="utf-8"))
            for node in def_data.get("nodes", []):
                node_id = node.get("id", "")
                if node_id:
                    node_agent_map[node_id] = node.get("agent_type", "unknown")
        except (json.JSONDecodeError, IOError):
            pass

    task_name = task_data.get("task_name", task_data.get("name", ""))
    task_status = task_data.get("status", "unknown")
    node_states_raw = task_data.get("node_states", {})

    aggregation = aggregate_token_usage(node_states_raw, node_agent_map)

    return {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "task_name": task_name,
        "status": task_status,
        **aggregation,
    }


@router.post("/{workflow_id}/tasks/{task_id}/stop")
async def stop_task(workflow_id: str, task_id: str, request: Request):
    """停止正在运行的任务。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    return await mgr.stop_task(workflow_id, task_id)


@router.get("/{workflow_id}/tasks/{task_id}/nodes/{node_id}/messages")
async def get_node_messages(workflow_id: str, task_id: str, node_id: str, request: Request):
    """获取任务中某个节点的 Agent 会话消息历史和推理链路。"""
    mgr = _get_manager(request)
    result = mgr.get_node_messages(workflow_id, task_id, node_id)
    if result is None:
        raise HTTPException(status_code=404, detail="任务或节点不存在")
    return result


# ============================================================
# Agent 类型查询 API（供前端节点库使用）
# ============================================================

@router.get("/agent-types/list")
async def list_agent_types(request: Request):
    """列出可用的 Agent 类型（供前端画布节点库）。

    返回每个 agent 类型的信息，包括其 prompt template 的自定义变量块声明。
    """
    from src.agent.definition import list_available_sub_session_types, get_agent_definition
    from src.prompts.manager import PromptManager

    pm = getattr(request.app.state, "prompt_manager", None)

    types = []
    for at in list_available_sub_session_types():
        ad = get_agent_definition(at)
        agent_info = {
            "agent_type": at,
            "description": ad.description if ad else "",
        }

        # 获取该 agent 的 prompt template 的自定义变量块声明
        if ad and ad.prompt_template and pm:
            try:
                template_vars = pm.get_template_variables(ad.prompt_template)
                agent_info["template_variables"] = template_vars
            except Exception:
                agent_info["template_variables"] = []
        else:
            agent_info["template_variables"] = []

        types.append(agent_info)
    return types


# ============================================================
# 节点类型查询 API（供前端节点面板使用）
# ============================================================

@router.get("/node-types/list")
async def list_node_types(request: Request):
    """列出所有已注册的节点类型（供前端画布节点面板渲染）。"""
    from src.workflow.nodes import registry
    return registry.list_all()


# ============================================================
# 子流程节点 API（可见变量）
# ============================================================

@router.get("/{workflow_id}/visible-variables")
async def get_visible_variables(workflow_id: str, request: Request):
    """获取指定工作流的可见变量列表（供子流程节点填参使用）。

    可见变量定义：source_type="input" 且 hidden!=true 的变量。
    返回每个变量的 key、name、type、default、required、description、options。
    """
    mgr = _get_manager(request)
    wf_data = mgr.get_workflow(workflow_id)
    if wf_data is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    variables = wf_data["definition"].get("variables", [])
    visible = [
        v for v in variables
        if v.get("source_type", "input") == "input" and not v.get("hidden", False)
    ]
    return visible


# ============================================================
# 执行方案 API
# ============================================================

class SchemeCreateRequest(BaseModel):
    name: str = Field(description="方案名称")
    selected_node_ids: list[str] = Field(description="选中的节点 ID 列表")


class SchemeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, description="方案名称")
    selected_node_ids: list[str] | None = Field(default=None, description="选中的节点 ID 列表")


@router.get("/{workflow_id}/schemes")
async def list_schemes(workflow_id: str, request: Request):
    """列出工作流的所有执行方案。"""
    mgr = _get_manager(request)
    result = mgr.get_schemes(workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return result


@router.post("/{workflow_id}/schemes")
async def create_scheme(workflow_id: str, request: Request, body: SchemeCreateRequest):
    """创建新的执行方案。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    try:
        result = mgr.create_scheme(workflow_id, body.name, body.selected_node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return result


@router.put("/{workflow_id}/schemes/{scheme_id}")
async def update_scheme(workflow_id: str, scheme_id: str, request: Request,
                         body: SchemeUpdateRequest):
    """更新执行方案。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    try:
        result = mgr.update_scheme(workflow_id, scheme_id,
                                    name=body.name,
                                    selected_node_ids=body.selected_node_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="工作流或方案不存在")
    return result


@router.delete("/{workflow_id}/schemes/{scheme_id}")
async def delete_scheme(workflow_id: str, scheme_id: str, request: Request):
    """删除执行方案。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    success = mgr.delete_scheme(workflow_id, scheme_id)
    if not success:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"success": True}


# ============================================================
# 审批节点 API（人工审批）
# ============================================================

class ResolveApprovalRequest(BaseModel):
    approved: bool = Field(description="是否通过：true=通过，false=驳回")
    reason: str = Field(default="", description="驳回原因")


@router.post("/{workflow_id}/tasks/{task_id}/resolve-approval/{node_id}")
async def resolve_approval(workflow_id: str, task_id: str, node_id: str,
                            request: Request, body: ResolveApprovalRequest):
    """人工审批节点决策（审批节点使用）。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = mgr.approve_node(
        workflow_id=workflow_id, task_id=task_id,
        node_id=node_id, approved=body.approved, feedback=body.reason,
    )
    return result


# ============================================================
# Main 接管模式 API
# ============================================================

class VariableUpdateRequest(BaseModel):
    parameter_values: dict[str, str] = Field(default_factory=dict)


@router.post("/{workflow_id}/pre-start")
async def pre_start_workflow(workflow_id: str, request: Request,
                               body: PreStartRequest = PreStartRequest()):
    """预启动工作流：创建 pending task + workspace + workflow-main session。
    返回 task_id 和 session_id，前端随后可连接 WebSocket 与 main 对话填参。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = await mgr.pre_start_task(workflow_id,
                                       workspace_override=body.workspace_override,
                                       main_takeover=body.main_takeover)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "预启动失败"))
    return result


@router.put("/{workflow_id}/tasks/{task_id}/variables")
async def update_task_variables(workflow_id: str, task_id: str,
                                 request: Request, body: VariableUpdateRequest):
    """更新任务的全局变量参数值（用于手动填参后更新 pending task）。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    if not mgr.update_task_variables(workflow_id, task_id, body.parameter_values or {}):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "message": "参数已更新", "task_id": task_id}


@router.post("/{workflow_id}/tasks/{task_id}/start")
async def start_pre_running_task(workflow_id: str, task_id: str, request: Request):
    """从预启动状态正式启动任务执行。仅对 pre_running 状态的任务有效。"""
    mgr = _ensure_http_mutation_allowed(request, workflow_id)
    result = await mgr.start_pre_running_task(workflow_id, task_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "启动失败"))
    return result


# ============================================================
# 脚本文件管理 API
# ============================================================

_SCRIPT_NAME_RE = re.compile(r"^[\w][\w\-]*$")

def _get_script_path(workflow_id: str, script_name: str, script_type: str = "shell") -> Path:
    """构建脚本文件路径，含安全校验。"""
    from src.config import WORKFLOWS_DIR
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", workflow_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail=f"workflow_id 包含非法字符: {workflow_id}")
    if not _SCRIPT_NAME_RE.match(script_name):
        raise HTTPException(status_code=400, detail=f"脚本名包含非法字符: {script_name}")
    ext_map = {"shell": "sh", "python": "py"}
    if script_type not in ext_map:
        raise HTTPException(status_code=400, detail=f"不支持的脚本类型: {script_type}，可选: {', '.join(ext_map)}")
    ext = ext_map[script_type]
    wf_dir = (WORKFLOWS_DIR / safe_id).resolve()
    if not wf_dir.is_relative_to(WORKFLOWS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="workflow_id 路径越界")
    return wf_dir / "script" / f"{script_name}.{ext}"


class ScriptContentRequest(BaseModel):
    content: str = Field(description="脚本内容")


@router.get("/{workflow_id}/script/{script_name}")
async def get_script_content(workflow_id: str, script_name: str,
                              request: Request, type: str = "shell"):
    """读取脚本文件内容。"""
    script_path = _get_script_path(workflow_id, script_name, type)
    if not script_path.exists():
        return {"content": "", "exists": False}
    try:
        content = script_path.read_text(encoding="utf-8")
        return {"content": content, "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取脚本失败: {e}")


@router.put("/{workflow_id}/script/{script_name}")
async def update_script_content(workflow_id: str, script_name: str,
                                 request: Request, body: ScriptContentRequest,
                                 type: str = "shell"):
    """写入/更新脚本文件内容。"""
    _ensure_http_mutation_allowed(request, workflow_id)
    _ensure_workflow_writable(request, workflow_id)
    script_path = _get_script_path(workflow_id, script_name, type)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        script_path.write_text(body.content, encoding="utf-8")
        return {"success": True, "message": f"脚本 {script_name} 已保存"}
    except Exception as e:
        logger.exception("保存脚本失败")
        raise HTTPException(status_code=500, detail="保存脚本失败，请查看服务日志")


@router.delete("/{workflow_id}/script/{script_name}")
async def delete_script(workflow_id: str, script_name: str,
                         request: Request, type: str = "shell"):
    """删除脚本文件。"""
    _ensure_http_mutation_allowed(request, workflow_id)
    _ensure_workflow_writable(request, workflow_id)
    script_path = _get_script_path(workflow_id, script_name, type)
    if not script_path.exists():
        return {"success": True, "message": "脚本文件不存在"}
    try:
        script_path.unlink()
        return {"success": True, "message": f"脚本 {script_name} 已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除脚本失败: {e}")


# ============================================================
# 全局任务历史 API（跨工作流）
# ============================================================

tasks_router = APIRouter(prefix="/api", tags=["tasks"])


# ============================================================
# 脚本库 API（data/script-library/）
# ============================================================

import shutil
from src.workflow.script_library import get_script_library


@router.get("/script-library/groups")
async def list_script_library_groups(request: Request):
    """列出脚本库所有分组（子目录名）。"""
    return get_script_library().list_groups()


@router.get("/script-library/scripts")
async def list_script_library_scripts(request: Request, group: str = ""):
    """列出脚本库所有脚本（含分组信息），可按分组过滤。

    返回格式：[{group: "utils", name: "deploy", script_type: "shell"}, ...]
    """
    return get_script_library().list_scripts(group)


@router.get("/script-library/{group}/{script_name}/script")
async def get_library_script_content(group: str, script_name: str,
                                      request: Request, type: str = "shell"):
    """读取脚本库脚本文件内容。"""
    if not _SCRIPT_NAME_RE.match(script_name):
        raise HTTPException(status_code=400, detail=f"脚本名包含非法字符: {script_name}")
    ext_map = {"shell": "sh", "python": "py"}
    if type not in ext_map:
        raise HTTPException(status_code=400, detail=f"不支持的脚本类型: {type}")
    ext = ext_map[type]
    try:
        location = get_script_library().resolve_file(group, script_name, f"{script_name}.{ext}")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库路径: {group}/{script_name}")
    if location is None:
        return {"content": "", "exists": False}
    script_path = location.directory / f"{script_name}.{ext}"
    try:
        content = script_path.read_text(encoding="utf-8")
        return {"content": content, "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取脚本失败: {e}")


class LibraryScriptUpdateRequest(BaseModel):
    content: str = Field(description="脚本内容")


@router.put("/script-library/{group}/{script_name}/script")
async def update_library_script_content(group: str, script_name: str,
                                         request: Request, body: LibraryScriptUpdateRequest,
                                         type: str = "shell"):
    """创建/更新脚本库脚本文件。"""
    if not _SCRIPT_NAME_RE.match(script_name):
        raise HTTPException(status_code=400, detail=f"脚本名包含非法字符: {script_name}")
    ext_map = {"shell": "sh", "python": "py"}
    if type not in ext_map:
        raise HTTPException(status_code=400, detail=f"不支持的脚本类型: {type}")
    ext = ext_map[type]
    try:
        script_dir = get_script_library().writable_directory(group, script_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库路径: {group}/{script_name}")
    script_path = script_dir / f"{script_name}.{ext}"
    script_dir.mkdir(parents=True, exist_ok=True)
    try:
        script_path.write_text(body.content, encoding="utf-8")
        return {"success": True, "message": f"脚本已保存: {script_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存脚本失败: {e}")


@router.delete("/script-library/{group}/{script_name}")
async def delete_library_script(group: str, script_name: str,
                                 request: Request):
    """删除脚本库脚本（含整个子目录）。"""
    if not _SCRIPT_NAME_RE.match(script_name):
        raise HTTPException(status_code=400, detail=f"脚本名包含非法字符: {script_name}")
    catalog = get_script_library()
    try:
        script_dir = catalog.writable_directory(group, script_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库路径: {group}/{script_name}")
    if not script_dir.exists():
        return {"success": True, "message": "脚本不存在"}
    try:
        shutil.rmtree(script_dir)
        # 如果分组目录为空，也清理它
        group_dir = catalog.writable_group(group)
        if group_dir.exists() and not any(group_dir.iterdir()):
            group_dir.rmdir()
        return {"success": True, "message": f"脚本已删除: {group}/{script_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除脚本失败: {e}")


@router.get("/script-library/{group}/{script_name}/meta")
async def get_library_script_meta(group: str, script_name: str,
                                   request: Request):
    """读取脚本库脚本的 SCRIPT.md 元信息。"""
    try:
        location = get_script_library().resolve_file(group, script_name, "SCRIPT.md")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库路径: {group}/{script_name}")
    if location is None:
        return {"content": "", "exists": False}
    meta_path = location.directory / "SCRIPT.md"
    try:
        content = meta_path.read_text(encoding="utf-8")
        return {"content": content, "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取元信息失败: {e}")


class LibraryMetaUpdateRequest(BaseModel):
    content: str = Field(description="SCRIPT.md 内容")


@router.put("/script-library/{group}/{script_name}/meta")
async def update_library_script_meta(group: str, script_name: str,
                                      request: Request, body: LibraryMetaUpdateRequest):
    """更新脚本库脚本的 SCRIPT.md 元信息。"""
    try:
        script_dir = get_script_library().writable_directory(group, script_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库路径: {group}/{script_name}")
    script_dir.mkdir(parents=True, exist_ok=True)
    meta_path = script_dir / "SCRIPT.md"
    try:
        meta_path.write_text(body.content, encoding="utf-8")
        return {"success": True, "message": f"元信息已保存: {meta_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存元信息失败: {e}")


@router.delete("/script-library/{group}")
async def delete_library_group(group: str, request: Request):
    """删除脚本库分组（仅当分组为空时允许）。"""
    try:
        group_dir = get_script_library().writable_group(group)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法的脚本库分组: {group}")
    if not group_dir.exists():
        return {"success": True, "message": "分组不存在"}
    if any(group_dir.iterdir()):
        raise HTTPException(status_code=400, detail="分组不为空，请先删除组内所有脚本")
    try:
        group_dir.rmdir()
        return {"success": True, "message": f"分组已删除: {group}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除分组失败: {e}")


@tasks_router.get("/tasks")
async def list_all_tasks(request: Request,
                         status: str = "",
                         search: str = "",
                         sort_by: str = "created_at",
                         sort_order: str = "desc",
                         page: int = 1,
                         page_size: int = 20,
                         workflow_id: str = "",
                         main_session_id: str = ""):
    """列出全部工作流的所有任务（全局任务历史），支持筛选/排序/搜索/分页。

    Query params:
        status: 状态过滤
        search: 搜索关键词（匹配名称/task_id/模板名称/workflow_id）
        sort_by: 排序字段（created_at/started_at/completed_at/status/name/workflow_name）
        sort_order: asc 或 desc
        page / page_size: 分页
        workflow_id: 按工作流过滤（可选）
        main_session_id: 按任务所属 Main 会话过滤（可选）
    """
    mgr = _get_manager(request)
    return mgr.list_all_tasks(
        status=status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
        workflow_id=workflow_id,
        main_session_id=main_session_id,
    )
