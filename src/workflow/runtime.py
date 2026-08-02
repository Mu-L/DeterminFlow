"""Restricted workflow runtime exposed through the Extension API."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "auth_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
    }
)
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_auth_token",
    "_client_secret",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        _SENSITIVE_KEY_SUFFIXES
    )


def _semantic_identity_values(value: Any) -> Any:
    """Bind credentials to semantic identity without exposing their values."""
    if isinstance(value, dict):
        return {
            str(key): (
                {
                    "redacted": True,
                    "value_sha256": _canonical_sha256(item),
                }
                if _is_sensitive_key(key)
                else _semantic_identity_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_semantic_identity_values(item) for item in value]
    return value


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _redact_sensitive_values(value: Any) -> Any:
    """Keep runtime metadata auditable without exposing credential-like values."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def build_effective_agent_definition(
    agent_type: str,
    *,
    prompt_manager,
    prompt_builder=None,
    model_override: str | None = None,
) -> dict | None:
    """Build the execution-sensitive Agent identity used by runtime guards.

    ``model_override`` is part of the effective identity because Workflow Agent
    nodes may select a model independently from the Agent definition.
    """
    from src.agent.definition import get_agent_definition
    from src.core.model_manager import get_model_manager

    definition = get_agent_definition(agent_type)
    if definition is None:
        return None
    if prompt_manager is None:
        raise RuntimeError(
            "缺少进程级 layered PromptManager；"
            "拒绝生成可能错误的有效 Agent definition"
        )
    if prompt_builder is None:
        from src.session.prompt_builder import PromptBuilder

        prompt_builder = PromptBuilder(prompt_manager=prompt_manager)
    elif prompt_builder.prompt_manager is not prompt_manager:
        raise RuntimeError(
            "PromptBuilder 与 layered PromptManager 不一致；"
            "拒绝生成可能错误的有效 Agent definition"
        )
    prompt_sections = prompt_manager.get_sections(definition.prompt_template)
    static_base_prompt = prompt_builder.build_static_workflow_base(agent_type)
    model_manager = get_model_manager()
    effective_model = model_override or definition.model
    provider_id, separator, model_name = str(effective_model or "").partition(":")
    provider_config = (
        model_manager.get_provider(provider_id)
        if separator and model_name
        else None
    )
    model_runtime = {
        "provider_id": provider_id if separator and model_name else "",
        "model_name": model_name if separator else "",
        "provider_config": _semantic_identity_values(provider_config or {}),
        "default_params": _semantic_identity_values(
            model_manager.get_default_params()
        ),
        "retry_config": _semantic_identity_values(
            model_manager.get_retry_config()
        ),
    }
    return {
        "schema_version": "effective_agent_definition.v3",
        "agent_type": definition.agent_type,
        "prompt_template": definition.prompt_template,
        "tools": definition.tools,
        "disallowed_tools": definition.disallowed_tools,
        "model": effective_model,
        "max_turns": definition.max_turns,
        "copy_main_workspace": definition.copy_main_workspace,
        "visible_skill_group_ids": definition.visible_skill_group_ids,
        "visible_rule_group_ids": definition.visible_rule_group_ids,
        "include_skills": definition.include_skills,
        "include_rules": definition.include_rules,
        "available_for_sub_session": definition.available_for_sub_session,
        "model_params": _redact_sensitive_values(definition.model_params),
        "model_params_semantic_sha256": _canonical_sha256(
            _semantic_identity_values(definition.model_params)
        ),
        "prompt_template_sections_sha256": _canonical_sha256(prompt_sections),
        "static_base_system_prompt_sha256": _canonical_sha256(
            static_base_prompt
        ),
        "model_runtime_sha256": _canonical_sha256(model_runtime),
        "system_prompt_template_sha256": _canonical_sha256(
            definition.system_prompt_template
        ),
        "extension_options_sha256": _canonical_sha256(
            definition.extension_options
        ),
    }


def effective_agent_definition_sha256(definition: dict) -> str:
    """Return a stable digest for a non-sensitive effective definition."""
    return _canonical_sha256(definition)


class WorkflowRuntimeFacade:
    """Delegate execution-only operations without exposing manager internals."""

    def __init__(self, manager, *, prompt_manager=None, prompt_builder=None):
        self._manager = manager
        self._prompt_manager = prompt_manager
        self._prompt_builder = prompt_builder

    def get_workflow(self, workflow_id: str) -> dict | None:
        return self._manager.get_workflow(workflow_id)

    def get_workflow_execution_identity(self, workflow_id: str) -> dict | None:
        """Return non-sensitive identities for actual Core execution files."""
        return self._manager.get_workflow_execution_identity(workflow_id)

    def get_effective_agent_definition(self, agent_type: str) -> dict | None:
        """Return the resolved execution fields without prompt or option contents."""
        return build_effective_agent_definition(
            agent_type,
            prompt_manager=self._prompt_manager,
            prompt_builder=self._prompt_builder,
        )

    def create_task(
        self,
        workflow_id: str,
        *,
        parameter_values: dict[str, Any] | None = None,
        disabled_node_ids: list[str] | None = None,
        workspace_override: str | None = None,
        scheme_id: str | None = None,
        selected_node_ids: list[str] | None = None,
    ) -> dict | None:
        return self._manager.create_task(
            workflow_id,
            parameter_values=parameter_values,
            disabled_node_ids=disabled_node_ids,
            workspace_override=workspace_override,
            scheme_id=scheme_id,
            selected_node_ids=selected_node_ids,
        )

    async def run_task(self, workflow_id: str, task_id: str) -> dict:
        return await self._manager.run_task(workflow_id, task_id)

    async def stop_task(self, workflow_id: str, task_id: str) -> dict:
        return await self._manager.stop_task(workflow_id, task_id)

    async def retry_node(
        self, workflow_id: str, task_id: str, node_id: str,
        expected_attempt_count: int,
    ) -> dict:
        return await self._manager.retry_node(
            workflow_id, task_id, node_id, expected_attempt_count,
        )

    async def skip_node(
        self, workflow_id: str, task_id: str, node_id: str,
        expected_attempt_count: int,
    ) -> dict:
        return await self._manager.skip_node(
            workflow_id, task_id, node_id, expected_attempt_count,
        )

    def get_task(self, workflow_id: str, task_id: str) -> dict | None:
        return self._manager.get_task(workflow_id, task_id)

    def get_task_token_usage(self, workflow_id: str, task_id: str) -> dict | None:
        task = self._manager.get_task(workflow_id, task_id)
        if task is None:
            return None
        definition = task.get("snapshot_definition") or (
            self._manager.get_workflow(workflow_id) or {}
        ).get("definition", {})
        node_agent_map = {
            node.get("id", ""): node.get("agent_type", "unknown")
            for node in definition.get("nodes", [])
            if node.get("id")
        }
        from src.workflow.token_usage import aggregate_token_usage

        return {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "task_name": task.get("task_name", task.get("name", "")),
            "status": task.get("status", "unknown"),
            **aggregate_token_usage(task.get("node_states", {}), node_agent_map),
        }
