"""
ApprovalNode — 审批节点插件。

在人工审批模式下阻塞工作流，等待用户在 UI 上审核指定文件并作出通过/驳回决定。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .base import BaseNodePlugin, NodeContext, NodeResult

logger = logging.getLogger(__name__)


class ApprovalNode(BaseNodePlugin):
    node_type = "approval"
    label = "审批节点"
    default_icon = "check-square"

    @property
    def params_schema(self) -> list[dict]:
        return [
            {
                "key": "file_paths",
                "label": "要展示的文件路径",
                "type": "textarea",
                "required": False,
                "default": "",
                "placeholder": "/path/to/file.md\n/path/to/another.md",
                "description": "每行一个文件路径（相对于工作流 workspace 根目录）。审批人在运行时可以查看这些文件的内容。",
            },
            {
                "key": "rejection_reason_placeholder",
                "label": "驳回原因输入框提示文案",
                "type": "text",
                "required": False,
                "default": "请输入驳回原因...",
                "description": "审批界面驳回原因输入框的 placeholder 提示文字。实际驳回原因由审批人实时填写。",
            },
        ]

    async def execute(self, ctx: NodeContext) -> NodeResult:
        """执行审批节点：读取文件 → 推送前端 → 等待人工决策。"""
        node_def = ctx.node_def
        node_state = ctx.node_state
        shared_ws = ctx.shared_ws

        # 读取配置的文件路径
        file_paths_raw = node_def.node_params.get("file_paths", "")
        files: list[dict] = []

        if file_paths_raw.strip() and shared_ws:
            for line in file_paths_raw.strip().split("\n"):
                p = line.strip()
                if not p:
                    continue
                full_path = (shared_ws / p).resolve()
                try:
                    if full_path.is_relative_to(shared_ws) and full_path.exists():
                        content = full_path.read_text(encoding="utf-8")
                        files.append({
                            "path": p,
                            "content": content,
                            "exists": True,
                        })
                    else:
                        files.append({
                            "path": p,
                            "content": f"[文件不存在或超出 workspace: {p}]",
                            "exists": False,
                        })
                except Exception as e:
                    files.append({
                        "path": p,
                        "content": f"[读取失败: {str(e)}]",
                        "exists": False,
                    })

        # 在任何可观察的 waiting 状态或审批事件之前先注册 waiter，避免
        # 前端即时批准时 resolve_approval 尚找不到等待器而丢失决策。
        from src.workflow.engine import get_current_engine
        engine = get_current_engine()
        if engine is None:
            return NodeResult(status="failed", error="WorkflowEngine 未初始化")
        approval_key = (ctx.workflow_id, ctx.task_id, node_def.id)
        approval_event = asyncio.Event()
        engine._pending_approvals[approval_key] = approval_event
        engine._approval_results[approval_key] = {}

        node_state.status = "waiting_approval"
        try:
            if ctx.checkpoint:
                await ctx.checkpoint()
        except BaseException:
            engine._approval_results.pop(approval_key, None)
            engine._pending_approvals.pop(approval_key, None)
            raise

        # 推送到前端
        try:
            from src.web.event_bus import event_bus
            await event_bus.emit_event({
                "type": "wf_approval_required",
                "workflow_id": ctx.workflow_id,
                "task_id": ctx.task_id,
                "node_id": node_def.id,
                "node_label": node_def.label,
                "files": files,
                "placeholder": node_def.node_params.get(
                    "rejection_reason_placeholder", "请输入驳回原因...",
                ),
            })
        except Exception as e:
            engine._approval_results.pop(approval_key, None)
            engine._pending_approvals.pop(approval_key, None)
            logger.exception(f"推送审批事件失败: {e}")
            return NodeResult(status="failed", error=f"推送审批事件失败: {e}")

        # 推送中间状态更新，让前端知道审批节点正在等待
        try:
            from src.web.event_bus import event_bus
            await event_bus.emit_event({
                "type": "wf_task_update",
                "workflow_id": ctx.workflow_id,
                "task_id": ctx.task_id,
                "status": "running",
                "current_node_id": node_def.id,
                "node_states": {
                    node_def.id: {
                        "node_id": node_def.id,
                        "status": "waiting_approval",
                        "session_id": "",
                        "started_at": node_state.started_at,
                        "completed_at": None,
                        "summary": "",
                        "error": "",
                        "rejection_count": 0,
                        "rejection_reason": "",
                    },
                },
                "started_at": None,
                "completed_at": None,
            })
        except Exception:
            pass

        # 等待人工决策（通过 resolve-approval REST API 解除阻塞）
        result_: dict = {}
        try:
            try:
                # 审批节点超时时间设为 24 小时（人工审批不应太急）
                await asyncio.wait_for(approval_event.wait(), timeout=86400)
            except asyncio.TimeoutError:
                logger.warning(f"审批节点超时: {node_def.id}")
                engine._approval_results[approval_key] = {
                    "approved": False,
                    "feedback": "审批超时",
                    "infrastructure_failure": True,
                }
        finally:
            result_ = engine._approval_results.pop(approval_key, {})
            engine._pending_approvals.pop(approval_key, None)
        approved = result_.get("approved", False)
        reason = result_.get("feedback", "")

        if result_.get("infrastructure_failure"):
            return NodeResult(status="failed", error=reason or "审批等待失败")

        if approved:
            logger.info(f"审批节点通过: {node_def.id}")
            return NodeResult(
                summary=f"审批通过 (节点: {node_def.label})",
                status="success",
            )
        else:
            logger.info(f"审批节点驳回: {node_def.id}, reason={reason}")
            node_state.rejection_reason = reason
            return NodeResult(
                summary=f"审批驳回，原因: {reason}",
                status="rejected",
                error=reason,
            )
