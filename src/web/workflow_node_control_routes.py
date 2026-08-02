"""与节点插件无关的工作流失败节点控制 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .workflow_routes import _ensure_http_mutation_allowed


router = APIRouter(prefix="/api/workflows", tags=["workflow-node-control"])


class NodeControlRequest(BaseModel):
    expected_attempt_count: int = Field(ge=0)


def _raise_control_error(result: dict) -> None:
    error = result.get("error", "node_control_failed")
    if error in {"task_not_found", "node_not_found"}:
        status_code = 404
    elif (
        error in {"node_control_conflict", "node_control_stale"}
        or "仅允许读取" in result.get("message", "")
    ):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "message": result.get("message", "节点操作失败"),
            "workflow_id": result.get("workflow_id"),
            "task_id": result.get("task_id"),
            "node_id": result.get("node_id"),
        },
    )


@router.post("/{workflow_id}/tasks/{task_id}/nodes/{node_id}/retry")
async def retry_failed_node(
    workflow_id: str,
    task_id: str,
    node_id: str,
    request: Request,
    body: NodeControlRequest,
):
    """使用原任务快照与参数，原地手动重试失败节点。"""
    manager = _ensure_http_mutation_allowed(request, workflow_id)
    result = await manager.retry_node(
        workflow_id,
        task_id,
        node_id,
        body.expected_attempt_count,
    )
    if not result.get("success"):
        _raise_control_error(result)
    return result


@router.post("/{workflow_id}/tasks/{task_id}/nodes/{node_id}/skip")
async def skip_failed_node(
    workflow_id: str,
    task_id: str,
    node_id: str,
    request: Request,
    body: NodeControlRequest,
):
    """清空失败节点的运行产出，标记 skipped 后继续原任务。"""
    manager = _ensure_http_mutation_allowed(request, workflow_id)
    result = await manager.skip_node(
        workflow_id,
        task_id,
        node_id,
        body.expected_attempt_count,
    )
    if not result.get("success"):
        _raise_control_error(result)
    return result
