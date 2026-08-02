"""Workflow HTTP access policy helpers shared by route modules."""

from __future__ import annotations

from fastapi import HTTPException, Request


def _get_manager(request: Request):
    """从 app state 中获取 WorkflowManager。"""
    manager = request.app.state.workflow_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="WorkflowManager 未初始化")
    return manager


def _ensure_workflow_writable(request: Request, workflow_id: str):
    """拒绝修改非运行扩展所拥有的工作流。"""
    manager = _get_manager(request)
    if not manager.is_workflow_owner_enabled(workflow_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"工作流 {workflow_id} 所属扩展未处于运行状态，"
                "当前仅允许读取历史任务"
            ),
        )
    return manager


def _ensure_http_mutation_allowed(request: Request, workflow_id: str):
    """Keep service-owned workflows executable only through their business API."""
    manager = _get_manager(request)
    policy = manager.get_workflow_execution_policy(workflow_id)
    if policy in ("not_found", "public"):
        return manager
    if policy == "internal_only":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "workflow_internal_only",
                "message": (
                    f"Workflow {workflow_id} 只允许 Core 内部服务调用；"
                    "请使用所属业务 API"
                ),
            },
        )
    if policy not in (None, "", "public"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "workflow_execution_policy_invalid",
                "message": f"Workflow {workflow_id} 的执行策略无效",
            },
        )
    return manager
