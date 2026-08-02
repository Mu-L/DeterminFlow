"""
命令审批引擎 - 管理 execute_command 工具的命令安全审批

参考 Roo-Code:
- src/core/auto-approval/index.ts（决策状态机）
- src/core/auto-approval/commands.ts（最长前缀匹配）
- src/core/auto-approval/tools.ts（读写分类）

四种行为模式：
- allow_all: 所有命令直接执行
- approve_all: 所有命令需人工审批
- blacklist: 匹配黑名单的命令需审批，其余放行
- whitelist: 匹配白名单的命令放行，其余需审批

审批流程：
1. check_command() 判断是否需要审批
2. 需要审批时创建 ApprovalRequest，通过 EventBus 推送到前端
3. wait_for_approval() 异步等待用户操作（asyncio.Event）
4. 前端通过 REST API 调用 approve()/reject()
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import src.config as config

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """审批请求"""
    request_id: str
    session_id: str
    tool_name: str
    command: str
    workspace: str
    status: str = "pending"  # pending / approved / rejected / timeout
    created_at: str = ""
    expires_at: str = ""
    reason: str = ""
    # 内部使用：asyncio.Event 用于等待审批结果
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "command": self.command,
            "workspace": self.workspace,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "reason": self.reason,
        }


class ApprovalManager:
    """命令审批引擎"""

    def __init__(self):
        # 待审批请求池：request_id → ApprovalRequest
        self.pending_approvals: dict[str, ApprovalRequest] = {}
        logger.info("ApprovalManager 已初始化")

    def check_command(self, command: str, session_id: str) -> str:
        """检查命令是否需要审批

        这里不做具体的黑白名单检查（由 WorkspaceGuard 完成），
        仅根据 WorkspaceGuard 的结果决定后续流程。

        Returns:
            "allowed" | "needs_approval"
        """
        from src.core.workspace_guard import WorkspaceGuard
        guard = WorkspaceGuard()
        result = guard.validate_command("", command)
        if result.allowed:
            return "allowed"
        return "needs_approval"

    def request_approval(self, command: str, session_id: str,
                         tool_name: str = "execute_command",
                         workspace: str = "") -> ApprovalRequest:
        """创建审批请求

        Args:
            command: 要执行的命令
            session_id: 会话 ID
            tool_name: 工具名
            workspace: 工作目录

        Returns:
            ApprovalRequest 实例
        """
        request_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        timeout = config.CODING_APPROVAL_TIMEOUT
        expires_at = now + timedelta(seconds=timeout)

        request = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            tool_name=tool_name,
            command=command,
            workspace=workspace,
            status="pending",
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

        self.pending_approvals[request_id] = request

        # 通过 EventBus 推送审批请求到前端
        self._emit_approval_event(request)

        logger.info(f"审批请求已创建: {request_id}, command={command[:80]}")
        return request

    async def wait_for_approval(self, request_id: str, timeout: float | None = None) -> str:
        """异步等待审批结果

        Args:
            request_id: 审批请求 ID
            timeout: 超时秒数，默认从配置读取

        Returns:
            "approved" | "rejected" | "timeout"
        """
        request = self.pending_approvals.get(request_id)
        if not request:
            return "rejected"

        if timeout is None:
            timeout = float(config.CODING_APPROVAL_TIMEOUT)

        try:
            await asyncio.wait_for(request._event.wait(), timeout=timeout)
            return request.status
        except asyncio.TimeoutError:
            request.status = "timeout"
            self.pending_approvals.pop(request_id, None)
            self._emit_resolved_event(request)
            logger.warning(f"审批请求超时: {request_id}")
            return "timeout"

    def approve(self, request_id: str) -> bool:
        """批准审批请求"""
        request = self.pending_approvals.get(request_id)
        if not request or request.status != "pending":
            return False

        request.status = "approved"
        request._event.set()
        self.pending_approvals.pop(request_id, None)
        self._emit_resolved_event(request)
        logger.info(f"审批请求已批准: {request_id}")
        return True

    def reject(self, request_id: str, reason: str = "") -> bool:
        """拒绝审批请求"""
        request = self.pending_approvals.get(request_id)
        if not request or request.status != "pending":
            return False

        request.status = "rejected"
        request.reason = reason
        request._event.set()
        self.pending_approvals.pop(request_id, None)
        self._emit_resolved_event(request)
        logger.info(f"审批请求已拒绝: {request_id}, reason={reason}")
        return True

    def get_pending(self) -> list[dict]:
        """获取所有待审批请求"""
        self._cleanup_expired()
        return [r.to_dict() for r in self.pending_approvals.values() if r.status == "pending"]

    def _cleanup_expired(self) -> None:
        """清理已过期但未被 wait_for_approval 消费的审批请求，防止内存泄漏。"""
        now = datetime.now(timezone.utc)
        expired_ids = []
        for req_id, req in self.pending_approvals.items():
            if req.status != "pending":
                continue
            try:
                if datetime.fromisoformat(req.expires_at) < now:
                    req.status = "timeout"
                    expired_ids.append(req_id)
            except (ValueError, TypeError):
                continue
        for req_id in expired_ids:
            self.pending_approvals.pop(req_id, None)
            logger.info(f"自动清理过期审批请求: {req_id}")

    def update_config(self, mode: str | None = None,
                      blacklist: str | None = None,
                      whitelist: str | None = None) -> None:
        """更新审批配置（同时持久化到 .env 文件）"""
        updates: dict[str, str] = {}
        if mode is not None:
            updates["CODING_CMD_MODE"] = mode
        if blacklist is not None:
            updates["CODING_CMD_BLACKLIST"] = blacklist
        if whitelist is not None:
            updates["CODING_CMD_WHITELIST"] = whitelist
        if updates:
            config.update_config(updates, persist=True)
        logger.info(f"审批配置已更新: mode={config.CODING_CMD_MODE}")

    # ============================================================
    # 内部方法
    # ============================================================

    @staticmethod
    def _emit_event(event: dict) -> None:
        """通过 EventBus 推送事件（通用方法）。

        _emit_approval_event 和 _emit_resolved_event 共用此方法，
        消除重复的 try/except RuntimeError/Exception 模式。
        """
        try:
            from src.web.event_bus import event_bus
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.emit_event(event))
        except RuntimeError:
            logger.debug("无运行中的事件循环，跳过事件推送")
        except Exception as e:
            logger.error(f"推送事件失败: {e}")

    @classmethod
    def _emit_approval_event(cls, request: ApprovalRequest) -> None:
        """通过 EventBus 推送审批请求事件"""
        cls._emit_event({
            "type": "approval_request",
            **request.to_dict(),
        })

    @classmethod
    def _emit_resolved_event(cls, request: ApprovalRequest) -> None:
        """通过 EventBus 推送审批结果事件"""
        cls._emit_event({
            "type": "approval_resolved",
            "request_id": request.request_id,
            "result": request.status,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        })
