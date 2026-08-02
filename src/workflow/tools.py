"""
Workflow 专用工具 — 供 main agent（chat main 和 workflow main）使用。

已有工具（3 个）：
1. set_workflow_variable  — 修改变量值 + 推送 WebSocket 事件到前端
2. start_workflow_task     — 触发 engine 执行任务
3. approve_node           — 审批节点完成（通过/拒绝）

新增工具（6 个，chat main 可用的查询/操作工具）：
4. list_workflows         — 列出所有工作流定义
5. get_workflow           — 获取单个工作流详情
6. create_and_attach_task — 创建 pre_running 任务并绑定到当前 chat session
7. list_tasks             — 列出工作流任务历史（支持状态/搜索/分页）
8. get_task_status        — 获取单个任务执行状态
9. stop_task              — 停止运行中的任务
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.session.context import get_session_context

if TYPE_CHECKING:
    from src.workflow.manager import WorkflowManager
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)

_INTERNAL_ONLY_POLICY = "internal_only"


# ============================================================
# 通用 Helper
# ============================================================

def _ok(**data) -> str:
    """构建成功响应 JSON。自动添加 success=True。"""
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def _fail(message: str, **extra) -> str:
    """构建失败响应 JSON。自动添加 success=False。"""
    return json.dumps({"success": False, "message": message, **extra}, ensure_ascii=False)


def _internal_only_failure(workflow_id: str) -> str:
    """Return the stable denial used by user-facing Workflow Agent tools."""
    return _fail(
        (
            f"Workflow {workflow_id} 只允许 Core 内部服务调用；"
            "请使用所属业务 API"
        ),
        error="workflow_internal_only",
    )


def _policy_unavailable_failure(workflow_id: str) -> str:
    """Fail closed when a tool cannot establish a workflow's policy."""
    return _fail(
        f"无法确认 Workflow {workflow_id} 的执行策略，已拒绝操作",
        error="workflow_policy_unavailable",
    )


def _policy_invalid_failure(workflow_id: str) -> str:
    return _fail(
        f"Workflow {workflow_id} 的执行策略无效，已拒绝操作",
        error="workflow_execution_policy_invalid",
    )


def _load_tool_visible_workflow(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
) -> tuple[dict | None, str | None]:
    """Load a workflow and reject definitions reserved for internal services.

    The Workflow Agent tools are another user-controlled execution surface, so
    they must enforce the same boundary as the raw HTTP routes.  Returning the
    loaded definition also avoids a second lookup between policy validation and
    the caller's read operation.
    """
    try:
        workflow = workflow_manager.get_workflow(workflow_id)
    except Exception:
        logger.exception("读取 Workflow 执行策略失败: %s", workflow_id)
        return None, _policy_unavailable_failure(workflow_id)

    if not workflow:
        return None, None
    definition = workflow.get("definition")
    if not isinstance(definition, dict):
        return None, _policy_unavailable_failure(workflow_id)
    policy = definition.get("http_execution_policy")
    if policy == _INTERNAL_ONLY_POLICY:
        return None, _internal_only_failure(workflow_id)
    if policy not in (None, "", "public"):
        return None, _policy_invalid_failure(workflow_id)
    return workflow, None


def _ensure_tool_execution_allowed(
    workflow_manager: "WorkflowManager",
    workflow_id: str,
) -> str | None:
    """Return an error response unless a public workflow policy is confirmed."""
    workflow, error = _load_tool_visible_workflow(workflow_manager, workflow_id)
    if error:
        return error
    if workflow is None:
        return _fail(f"工作流 {workflow_id} 不存在")
    return None


def _require_binding(session_manager, action_desc: str) -> tuple[str, str, str | None]:
    """从 session 读取 workflow/task 绑定，未绑定时返回错误 JSON。

    Returns:
        (workflow_id, task_id, error_json_or_None)
        若 error_json_or_None 不为 None，调用方应直接返回该 JSON。
    """
    binding = _get_workflow_binding(session_manager)
    wid, tid = binding["workflow_id"], binding["task_id"]
    if not wid or not tid:
        return "", "", _fail(f"当前会话未关联工作流任务，无法{action_desc}")
    return wid, tid, None


