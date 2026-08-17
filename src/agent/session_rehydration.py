"""终态 Workflow Agent Session 的有界按需恢复。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from src.agent.session import AgentSession
from src.config import WORKFLOWS_DIR

logger = logging.getLogger(__name__)

_TASK_TERMINAL_STATUSES = {"completed", "failed", "stopped", "cancelled"}
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_HISTORICAL_CONVERSATION_PROFILE = "historical_conversation"


class SessionRehydrationMixin:
    """把已完成的 Workflow Agent Session 恢复为独立历史续聊。"""

    sessions: dict[str, AgentSession]
    _session_catalog: Any
    _cold_session_lru: Any
    _historical_runtime_locks: dict[str, asyncio.Lock]

    def can_rehydrate_session(self, session: AgentSession) -> bool:
        """终态 Workflow Node 可以升级为独立交互续聊。"""
        profile = getattr(session, "lifecycle_profile", "task")
        if session.session_type != "sub" or session.status != "completed":
            return False
        if profile == _HISTORICAL_CONVERSATION_PROFILE:
            return True
        if getattr(session, "runtime_scope", "interactive") != "workflow":
            return False
        return self._workflow_task_is_terminal_for_rehydrate(session)

    @staticmethod
    def _workflow_task_is_terminal_for_rehydrate(session: AgentSession) -> bool:
        """首次升级前 fail-closed 核验原 Workflow Task 已进入终态。"""
        workflow_id = session.workflow_id
        task_id = session.task_id
        if (
            not workflow_id
            or not task_id
            or not _SAFE_ID_PATTERN.fullmatch(workflow_id)
            or not _SAFE_ID_PATTERN.fullmatch(task_id)
        ):
            return False
        task_file = WORKFLOWS_DIR / workflow_id / "tasks" / f"{task_id}.json"
        try:
            task_data = json.loads(task_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, TypeError, ValueError):
            return False
        return (
            isinstance(task_data, dict)
            and task_data.get("status") in _TASK_TERMINAL_STATUSES
        )

    async def _rehydrate_historical_session(
        self,
        session: AgentSession,
    ) -> AgentSession:
        """恢复通用 Agent Graph，但不恢复已终态 Task 的调度工具。"""
        if session.compiled_graph is not None:
            return session
        if not self.can_rehydrate_session(session):
            raise RuntimeError(f"会话 {session.session_id} 不支持按需恢复")

        from src.agent.definition import get_agent_definition
        from src.core.llm_client import create_llm

        agent_def = get_agent_definition(session.agent_type)
        if agent_def is None:
            raise RuntimeError(f"Agent 定义不存在: {session.agent_type}")
        assembler = getattr(self, "_tool_assembler", None)
        if assembler is None:
            raise RuntimeError("SessionManager 缺少 ToolAssembler")
        if not session.model_id:
            raise RuntimeError(
                f"历史会话 {session.session_id} 缺少已冻结模型，无法按需恢复"
            )

        tools = assembler.build(
            session.agent_type,
            is_workflow_node=False,
            agent_definition=agent_def,
            workspace_path=session.workspace_path or "",
            enable_complete_node_task=False,
            enable_reject_upstream=False,
        )
        llm = create_llm(
            model_override=session.model_id,
            streaming=True,
            model_params=session.model_params or {},
        )

        previous_scope = session.runtime_scope
        previous_profile = session.lifecycle_profile
        try:
            session.runtime_scope = "interactive"
            session.lifecycle_profile = _HISTORICAL_CONVERSATION_PROFILE
            session._on_node_complete = None
            session._on_auto_complete = None
            session._on_reject_upstream = None
            session._auto_flow = False
            session.setup_graph(llm=llm, tools=tools)
            session.start_consumer()
            self.register_runtime_session(session)
            await session.async_save(force=True, strict=True)
        except Exception:
            session.runtime_scope = previous_scope
            session.lifecycle_profile = previous_profile
            await session.stop_consumer()
            session.compiled_graph = None
            session.tools = []
            self._cold_session_lru[session.session_id] = None
            self._session_catalog.upsert_session(session)
            raise
        return session

    async def _deactivate_historical_session(self, session: AgentSession) -> None:
        """严格保存后卸载历史续聊 Graph，保持冷驻留上限。"""
        if session.lifecycle_profile != _HISTORICAL_CONVERSATION_PROFILE:
            return
        if session.invocation_active:
            return
        await session.stop_consumer()
        try:
            await session.async_save(force=True, strict=True)
        except Exception:
            session.start_consumer()
            logger.exception("历史续聊保存失败，保留热驻留: %s", session.session_id)
            return

        from src.agent.session import _persistence_manager

        session.compiled_graph = None
        session.tools = []
        self._session_catalog.upsert_session(session)
        self.sessions.pop(session.session_id, None)
        self._cold_session_lru.pop(session.session_id, None)
        _persistence_manager.unregister(session.session_id)

    @asynccontextmanager
    async def session_runtime(self, session_id: str):
        """为一次会话操作确保 Graph 可用，并在历史续聊后卸载。"""
        session = self.get_session(session_id)
        if session is None:
            yield None
            return
        managed = (
            session.lifecycle_profile == _HISTORICAL_CONVERSATION_PROFILE
            or (
                session.compiled_graph is None
                and self.can_rehydrate_session(session)
            )
        )
        if not managed:
            yield session
            return

        lock = self._historical_runtime_locks.setdefault(
            session_id, asyncio.Lock(),
        )
        async with lock:
            session = self.get_session(session_id)
            if session is None:
                yield None
                return
            if session.compiled_graph is None:
                session = await self._rehydrate_historical_session(session)
            try:
                yield session
            finally:
                await self._deactivate_historical_session(session)
