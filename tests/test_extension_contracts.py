from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from src.agent.definition import AgentDefinition
from src.extension_api import ExtensionManifest, WorkflowRuntime
from src.extension_api.registrar import ExtensionContributions, ExtensionRegistrar, OwnedPath
from src.extension_host import LayeredJsonConfig
from src.prompts.manager import PromptManager
from src.tools.prompt_tools import create_prompt_tools
from src.tools.registry import ToolRegistry
from src.workflow.nodes import BaseNodePlugin, NodeRegistry
from src.workflow.runtime import WorkflowRuntimeFacade


class _FakeWorkflowManager:
    def __init__(self):
        self.created = None

    def get_workflow(self, workflow_id):
        return {"definition": {"workflow_id": workflow_id}}

    def get_workflow_execution_identity(self, workflow_id):
        return {
            "schema_version": "workflow_execution_identity.v1",
            "workflow_id": workflow_id,
            "definition_version": 1,
            "inline_scripts": [],
        }

    def create_task(self, workflow_id, **kwargs):
        self.created = (workflow_id, kwargs)
        return {"task_id": "task-1"}

    async def run_task(self, workflow_id, task_id):
        return {"success": True, "workflow_id": workflow_id, "task_id": task_id}

    async def stop_task(self, workflow_id, task_id):
        return {"success": True}

    async def retry_node(
        self, workflow_id, task_id, node_id, expected_attempt_count,
    ):
        return {
            "success": True,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "node_id": node_id,
            "expected_attempt_count": expected_attempt_count,
        }

    async def skip_node(
        self, workflow_id, task_id, node_id, expected_attempt_count,
    ):
        return {
            "success": True,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "node_id": node_id,
            "expected_attempt_count": expected_attempt_count,
            "status": "skipped",
        }

    def get_task(self, workflow_id, task_id):
        return {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "snapshot_definition": {
                "nodes": [{"id": "writer", "agent_type": "demo.writer"}],
            },
            "node_states": {
                "writer": {
                    "status": "completed",
                    "token_usage": {
                        "demo:model": {
                            "prompt_tokens": 2,
                            "completion_tokens": 3,
                            "total_tokens": 5,
                            "call_count": 1,
                        }
                    },
                    "token_usage_calls": [{
                        "call_id": "session-1:1",
                        "timestamp": "2026-07-18T04:00:00+00:00",
                        "provider": "demo",
                        "model": "model",
                        "model_id": "demo:model",
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                        "cached_tokens": 0,
                        "reasoning_tokens": 0,
                        "call_count": 1,
                        "call_index": 1,
                        "session_id": "session-1",
                    }],
                }
            },
        }


def test_workflow_runtime_facade_exposes_execution_operations():
    manager = _FakeWorkflowManager()
    runtime = WorkflowRuntimeFacade(manager)

    assert isinstance(runtime, WorkflowRuntime)
    assert runtime.get_workflow("wf-demo")["definition"]["workflow_id"] == "wf-demo"
    assert runtime.get_workflow_execution_identity("wf-demo") == {
        "schema_version": "workflow_execution_identity.v1",
        "workflow_id": "wf-demo",
        "definition_version": 1,
        "inline_scripts": [],
    }
    assert runtime.create_task("wf-demo", parameter_values={"topic": "demo"}) == {
        "task_id": "task-1"
    }
    assert manager.created == ("wf-demo", {"parameter_values": {"topic": "demo"},
                                           "disabled_node_ids": None,
                                           "workspace_override": None,
                                           "scheme_id": None,
                                           "selected_node_ids": None})
    assert asyncio.run(runtime.run_task("wf-demo", "task-1"))["success"] is True
    assert asyncio.run(runtime.stop_task("wf-demo", "task-1"))["success"] is True
    assert asyncio.run(runtime.retry_node(
        "wf-demo", "task-1", "writer", 3,
    ))["node_id"] == "writer"
    assert asyncio.run(runtime.skip_node(
        "wf-demo", "task-1", "writer", 3,
    ))["status"] == "skipped"
    usage = runtime.get_task_token_usage("wf-demo", "task-1")
    assert usage["total"]["total_tokens"] == 5
    assert usage["workflow_id"] == "wf-demo"
    assert usage["task_id"] == "task-1"
    assert usage["calls"][0]["call_id"] == "session-1:1"
    assert usage["cost_status"] == "unpriced"
    assert usage["nodes"][0]["agent_type"] == "demo.writer"


