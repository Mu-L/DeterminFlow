"""Owner-scoped reusable Sub sessions for trusted Extensions."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.agent.session import AgentSession, _persistence_manager
from src.config import DETACHED_SESSION_MAX_CONCURRENT_INVOCATIONS

_SAFE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PROFILE = "detached_conversation"


@dataclass(frozen=True)
class DetachedSessionRef:
    session_id: str
    external_ref: str
    created: bool


class ExtensionSessionRuntime:
    """Generic, owner-scoped facade for reusable detached Sub conversations."""

    def __init__(
        self,
        session_manager,
        *,
        owner: str = "",
        semaphore: asyncio.Semaphore | None = None,
        ensure_lock: asyncio.Lock | None = None,
        invocation_locks: dict[str, asyncio.Lock] | None = None,
        invocation_users: dict[str, int] | None = None,
        invocation_locks_guard: asyncio.Lock | None = None,
    ) -> None:
        self._manager = session_manager
        self._owner = owner
        self._semaphore = semaphore or asyncio.Semaphore(
            DETACHED_SESSION_MAX_CONCURRENT_INVOCATIONS
        )
        self._ensure_lock = ensure_lock or asyncio.Lock()
        self._invocation_locks = (
            invocation_locks if invocation_locks is not None else {}
        )
        self._invocation_users = (
            invocation_users if invocation_users is not None else {}
        )
        self._invocation_locks_guard = invocation_locks_guard or asyncio.Lock()

    def for_owner(self, owner: str) -> ExtensionSessionRuntime:
        normalized = str(owner or "").strip()
        if not normalized:
            raise ValueError("Extension session owner 不能为空")
        return ExtensionSessionRuntime(
            self._manager,
            owner=normalized,
            semaphore=self._semaphore,
            ensure_lock=self._ensure_lock,
            invocation_locks=self._invocation_locks,
            invocation_users=self._invocation_users,
            invocation_locks_guard=self._invocation_locks_guard,
        )

    def _require_owner(self) -> str:
        if not self._owner:
            raise RuntimeError("ExtensionSessionRuntime 尚未绑定 owner")
        return self._owner

    @staticmethod
    def _validate_ref(value: str, field: str) -> str:
        normalized = str(value or "").strip()
        if not _SAFE_REF.fullmatch(normalized):
            raise ValueError(f"{field} 必须是 1-128 位安全标识")
        return normalized

    def _find(self, external_ref: str):
        owner = self._require_owner()
        for session in self._manager.sessions.values():
            if (
                getattr(session, "lifecycle_profile", "") == _PROFILE
                and getattr(session, "resource_owner", "") == owner
                and getattr(session, "external_ref", "") == external_ref
            ):
                return session
        for metadata in self._manager._session_catalog.values():
            if (
                metadata.lifecycle_profile == _PROFILE
                and metadata.resource_owner == owner
                and metadata.external_ref == external_ref
            ):
                return metadata
        return None

    @staticmethod
    def _assert_binding(
        session,
        *,
        owner: str,
        agent_type: str | None = None,
        scope_hash: str | None = None,
    ) -> None:
        if (
            getattr(session, "lifecycle_profile", "") != _PROFILE
            or getattr(session, "resource_owner", "") != owner
        ):
            raise PermissionError("Detached session owner 不匹配")
        if agent_type is not None and session.agent_type != agent_type:
            raise RuntimeError("Detached session Agent 定义不匹配")
        if scope_hash is not None and getattr(session, "scope_hash", "") != scope_hash:
            raise PermissionError("Detached session scope 不匹配")

    def _configure_graph(self, session: AgentSession) -> None:
        from src.agent.definition import get_agent_definition
        from src.core.llm_client import create_llm

        agent_def = get_agent_definition(session.agent_type)
        if agent_def is None:
            raise RuntimeError(
                f"Detached session Agent 定义不存在: {session.agent_type}"
            )
        assembler = getattr(self._manager, "_tool_assembler", None)
        tools = []
        if assembler is not None:
            tools = assembler.build(
                session.agent_type,
                is_workflow_node=False,
                agent_definition=agent_def,
                workspace_path="",
                enable_complete_node_task=False,
                enable_reject_upstream=False,
            )
        llm = create_llm(
            model_override=session.model_id or agent_def.model,
            streaming=True,
            model_params=session.model_params or agent_def.model_params,
        )
        session.setup_graph(llm=llm, tools=tools)
        session.start_consumer()
        self._manager.register_runtime_session(session)

    async def ensure_detached(
        self,
        *,
        external_ref: str,
        agent_type: str,
        scope_hash: str,
    ) -> DetachedSessionRef:
        owner = self._require_owner()
        external_ref = self._validate_ref(external_ref, "external_ref")
        scope_hash = self._validate_ref(scope_hash, "scope_hash")
        async with self._ensure_lock:
            existing = self._find(external_ref)
            if existing is not None:
                self._assert_binding(
                    existing,
                    owner=owner,
                    agent_type=agent_type,
                    scope_hash=scope_hash,
                )
                return DetachedSessionRef(existing.session_id, external_ref, False)

            from src.agent.definition import get_agent_definition

            agent_def = get_agent_definition(agent_type)
            if agent_def is None:
                raise RuntimeError(f"Detached session Agent 定义不存在: {agent_type}")
            session = AgentSession(
                session_id=uuid.uuid4().hex,
                session_type="sub",
                parent_id=None,
                task_description="Detached extension conversation",
                system_prompt="",
                agent_type=agent_type,
                runtime_scope="interactive",
                model_params=agent_def.model_params,
                lifecycle_profile=_PROFILE,
                resource_owner=owner,
                external_ref=external_ref,
                scope_hash=scope_hash,
            )
            session.status = "completed"
            builder = getattr(self._manager, "_prompt_builder", None)
            if builder is None:
                raise RuntimeError("SessionManager 缺少 PromptBuilder")
            session.system_prompt = builder.build(
                agent_type,
                session=session,
                custom_append=agent_def.system_prompt_template or "",
                is_workflow=False,
                upstream_summary="",
            )
            session.model_id = agent_def.model
            self._configure_graph(session)
            await session.async_save(force=True, strict=True)
            return DetachedSessionRef(session.session_id, external_ref, True)

    def _load_owned(self, session_id: str) -> AgentSession:
        owner = self._require_owner()
        session = self._manager.get_session(session_id)
        if session is None:
            raise LookupError(f"Detached session 不存在: {session_id}")
        self._assert_binding(session, owner=owner)
        if session.status == "error":
            raise RuntimeError("Detached session 处于 error 状态")
        if session.compiled_graph is None:
            self._configure_graph(session)
        return session

    @asynccontextmanager
    async def _serialized_invocation(self, session_id: str) -> AsyncIterator[None]:
        async with self._invocation_locks_guard:
            invocation_lock = self._invocation_locks.setdefault(
                session_id,
                asyncio.Lock(),
            )
            self._invocation_users[session_id] = (
                self._invocation_users.get(session_id, 0) + 1
            )
        try:
            async with invocation_lock:
                yield
        finally:
            async with self._invocation_locks_guard:
                remaining = self._invocation_users[session_id] - 1
                if remaining:
                    self._invocation_users[session_id] = remaining
                else:
                    self._invocation_users.pop(session_id, None)
                    self._invocation_locks.pop(session_id, None)

    async def invoke(
        self,
        session_id: str,
        message: str,
        *,
        event_callback: Callable[[dict], Awaitable[None]] | None = None,
        max_rounds: int | None = None,
    ) -> str:
        async with self._serialized_invocation(session_id), self._semaphore:
            session = self._load_owned(session_id)
            try:
                return await session.send_message(
                    content=message,
                    event_callback=event_callback,
                    max_rounds=max_rounds,
                )
            finally:
                await self._deactivate(session)

    async def _deactivate(self, session: AgentSession) -> None:
        await session.async_save(force=True, strict=True)
        await session.stop_consumer()
        session.compiled_graph = None
        session.tools = []
        self._manager._session_catalog.upsert_session(session)
        self._manager.sessions.pop(session.session_id, None)
        self._manager._cold_session_lru.pop(session.session_id, None)
        _persistence_manager.unregister(session.session_id)

    async def delete(self, session_id: str) -> None:
        async with self._serialized_invocation(session_id):
            await self._delete_unlocked(session_id)

    async def _delete_unlocked(self, session_id: str) -> None:
        owner = self._require_owner()
        session = self._manager.get_session(session_id)
        if session is None:
            raise LookupError(f"Detached session 不存在: {session_id}")
        self._assert_binding(session, owner=owner)
        result = await self._manager.delete_session(session.session_id)
        if not result.get("success"):
            raise RuntimeError(result.get("message") or "Detached session 删除失败")

    async def delete_detached(
        self,
        *,
        external_ref: str,
        scope_hash: str | None = None,
    ) -> bool:
        owner = self._require_owner()
        external_ref = self._validate_ref(external_ref, "external_ref")
        if scope_hash is not None:
            scope_hash = self._validate_ref(scope_hash, "scope_hash")
        async with self._ensure_lock:
            existing = self._find(external_ref)
            if existing is None:
                return False
            self._assert_binding(
                existing,
                owner=owner,
                scope_hash=scope_hash,
            )
            session_id = existing.session_id
            async with self._serialized_invocation(session_id):
                await self._delete_unlocked(session_id)
            return True