# ============================================================
# Helper：从 session 对象读取绑定（跨 asyncio task 安全）
# ============================================================

def _get_workflow_binding(session_manager) -> dict:
    """从 session 对象直接读取当前绑定的 workflow_id/task_id。

    session 对象是跨 asyncio task 共享的同一 Python 对象，
    create_and_attach_task 对 session.workflow_id 的修改对所有工具可见。

    为什么不直接用 contextvars？
    LangGraph 的 ainvoke 会在子 asyncio task 中执行工具协程，
    子 task 中的 contextvars 修改不会传播回父 task。
    """
    ctx = get_session_context()
    session_id = ctx.get("session_id", "")
    if not session_id:
        return {"workflow_id": "", "task_id": ""}

    sessions = getattr(session_manager, 'sessions', {})
    session = sessions.get(session_id)
    if session is None:
        return {"workflow_id": "", "task_id": ""}

    return {
        "workflow_id": getattr(session, 'workflow_id', '') or '',
        "task_id": getattr(session, 'task_id', '') or '',
    }


# ============================================================
# Args Models
# ============================================================

class SetWorkflowVariableArgs(BaseModel):
    """set_workflow_variable 工具参数"""
    key: str = Field(description="变量 key（全局变量定义中的唯一标识）")
    value: str = Field(description="变量值")


class StartWorkflowTaskArgs(BaseModel):
    """start_workflow_task 参数（无参数，占位）"""
    pass


class ApproveNodeArgs(BaseModel):
    """approve_node 工具参数"""
    node_id: str = Field(description="节点 ID")
    approved: bool = Field(description="是否批准：true=通过，false=拒绝")
    feedback: str = Field(
        default="",
        description="审批意见。拒绝时建议提供具体反馈，帮助节点改进",
    )


# ============================================================
# 工具工厂
# ============================================================