def test_workflow_runtime_facade_exposes_non_sensitive_effective_agent_definition(
    monkeypatch,
):
    definition = AgentDefinition(
        agent_type="demo.writer",
        description="not part of the execution projection",
        prompt_template="writer",
        tools=["read_file"],
        model="openai:test",
        max_turns=12,
        system_prompt_template="private prompt body",
        extension_options={"provider": {"api_key": "never-expose"}},  # pragma: allowlist secret
        model_params={
            "temperature": 0.2,
            "response_format": None,
            "api_key": "never-expose",  # pragma: allowlist secret
        },
    )
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: definition if agent_type == definition.agent_type else None,
    )
    class PromptManagerStub:
        def get_sections(self, prompt_template):
            return [
                {"name": prompt_template, "content": "template body", "enabled": True}
            ]

    runtime = WorkflowRuntimeFacade(
        _FakeWorkflowManager(),
        prompt_manager=PromptManagerStub(),
    )

    effective = runtime.get_effective_agent_definition("demo.writer")

    assert effective["schema_version"] == "effective_agent_definition.v3"
    assert effective["model_params"] == {
        "temperature": 0.2,
        "response_format": None,
        "api_key": "[REDACTED]",
    }
    assert len(effective["system_prompt_template_sha256"]) == 64
    assert len(effective["extension_options_sha256"]) == 64
    assert len(effective["prompt_template_sections_sha256"]) == 64
    assert len(effective["static_base_system_prompt_sha256"]) == 64
    assert len(effective["model_runtime_sha256"]) == 64
    assert "system_prompt_template" not in effective
    assert "extension_options" not in effective
    assert "never-expose" not in json.dumps(effective)
    assert runtime.get_effective_agent_definition("missing") is None


def test_workflow_runtime_fails_closed_without_layered_prompt_manager(monkeypatch):
    definition = AgentDefinition(
        agent_type="extension.writer",
        description="extension prompt provenance test",
        prompt_template="extension-writer",
        model="openai:test",
    )
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: definition if agent_type == definition.agent_type else None,
    )
    runtime = WorkflowRuntimeFacade(_FakeWorkflowManager())

    with pytest.raises(RuntimeError, match="layered PromptManager"):
        runtime.get_effective_agent_definition("extension.writer")


def test_workflow_runtime_uses_injected_layered_prompt_manager(monkeypatch):
    definition = AgentDefinition(
        agent_type="extension.writer",
        description="extension prompt provenance test",
        prompt_template="extension-writer",
        model="openai:test",
    )
    monkeypatch.setattr(
        "src.agent.definition.get_agent_definition",
        lambda agent_type: definition if agent_type == definition.agent_type else None,
    )

    class LayeredPromptManager:
        def __init__(self):
            self.requested = []

        def get_sections(self, prompt_template):
            self.requested.append(prompt_template)
            return [{"name": "extension", "content": "layered", "enabled": True}]

    prompt_manager = LayeredPromptManager()
    runtime = WorkflowRuntimeFacade(
        _FakeWorkflowManager(),
        prompt_manager=prompt_manager,
    )

    effective = runtime.get_effective_agent_definition("extension.writer")

    assert prompt_manager.requested == ["extension-writer"] * 2
    assert len(effective["prompt_template_sections_sha256"]) == 64
    assert len(effective["static_base_system_prompt_sha256"]) == 64


def test_workflow_node_registry_rejects_cross_owner_conflicts():
    class FirstNode(BaseNodePlugin):
        node_type = "demo"

    class SecondNode(BaseNodePlugin):
        node_type = "demo"

    registry = NodeRegistry()
    registry.register(FirstNode, owner="extension-one")

    with pytest.raises(ValueError, match="extension-one vs extension-two"):
        registry.register(SecondNode, owner="extension-two")

    registry.unregister_owner("extension-one")
    assert registry.get("demo") is None


def test_plugin_registrar_does_not_expose_workflow_node_extension() -> None:
    registrar = ExtensionRegistrar(
        ExtensionManifest(
            extension_id="example-plugin",
            name="Example Plugin",
            version="1.0.0",
        ),
        ExtensionContributions(),
    )

    assert not hasattr(registrar, "add_workflow_node")


def test_tool_registry_unregister_owner_keeps_core_entries(tmp_path: Path):
    registry = ToolRegistry(str(tmp_path / "missing-groups.json"))
    registry.register("core-tool", "core", {}, owner="core")
    registry.register_group(
        {
            "id": "extension-group",
            "name": "Extension",
            "description": "",
            "tool_ids": ["extension-tool"],
        },
        owner="extension",
    )
    registry.register(
        "extension-tool",
        "extension",
        {},
        factory=lambda **deps: None,
        owner="extension",
    )

    registry.unregister_owner("extension")

    assert registry.get_tool("core-tool") is not None
    assert registry.get_tool("extension-tool") is None
    assert registry.get_factory_names() == set()
    assert {group["id"] for group in registry.get_groups()} == {"default"}


