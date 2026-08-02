"""Stable workflow operations available to extensions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkflowRuntime(Protocol):
    """Narrow execution facade; workflow editing remains a Core concern."""

    def get_workflow(self, workflow_id: str) -> dict | None: ...

    def get_workflow_execution_identity(
        self,
        workflow_id: str,
    ) -> dict | None: ...

    def get_effective_agent_definition(self, agent_type: str) -> dict | None: ...

    def create_task(
        self,
        workflow_id: str,
        *,
        parameter_values: dict[str, Any] | None = None,
        disabled_node_ids: list[str] | None = None,
        workspace_override: str | None = None,
        scheme_id: str | None = None,
        selected_node_ids: list[str] | None = None,
    ) -> dict | None: ...

    async def run_task(self, workflow_id: str, task_id: str) -> dict: ...

    async def stop_task(self, workflow_id: str, task_id: str) -> dict: ...

    async def retry_node(
        self, workflow_id: str, task_id: str, node_id: str,
        expected_attempt_count: int,
    ) -> dict: ...

    async def skip_node(
        self, workflow_id: str, task_id: str, node_id: str,
        expected_attempt_count: int,
    ) -> dict: ...

    def get_task(self, workflow_id: str, task_id: str) -> dict | None: ...

    def get_task_token_usage(self, workflow_id: str, task_id: str) -> dict | None: ...