def create_set_workflow_variable_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 set_workflow_variable 工具。

    此工具允许 main agent 修改工作流任务中的全局变量值，
    修改后通过 WebSocket 推送 wf_variable_update 事件到前端，
    使左侧表单实时更新。
    """

    async def _set_workflow_variable(key: str, value: str) -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")
        workflow_id, task_id, err = _require_binding(session_manager, "修改变量")
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        result = workflow_manager.set_workflow_variable(
            workflow_id=workflow_id,
            task_id=task_id,
            key=key,
            value=value,
            session_id=session_id,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="set_workflow_variable",
        description=(
            "修改当前工作流任务的全局变量值。"
            "调用后用户左侧填参表单会实时更新。"
            "参数 key 对应变量定义中的唯一标识（如 repo_url, branch 等）。"
        ),
        args_schema=SetWorkflowVariableArgs,
        func=lambda **kw: None,
        coroutine=_set_workflow_variable,
    )


def create_start_workflow_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 start_workflow_task 工具。

    此工具允许 main agent 正式启动工作流任务执行。
    预启动阶段（pre_running）结束后，调用此工具进入正式执行。
    """

    async def _start_workflow_task() -> str:
        workflow_id, task_id, err = _require_binding(session_manager, "启动")
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            result = await workflow_manager.start_pre_running_task(
                workflow_id=workflow_id,
                task_id=task_id,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("start_workflow_task 失败")
            return _fail(f"启动任务失败: {e}")

    return StructuredTool(
        name="start_workflow_task",
        description=(
            "正式启动工作流任务执行。"
            "调用此工具前请确保所有必要的全局变量已填写完毕。"
            "启动后，工作流将按节点顺序依次执行，每个节点完成后你需要审批其产出。"
        ),
        args_schema=StartWorkflowTaskArgs,  # 无参数
        func=lambda **kw: None,
        coroutine=_start_workflow_task,
    )


def create_approve_node_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 approve_node 工具。

    此工具用于审批 sub agent 调用 complete_node_task 后的节点产出。
    - 通过：引擎继续执行下一个节点
    - 拒绝：引擎回滚到上一个节点，将拒绝原因发送给 sub agent 重新执行
    """

    async def _approve_node(node_id: str, approved: bool, feedback: str = "") -> str:
        workflow_id, task_id, err = _require_binding(session_manager, "审批")
        if err:
            return err
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        result = workflow_manager.approve_node(
            workflow_id=workflow_id,
            task_id=task_id,
            node_id=node_id,
            approved=approved,
            feedback=feedback,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name="approve_node",
        description=(
            "审批工作流节点的完成产出。"
            "当 sub agent 调用 complete_node_task 后，你会收到审批请求。"
            "使用此工具批准（approved=true）或拒绝（approved=false）节点产出。"
            "拒绝时请提供 feedback，帮助节点改进产出；最多可拒绝 3 次。"
        ),
        args_schema=ApproveNodeArgs,
        func=lambda **kw: None,
        coroutine=_approve_node,
    )


# ============================================================
# 新增 Args Models（Chat Main 可用的查询/操作工具）
# ============================================================

class ListWorkflowsArgs(BaseModel):
    """list_workflows 参数（无参数，占位）"""
    pass


class GetWorkflowArgs(BaseModel):
    """get_workflow 工具参数"""
    workflow_id: str = Field(description="工作流 ID")


class CreateAndAttachTaskArgs(BaseModel):
    """create_and_attach_task 工具参数"""
    workflow_id: str = Field(description="工作流 ID")
    parameter_values: dict[str, str] | None = Field(
        default=None,
        description="可选的参数值字典，key 对应变量定义中的唯一标识",
    )


class ListTasksArgs(BaseModel):
    """list_tasks 工具参数"""
    workflow_id: str = Field(
        default="",
        description="工作流 ID。不传则使用当前已绑定工作流",
    )
    status: str = Field(
        default="",
        description="按状态过滤：pending/running/completed/failed/stopped，空字符串表示全部",
    )
    limit: int = Field(
        default=20,
        description="返回条数上限",
    )


class GetTaskStatusArgs(BaseModel):
    """get_task_status 工具参数"""
    workflow_id: str = Field(
        default="",
        description="工作流 ID。不传则使用当前已绑定工作流",
    )
    task_id: str = Field(
        default="",
        description="任务 ID。不传则使用当前已绑定任务",
    )


class StopTaskArgs(BaseModel):
    """stop_task 工具参数"""
    workflow_id: str = Field(
        default="",
        description="工作流 ID。不传则使用当前已绑定工作流",
    )
    task_id: str = Field(
        default="",
        description="任务 ID。不传则使用当前已绑定任务",
    )


# ============================================================
# 新增工具工厂
# ============================================================

def create_list_workflows_tool(
    workflow_manager: "WorkflowManager",
) -> StructuredTool:
    """创建 list_workflows 工具 — 列出所有工作流定义。"""

    async def _list_workflows() -> str:
        try:
            workflows = workflow_manager.list_workflows()
            visible_workflows = []
            for workflow in workflows or []:
                workflow_id = workflow.get("workflow_id", "")
                if not workflow_id:
                    continue
                _, policy_error = _load_tool_visible_workflow(
                    workflow_manager,
                    workflow_id,
                )
                if policy_error:
                    continue
                visible_workflows.append(workflow)
            if not visible_workflows:
                return _ok(workflows=[], message="当前没有任何工作流定义")
            return _ok(
                workflows=visible_workflows,
                count=len(visible_workflows),
            )
        except Exception as e:
            logger.exception("list_workflows 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="list_workflows",
        description=(
            "列出所有工作流定义。返回名称、ID、节点数、版本、创建时间和运行状态。"
            "用于发现可用的工作流模板，选择后可用 create_and_attach_task 创建任务。"
        ),
        args_schema=ListWorkflowsArgs,
        func=lambda **kw: None,
        coroutine=_list_workflows,
    )


def create_get_workflow_tool(
    workflow_manager: "WorkflowManager",
) -> StructuredTool:
    """创建 get_workflow 工具 — 获取单个工作流详情（含节点和变量定义）。"""

    async def _get_workflow(workflow_id: str) -> str:
        try:
            wf_data, policy_error = _load_tool_visible_workflow(
                workflow_manager,
                workflow_id,
            )
            if policy_error:
                return policy_error
            if not wf_data:
                return _fail(f"工作流 {workflow_id} 不存在")
            definition = wf_data["definition"]
            nodes_info = []
            for n in definition.get("nodes", []):
                nodes_info.append({
                    "id": n.get("id", ""),
                    "label": n.get("label", ""),
                    "node_type": n.get("node_type", "agent"),
                    "agent_type": n.get("agent_type", ""),
                    "system_prompt_template": n.get("system_prompt_template", ""),
                    "first_message": n.get("first_message", ""),
                    "var_bindings": n.get("var_bindings", {}),
                    "node_params": n.get("node_params", {}),
                })
            variables = definition.get("variables", [])
            edges = definition.get("edges", [])
            return _ok(
                name=definition.get("name", ""),
                workflow_id=workflow_id,
                version=definition.get("version", 0),
                nodes=nodes_info,
                edges=[{"id": e.get("id", ""), "source": e.get("source", ""), "target": e.get("target", "")} for e in edges],
                variables=[
                    {"key": v.get("key", ""), "name": v.get("name", ""),
                     "type": v.get("type", "text"),
                     "required": v.get("required", False),
                     "default": v.get("default", ""),
                     "description": v.get("description", ""),
                     "options": v.get("options", [])}
                    for v in variables
                ],
                node_count=len(nodes_info),
                variable_count=len(variables),
            )
        except Exception as e:
            logger.exception("get_workflow 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="get_workflow",
        description=(
            "获取指定工作流的完整定义，包括节点列表（ID、标签、Agent 类型、system prompt、首条任务消息、变量绑定）、"
            "节点间的连线关系（edges）、以及全局变量定义（含 select 类型的可选项）。"
            "用于全面了解工作流的执行结构、各节点的行为指令和任务模板、以及需要填写的参数。"
        ),
        args_schema=GetWorkflowArgs,
        func=lambda **kw: None,
        coroutine=_get_workflow,
    )


def create_create_and_attach_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 create_and_attach_task 工具 — 创建 pre_running 任务并绑定当前 session。"""

    async def _create_and_attach_task(
        workflow_id: str, parameter_values: dict[str, str] | None = None,
    ) -> str:
        ctx = get_session_context()
        session_id = ctx.get("session_id", "")

        if not session_id:
            return _fail("无法获取当前会话 ID")

        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            result = workflow_manager.create_and_attach_task_for_session(
                workflow_id=workflow_id,
                session_id=session_id,
                parameter_values=parameter_values,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("create_and_attach_task 失败")
            return _fail(f"创建失败: {e}")

    return StructuredTool(
        name="create_and_attach_task",
        description=(
            "创建一个工作流任务并绑定到当前会话。"
            "创建后任务处于预启动状态（pre_running），"
            "你可以用 set_workflow_variable 填充变量，确认无误后用 start_workflow_task 启动执行。"
            "创建新任务会自动覆盖当前会话已有的工作流绑定。"
            "参数 parameter_values 可传入初始变量值（可选）。"
        ),
        args_schema=CreateAndAttachTaskArgs,
        func=lambda **kw: None,
        coroutine=_create_and_attach_task,
    )


def create_list_tasks_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 list_tasks 工具 — 列出工作流任务历史。"""

    async def _list_tasks(
        workflow_id: str = "", status: str = "", limit: int = 20,
    ) -> str:
        # 优先使用显式参数，否则从 session 对象读取绑定
        if not workflow_id:
            binding = _get_workflow_binding(session_manager)
            workflow_id = binding["workflow_id"]
        if not workflow_id:
            return _fail("请提供 workflow_id 或先通过 create_and_attach_task 绑定工作流")
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            result = workflow_manager.list_tasks(workflow_id, limit=limit, status=status)
            tasks = result["tasks"] if isinstance(result, dict) else (result or [])
            total = result.get("total", len(tasks)) if isinstance(result, dict) else len(tasks)
            return _ok(
                workflow_id=workflow_id,
                tasks=tasks,
                total=total,
                status_filter=status,
            )
        except Exception as e:
            logger.exception("list_tasks 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="list_tasks",
        description=(
            "列出指定工作流的任务历史记录，支持按状态过滤和条数限制。"
            "workflow_id 不传则使用当前已绑定的工作流。"
        ),
        args_schema=ListTasksArgs,
        func=lambda **kw: None,
        coroutine=_list_tasks,
    )


def create_get_task_status_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 get_task_status 工具 — 获取单个任务执行状态（含节点进度）。"""

    async def _get_task_status(workflow_id: str = "", task_id: str = "") -> str:
        # 优先显式参数，否则从 session 对象读取绑定
        if not workflow_id or not task_id:
            binding = _get_workflow_binding(session_manager)
            if not workflow_id:
                workflow_id = binding["workflow_id"]
            if not task_id:
                task_id = binding["task_id"]
        if not workflow_id or not task_id:
            return _fail("请提供 workflow_id/task_id 或先通过 create_and_attach_task 绑定工作流")
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            task = workflow_manager.get_task(workflow_id, task_id)
            if not task:
                return _fail(f"任务 {task_id} 不存在")

            nodes_summary = {}
            for nid, ns in task.get("node_states", {}).items():
                nodes_summary[nid] = {
                    "status": ns.get("status", "pending"),
                    "summary": ns.get("summary", ""),
                }
            return _ok(
                task_id=task_id,
                workflow_id=workflow_id,
                name=task.get("name", ""),
                status=task.get("status", "unknown"),
                current_node_id=task.get("current_node_id", ""),
                node_states=nodes_summary,
                started_at=task.get("started_at", ""),
                completed_at=task.get("completed_at", ""),
            )
        except Exception as e:
            logger.exception("get_task_status 失败")
            return _fail(f"查询失败: {e}")

    return StructuredTool(
        name="get_task_status",
        description=(
            "获取指定任务的最新执行状态，包括每个节点的状态和摘要。"
            "workflow_id/task_id 不传则使用当前已绑定的工作流和任务。"
        ),
        args_schema=GetTaskStatusArgs,
        func=lambda **kw: None,
        coroutine=_get_task_status,
    )


def create_stop_task_tool(
    workflow_manager: "WorkflowManager",
    session_manager: "SessionManager",
) -> StructuredTool:
    """创建 stop_task 工具 — 停止运行中的任务。"""

    async def _stop_task(workflow_id: str = "", task_id: str = "") -> str:
        # 优先显式参数，否则从 session 对象读取绑定
        if not workflow_id or not task_id:
            binding = _get_workflow_binding(session_manager)
            if not workflow_id:
                workflow_id = binding["workflow_id"]
            if not task_id:
                task_id = binding["task_id"]
        if not workflow_id or not task_id:
            return _fail("请提供 workflow_id/task_id 或先通过 create_and_attach_task 绑定工作流")
        policy_error = _ensure_tool_execution_allowed(workflow_manager, workflow_id)
        if policy_error:
            return policy_error

        try:
            result = await workflow_manager.stop_task(workflow_id, task_id)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("stop_task 失败")
            return _fail(f"停止失败: {e}")

    return StructuredTool(
        name="stop_task",
        description=(
            "停止一个正在运行的工作流任务。"
            "workflow_id/task_id 不传则使用当前已绑定的工作流和任务。"
        ),
        args_schema=StopTaskArgs,
        func=lambda **kw: None,
        coroutine=_stop_task,
    )