def test_tool_registry_guards_instantiated_extension_tool_after_unregister(
    tmp_path: Path,
):
    registry = ToolRegistry(str(tmp_path / "missing-groups.json"))

    def add_one(value: int) -> int:
        return value + 1

    source_tool = StructuredTool.from_function(
        func=add_one,
        name="extension-add-one",
        description="Add one to a value.",
    )
    registry.register_from_structured_tool(
        source_tool,
        factory=lambda **deps: source_tool,
        owner="extension-one",
    )

    guarded_tool = registry.instantiate("extension-add-one")

    assert guarded_tool is not None
    assert guarded_tool is not source_tool
    assert guarded_tool.name == source_tool.name
    assert guarded_tool.description == source_tool.description
    assert guarded_tool.args_schema is source_tool.args_schema
    assert guarded_tool.coroutine is None
    assert guarded_tool.invoke({"value": 4}) == 5

    registry.unregister_owner("extension-one")

    with pytest.raises(RuntimeError, match="owner 'extension-one' 已注销或发生变更"):
        guarded_tool.invoke({"value": 4})


def test_tool_registry_guards_async_tool_when_registered_owner_changes(
    tmp_path: Path,
):
    registry = ToolRegistry(str(tmp_path / "missing-groups.json"))

    async def multiply(value: int) -> int:
        return value * 3

    source_tool = StructuredTool.from_function(
        coroutine=multiply,
        name="remote-multiply",
        description="Multiply a value.",
    )
    registry.register_from_structured_tool(
        source_tool,
        factory=lambda **deps: source_tool,
        owner="mcp:first",
    )

    guarded_tool = registry.instantiate("remote-multiply")

    assert guarded_tool is not None
    assert guarded_tool.func is None
    assert guarded_tool.coroutine is not None
    assert asyncio.run(guarded_tool.ainvoke({"value": 4})) == 12

    registry.unregister_owner("mcp:first")
    registry.register_from_structured_tool(
        source_tool,
        factory=lambda **deps: source_tool,
        owner="mcp:second",
    )

    with pytest.raises(RuntimeError, match="owner 'mcp:first' 已注销或发生变更"):
        asyncio.run(guarded_tool.ainvoke({"value": 4}))


def test_tool_registry_keeps_core_tool_instance_unwrapped(tmp_path: Path):
    registry = ToolRegistry(str(tmp_path / "missing-groups.json"))

    def identity(value: int) -> int:
        return value

    source_tool = StructuredTool.from_function(
        func=identity,
        name="core-identity",
        description="Return the value.",
    )
    registry.register_from_structured_tool(
        source_tool,
        factory=lambda **deps: source_tool,
        owner="core",
    )

    assert registry.instantiate("core-identity") is source_tool


def test_prompt_tools_use_layered_extension_config(tmp_path: Path):
    config_file = tmp_path / "prompts.json"
    extension_file = tmp_path / "novel-prompts.json"
    config_file.write_text(
        json.dumps({
            "agents": {
                "main": {
                    "sections": [{"name": "core", "content": "core", "order": 1}],
                    "preambles": {},
                }
            }
        }),
        encoding="utf-8",
    )
    extension_file.write_text(
        json.dumps({
            "agents": {
                "novel.writer": {
                    "sections": [{"name": "role", "content": "writer", "order": 1}],
                    "preambles": {},
                }
            }
        }),
        encoding="utf-8",
    )
    store = LayeredJsonConfig(
        config_file,
        [OwnedPath("novel-workflows", extension_file)],
        dict_sections=["agents"],
    )
    manager = PromptManager(
        config_file=config_file,
        cache_file=tmp_path / "prompt-cache.json",
        history_file=tmp_path / "prompt-history.json",
        config_store=store,
    )
    tools = {tool.name: tool for tool in create_prompt_tools(manager)}

    listed = json.loads(tools["list_agent_types"].invoke({}))
    novel_prompt = json.loads(
        tools["get_system_prompt"].invoke({"agent_type": "novel.writer"})
    )

    assert listed["agent_types"] == ["main", "novel.writer"]
    assert novel_prompt["data"]["sections"][0]["content"] == "writer"
