from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from src.session.context import set_session_context
from src.workflow.tools import (
    create_approve_node_tool,
    create_create_and_attach_task_tool,
    create_get_task_status_tool,
    create_get_workflow_tool,
    create_list_tasks_tool,
    create_list_workflows_tool,
    create_set_workflow_variable_tool,
    create_start_workflow_task_tool,
    create_stop_task_tool,
)


class _ToolPolicyManager:
    def __init__(self, policies: dict[str, str]):
        self.policies = policies
        self.action_calls: list[str] = []
        self.create_kwargs: list[dict] = []

    def list_workflows(self):
        return [
            {"workflow_id": workflow_id, "name": workflow_id}
            for workflow_id in self.policies
        ]

    def get_workflow(self, workflow_id: str):
        policy = self.policies.get(workflow_id)
        if policy is None:
            return None
        return {
            "definition": {
                "workflow_id": workflow_id,
                "name": workflow_id,
                "http_execution_policy": policy,
                "nodes": [],
                "edges": [],
                "variables": [],
            }
        }

    def _called(self, action: str):
        self.action_calls.append(action)
        return {"success": True, "action": action}

    def set_workflow_variable(self, **kwargs):
        return self._called("set_workflow_variable")

    async def start_pre_running_task(self, **kwargs):
        return self._called("start_pre_running_task")

    def approve_node(self, **kwargs):
        return self._called("approve_node")

    def create_and_attach_task_for_session(self, **kwargs):
        self.create_kwargs.append(kwargs)
        return self._called("create_and_attach_task_for_session")

    def list_all_tasks(self, *args, **kwargs):
        self._called("list_all_tasks")
        return {"tasks": [], "total": 0}

    def get_task(self, *args, **kwargs):
        return {
            "name": "task",
            "status": "running",
            "node_states": {},
            "main_session_id": "session-1",
        }

    async def stop_task(self, *args, **kwargs):
        return self._called("stop_task")


def _session_manager(workflow_id: str):
    return SimpleNamespace(
        sessions={
            "session-1": SimpleNamespace(
                workflow_id=workflow_id,
                task_id="task-1",
            )
        }
    )


def _invoke(tool, **kwargs) -> dict:
    assert tool.coroutine is not None
    return json.loads(asyncio.run(tool.coroutine(**kwargs)))


def test_list_workflows_hides_internal_only_definitions():
    manager = _ToolPolicyManager(
        {"wf-public": "public", "wf-internal": "internal_only"}
    )

    result = _invoke(create_list_workflows_tool(manager))

    assert result == {
        "success": True,
        "workflows": [{"workflow_id": "wf-public", "name": "wf-public"}],
        "count": 1,
    }
    assert manager.action_calls == []


def test_every_workflow_agent_tool_denies_internal_only_before_action_call():
    manager = _ToolPolicyManager({"wf-internal": "internal_only"})
    sessions = _session_manager("wf-internal")
    set_session_context(session_id="session-1")
    calls = (
        (create_get_workflow_tool(manager), {"workflow_id": "wf-internal"}),
        (
            create_create_and_attach_task_tool(manager, sessions),
            {"workflow_id": "wf-internal", "parameter_values": {"x": "1"}},
        ),
        (
            create_set_workflow_variable_tool(manager, sessions),
            {"key": "x", "value": "1"},
        ),
        (create_start_workflow_task_tool(manager, sessions), {}),
        (
            create_approve_node_tool(manager, sessions),
            {
                "node_id": "node-1",
                "approved": True,
                "feedback": "",
                "expected_attempt_count": 1,
            },
        ),
        (
            create_list_tasks_tool(manager, sessions),
            {"workflow_id": "wf-internal", "status": "", "limit": 20},
        ),
        (
            create_get_task_status_tool(manager, sessions),
            {"workflow_id": "wf-internal", "task_id": "task-1"},
        ),
        (
            create_stop_task_tool(manager, sessions),
            {"workflow_id": "wf-internal", "task_id": "task-1"},
        ),
    )

    for tool, kwargs in calls:
        result = _invoke(tool, **kwargs)
        assert result["success"] is False
        assert result["error"] == "workflow_internal_only"

    assert manager.action_calls == []


def test_public_workflow_agent_actions_keep_reaching_manager():
    manager = _ToolPolicyManager({"wf-public": "public"})
    sessions = _session_manager("wf-public")
    set_session_context(session_id="session-1")
    calls = (
        (
            create_create_and_attach_task_tool(manager, sessions),
            {
                "workflow_id": "wf-public",
                "parameter_values": None,
                "main_takeover": True,
            },
        ),
        (
            create_set_workflow_variable_tool(manager, sessions),
            {"key": "x", "value": "1"},
        ),
        (create_start_workflow_task_tool(manager, sessions), {}),
        (
            create_approve_node_tool(manager, sessions),
            {
                "node_id": "node-1",
                "approved": True,
                "feedback": "",
                "expected_attempt_count": 1,
            },
        ),
        (
            create_list_tasks_tool(manager, sessions),
            {"workflow_id": "wf-public", "status": "", "limit": 20},
        ),
        (
            create_get_task_status_tool(manager, sessions),
            {"workflow_id": "wf-public", "task_id": "task-1"},
        ),
        (
            create_stop_task_tool(manager, sessions),
            {"workflow_id": "wf-public", "task_id": "task-1"},
        ),
    )

    for tool, kwargs in calls:
        assert _invoke(tool, **kwargs)["success"] is True

    assert manager.action_calls == [
        "create_and_attach_task_for_session",
        "set_workflow_variable",
        "start_pre_running_task",
        "approve_node",
        "list_all_tasks",
        "stop_task",
    ]
    assert manager.create_kwargs == [{
        "workflow_id": "wf-public",
        "session_id": "session-1",
        "parameter_values": None,
        "scheme_id": None,
        "selected_node_ids": None,
        "workspace_mode": "task_isolated",
        "workspace_ref": None,
        "main_takeover": True,
    }]


def test_policy_lookup_failure_denies_action_before_manager_call():
    class _UnavailablePolicyManager(_ToolPolicyManager):
        def get_workflow(self, workflow_id: str):
            raise RuntimeError("definition store unavailable")

    manager = _UnavailablePolicyManager({"wf-public": "public"})
    sessions = _session_manager("wf-public")
    set_session_context(session_id="session-1")

    result = _invoke(
        create_stop_task_tool(manager, sessions),
        workflow_id="wf-public",
        task_id="task-1",
    )

    assert result["success"] is False
    assert result["error"] == "workflow_policy_unavailable"
    assert manager.action_calls == []


def test_unknown_tool_execution_policy_fails_closed():
    manager = _ToolPolicyManager({"wf-corrupt": "unexpected"})
    sessions = _session_manager("wf-corrupt")
    set_session_context(session_id="session-1")

    result = _invoke(
        create_stop_task_tool(manager, sessions),
        workflow_id="wf-corrupt",
        task_id="task-1",
    )

    assert result["success"] is False
    assert result["error"] == "workflow_execution_policy_invalid"
    assert manager.action_calls == []
