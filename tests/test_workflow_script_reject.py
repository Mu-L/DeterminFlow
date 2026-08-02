from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import src.config as config
from src.workflow.definition import (
    NodeExecutionState,
    WorkflowDef,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRunRecord,
    WorkflowTask,
)
from src.workflow.engine import WorkflowEngine
from src.workflow.failure_policy import activate_scheduled_retry
from src.workflow.nodes.base import NodeContext
from src.workflow.nodes.script import ScriptNode, _parse_reject_upstream
from src.workflow.token_usage import aggregate_token_usage


def test_parse_reject_upstream_with_target():
    parsed = _parse_reject_upstream(
        '<WF_REJECT_UPSTREAM target="agent_l1">字段缺失</WF_REJECT_UPSTREAM>'
    )

    assert parsed == ("字段缺失", "agent_l1")


def test_script_node_reject_protocol_calls_callback(tmp_path, monkeypatch):
    workflow_id = "wf-test-script-reject"
    script_dir = tmp_path / "workflows" / workflow_id / "script"
    script_dir.mkdir(parents=True)
    script_file = script_dir / "validator.py"
    script_file.write_text(
        "print('<WF_REJECT_UPSTREAM target=\"agent_l1\">plot_point 不能为空</WF_REJECT_UPSTREAM>')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "WORKFLOWS_DIR", tmp_path / "workflows")

    calls: list[tuple[str, str, str]] = []

    def on_reject(session_id: str, reason: str, target_node_id: str):
        calls.append((session_id, reason, target_node_id))
        return {"success": True, "message": "已打回"}

    node = WorkflowNode(
        id="script_validate",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "validator",
            "enable_reject_upstream": True,
            "max_reject_count": 2,
        },
    )
    ctx = NodeContext(
        definition=WorkflowDef(workflow_id=workflow_id),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id=workflow_id,
        task_id="task-test",
        on_reject_upstream=on_reject,
    )

    result = asyncio.run(ScriptNode().execute(ctx))

    assert result.status == "failed"
    assert "已打回" in result.error
    assert calls == [("script:script_validate", "plot_point 不能为空", "agent_l1")]


def test_script_node_reject_protocol_requires_opt_in(tmp_path, monkeypatch):
    workflow_id = "wf-test-script-reject-disabled"
    script_dir = tmp_path / "workflows" / workflow_id / "script"
    script_dir.mkdir(parents=True)
    (script_dir / "validator.py").write_text(
        "print('<WF_REJECT_UPSTREAM>字段缺失</WF_REJECT_UPSTREAM>')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "WORKFLOWS_DIR", tmp_path / "workflows")

    node = WorkflowNode(
        id="script_validate",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "validator",
            "enable_reject_upstream": False,
        },
    )
    ctx = NodeContext(
        definition=WorkflowDef(workflow_id=workflow_id),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id=workflow_id,
        task_id="task-test",
        on_reject_upstream=lambda *_args: {"success": True},
    )

    result = asyncio.run(ScriptNode().execute(ctx))

    assert result.status == "failed"
    assert "未开启 enable_reject_upstream" in result.error


def test_inline_script_receives_workflow_owner_environment(tmp_path, monkeypatch):
    workflow_id = "wf-plugin-environment"
    script_dir = tmp_path / "workflows" / workflow_id / "script"
    script_dir.mkdir(parents=True)
    (script_dir / "capture.py").write_text(
        "import os\n"
        "print('<script_out>' + os.environ['PLUGIN_DB_HOST'] + '</script_out>')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "WORKFLOWS_DIR", tmp_path / "workflows")
    node = WorkflowNode(
        id="capture",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "capture",
        },
    )

    result = asyncio.run(ScriptNode().execute(NodeContext(
        definition=WorkflowDef(workflow_id=workflow_id),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        workflow_id=workflow_id,
        task_id="task-plugin-environment",
        owner_environment={"PLUGIN_DB_HOST": "database.internal"},
    )))

    assert result.status == "success"
    assert result.summary == "database.internal"


def test_script_node_argv_preserves_dynamic_values_exactly(tmp_path, monkeypatch):
    workflow_id = "wf-test-script-argv"
    script_dir = tmp_path / "workflows" / workflow_id / "script"
    script_dir.mkdir(parents=True)
    (script_dir / "capture.py").write_text(
        "import json\n"
        "import sys\n"
        "print('<script_out>' + json.dumps(sys.argv[1:], ensure_ascii=False) + '</script_out>')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    monkeypatch.setattr(config, "WORKFLOWS_DIR", tmp_path / "workflows")
    values = {
        "title": "Hero's Return",
        "author": "Jane Q. Author",
        "metadata": '{"quote":"\\"Hello\\"","owner":"O\'Brien"}',
        "path": "inputs/chapter one's draft.md",
    }
    node = WorkflowNode(
        id="script_capture",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "capture",
            "script_argv": [
                "--title", "{{title}}",
                "--author", "{{author}}",
                "--metadata", "{{metadata}}",
                "--path", "{{path}}",
            ],
        },
    )
    definition = WorkflowDef(workflow_id=workflow_id, nodes=[node])
    ctx = NodeContext(
        definition=definition,
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=workspace,
        parameter_values=values,
        workflow_id=workflow_id,
        task_id="task-test",
    )

    result = asyncio.run(ScriptNode().execute(ctx))

    assert result.status == "success"
    assert json.loads(result.summary) == [
        "--title", values["title"],
        "--author", values["author"],
        "--metadata", values["metadata"],
        "--path", values["path"],
    ]


def test_script_node_rejects_non_string_argv(tmp_path):
    node = WorkflowNode(
        id="script_invalid_argv",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "capture",
            "script_argv": ["--count", 3],
        },
    )
    ctx = NodeContext(
        definition=WorkflowDef(workflow_id="wf-invalid-argv", nodes=[node]),
        node_def=node,
        node_state=NodeExecutionState(node_id=node.id),
        shared_ws=tmp_path,
        workflow_id="wf-invalid-argv",
        task_id="task-test",
    )

    result = asyncio.run(ScriptNode().execute(ctx))

    assert result.status == "failed"
    assert result.error == "script_argv 必须是字符串数组"


def test_variable_references_include_nested_script_argv():
    node = WorkflowNode(
        id="script_capture",
        node_type="script",
        node_params={
            "script_argv": ["--title", "{{title}}", {"nested": "{{metadata}}"}],
        },
    )
    definition = WorkflowDef(workflow_id="wf-variable-references", nodes=[node])

    assert definition.get_variable_references() == {
        "metadata": ["script_capture"],
        "title": ["script_capture"],
    }


class _RetrySessionManager:
    def __init__(self):
        self.sessions: dict[str, SimpleNamespace] = {}
        self.task_descriptions: list[str] = []

    async def create_sub_session(self, task_description: str, **kwargs):
        self.task_descriptions.append(task_description)
        attempt = len(self.task_descriptions)
        session_id = f"session-{attempt}"
        output = "invalid" if attempt == 1 else "valid"
        token_usage = {
            "test-model": {
                "prompt_tokens": attempt * 10,
                "completion_tokens": attempt * 5,
                "total_tokens": attempt * 15,
                "call_count": 1,
            }
        }
        token_usage_calls = [{
            "call_id": f"{session_id}:1",
            "timestamp": "2026-07-18T04:00:00+00:00",
            "provider": "test",
            "model": "model",
            "model_id": "test-model",
            "prompt_tokens": attempt * 10,
            "completion_tokens": attempt * 5,
            "total_tokens": attempt * 15,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "call_count": 1,
            "call_index": 1,
            "session_id": session_id,
        }]
        self.sessions[session_id] = SimpleNamespace(
            record=[{"type": "assistant", "content": output}],
            get_cumulative_token_usage=lambda usage=token_usage: usage,
            get_token_usage_calls=lambda calls=token_usage_calls: calls,
        )

        loop = asyncio.get_running_loop()
        on_node_complete = kwargs["on_node_complete"]
        on_auto_complete = kwargs["on_auto_complete"]
        loop.call_soon(
            on_node_complete, session_id, f"attempt {attempt}", "success", "",
        )
        loop.call_soon(
            on_auto_complete, session_id, f"attempt {attempt}", "success", "",
        )
        return {"success": True, "session_id": session_id}


class _DoubleRejectEngine(WorkflowEngine):
    def __init__(self):
        super().__init__(SimpleNamespace(sessions={}))
        self.agent_attempts = 0
        self.validator_attempts = 0
        self.reject_results: list[dict] = []

    async def _save_task_state(self, _workflow_id, _task):
        return None

    def _push_wf_task_update(self, _workflow_id, _task):
        return None

    async def _execute_node(
        self, _definition, node_def, node_state, _shared_ws, **kwargs,
    ):
        if node_def.id == "agent_l1":
            self.agent_attempts += 1
            node_state.status = "success"
            node_state.session_id = f"session-{self.agent_attempts}"
            return node_state

        self.validator_attempts += 1
        if self.validator_attempts % 2 == 1:
            on_reject = kwargs["on_reject_upstream"]
            self.reject_results.append(on_reject(
                "validator-session", "[anchor_invalid] 第一次打回", "agent_l1",
            ))
            self.reject_results.append(on_reject(
                "validator-session", "[schema_invalid] 重复打回", "agent_l1",
            ))
            node_state.status = "failed"
            node_state.error = "validator requested retry"
        else:
            node_state.status = "success"
            node_state.error = ""
        return node_state


def test_one_downstream_execution_accepts_only_first_reject_upstream():
    workflow_id = "wf-reject-once"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[
            WorkflowNode(id="agent_l1", node_type="agent"),
            WorkflowNode(
                id="script_validate",
                node_type="script",
                node_params={"max_reject_count": 3},
            ),
        ],
        edges=[WorkflowEdge(source="agent_l1", target="script_validate")],
    )
    definition._rebuild_caches()
    task = WorkflowTask(workflow_id=workflow_id, status="running")
    engine = _DoubleRejectEngine()

    result = asyncio.run(engine._execute_node_sequence(
        definition=definition,
        task=task,
        node_ids=["agent_l1", "script_validate"],
        disabled_ids=set(),
        shared_ws=None,
        parent_id="workflow-main",
        on_node_started=lambda _state: None,
        needs_approval=False,
        run_record=WorkflowRunRecord(workflow_id=workflow_id),
    ))

    assert result == "completed"
    assert engine.reject_results[0]["success"] is True
    assert engine.reject_results[1] == {
        "success": False,
        "conflict": True,
        "error_code": "reject_upstream_conflict",
        "message": (
            "reject_upstream conflict: 当前下游节点的本次执行"
            "已接受对上游节点 agent_l1 的打回请求"
        ),
    }
    upstream_state = task.node_states["agent_l1"]
    assert engine.agent_attempts == 2
    assert upstream_state.status == "success"
    assert upstream_state.reject_upstream_count == 1
    assert len(upstream_state.rejection_history) == 1
    assert upstream_state.rejection_history[0]["error_codes"] == ["anchor_invalid"]
    assert upstream_state.rejection_history[0]["resolution"] == "passed"
    assert all(
        state.status != "waiting_retry" for state in task.node_states.values()
    )
    assert all(
        event["resolution"] != "retrying"
        for state in task.node_states.values()
        for event in state.rejection_history
    )


class _RejectThenTransientFailureEngine(WorkflowEngine):
    def __init__(self):
        super().__init__(SimpleNamespace(sessions={}))
        self.agent_attempts = 0
        self.validator_attempts = 0
        self.after_attempts = 0
        self.agent_messages: list[str] = []

    async def _save_task_state(self, _workflow_id, _task):
        return None

    def _push_wf_task_update(self, _workflow_id, _task):
        return None

    async def _execute_node(
        self, _definition, node_def, node_state, _shared_ws, **kwargs,
    ):
        node_state.attempt_count += 1
        if node_def.id == "agent_l1":
            self.agent_attempts += 1
            self.agent_messages.append(node_def.first_message)
            if self.agent_attempts == 1:
                node_state.input_snapshot = {"frozen": "original"}
            node_state.attempt_history.append(
                {
                    "attempt_number": node_state.attempt_count,
                    "status": (
                        "failed" if self.agent_attempts == 2 else "completed"
                    ),
                }
            )
            if self.agent_attempts == 2:
                node_state.status = "failed"
                node_state.error = "provider 502"
            else:
                node_state.status = "completed"
                node_state.error = ""
            return node_state

        if node_def.id == "after":
            self.after_attempts += 1
            node_state.status = "completed"
            node_state.error = ""
            return node_state

        self.validator_attempts += 1
        if self.validator_attempts == 1:
            kwargs["on_reject_upstream"](
                "validator-session",
                "[schema_invalid] 缺少字段",
                "agent_l1",
            )
            node_state.status = "failed"
            node_state.error = "validator requested retry"
        else:
            node_state.status = "completed"
            node_state.error = ""
        return node_state


class _SimulatedProcessCrash(BaseException):
    pass


class _RejectRetryCheckpointCrashEngine(_RejectThenTransientFailureEngine):
    def __init__(self):
        super().__init__()
        self.saved_states: list[dict] = []

    async def _save_task_state(self, _workflow_id, task):
        self.saved_states.append(deepcopy(task.node_states))

    async def _execute_node(
        self, definition, node_def, node_state, shared_ws, **kwargs,
    ):
        if node_def.id == "agent_l1" and self.agent_attempts == 1:
            self.agent_attempts += 1
            node_state.attempt_count += 1
            node_state.status = "running"
            node_state.session_id = "retry-session-started"
            await kwargs["on_node_checkpoint"](node_state)
            raise _SimulatedProcessCrash()
        return await super()._execute_node(
            definition,
            node_def,
            node_state,
            shared_ws,
            **kwargs,
        )


def test_rejected_upstream_retry_checkpoints_before_process_crash():
    workflow_id = "wf-reject-checkpoint-crash"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[
            WorkflowNode(
                id="agent_l1",
                node_type="agent",
                first_message="生成 L1 结果",
            ),
            WorkflowNode(
                id="script_validate",
                node_type="script",
                node_params={"max_reject_count": 2},
            ),
        ],
        edges=[WorkflowEdge(source="agent_l1", target="script_validate")],
    )
    definition._rebuild_caches()
    task = WorkflowTask(workflow_id=workflow_id, status="running")
    engine = _RejectRetryCheckpointCrashEngine()

    with pytest.raises(_SimulatedProcessCrash):
        asyncio.run(
            engine._execute_node_sequence(
                definition=definition,
                task=task,
                node_ids=["agent_l1", "script_validate"],
                disabled_ids=set(),
                shared_ws=None,
                parent_id="workflow-main",
                on_node_started=lambda _state: None,
                needs_approval=False,
                run_record=WorkflowRunRecord(workflow_id=workflow_id),
            )
        )

    persisted_retry = next(
        states["agent_l1"]
        for states in reversed(engine.saved_states)
        if states.get("agent_l1", {}).status == "running"
    )
    assert persisted_retry.session_id == "retry-session-started"
    assert persisted_retry.attempt_count == 2


def test_rejected_upstream_provider_failure_enters_normal_retry_engine():
    workflow_id = "wf-reject-provider-retry"
    agent = WorkflowNode(
        id="agent_l1",
        node_type="agent",
        first_message="生成 L1 结果",
        auto_retry_count=1,
        auto_retry_interval_seconds=30,
    )
    validator = WorkflowNode(
        id="script_validate",
        node_type="script",
        node_params={"max_reject_count": 2},
    )
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[agent, validator],
        edges=[WorkflowEdge(source="agent_l1", target="script_validate")],
    )
    definition._rebuild_caches()
    task = WorkflowTask(workflow_id=workflow_id, status="running")
    engine = _RejectThenTransientFailureEngine()

    first_result = asyncio.run(
        engine._execute_node_sequence(
            definition=definition,
            task=task,
            node_ids=["agent_l1", "script_validate"],
            disabled_ids=set(),
            shared_ws=None,
            parent_id="workflow-main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=workflow_id),
        )
    )

    assert first_result == "retry_waiting"
    waiting = task.node_states["agent_l1"]
    assert waiting.status == "retry_waiting"
    assert waiting.automatic_retry_count == 1
    assert waiting.attempt_count == 2
    assert len(waiting.attempt_history) == 2
    assert waiting.input_snapshot == {"frozen": "original"}
    assert task.node_states["script_validate"].status == "pending"
    assert "下游校验反馈" in engine.agent_messages[1]
    assert waiting.rejection_history[-1]["resolution"] == "retrying"

    task.node_states["agent_l1"] = activate_scheduled_retry(waiting)
    resumed_result = asyncio.run(
        engine._execute_node_sequence(
            definition=definition,
            task=task,
            node_ids=["agent_l1", "script_validate"],
            disabled_ids=set(),
            shared_ws=None,
            parent_id="workflow-main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=workflow_id),
        )
    )

    assert resumed_result == "completed"
    completed = task.node_states["agent_l1"]
    assert completed.attempt_count == 3
    assert len(completed.attempt_history) == 3
    assert "下游校验反馈" in engine.agent_messages[2]
    assert completed.rejection_history[-1]["resolution"] == "passed"


def test_rejected_upstream_auto_skip_skips_bound_validator_and_continues():
    workflow_id = "wf-reject-provider-skip"
    agent = WorkflowNode(
        id="agent_l1",
        node_type="agent",
        first_message="生成 L1 结果",
        fail_auto_skip=True,
    )
    validator = WorkflowNode(
        id="script_validate",
        node_type="script",
        node_params={"max_reject_count": 2},
    )
    after = WorkflowNode(id="after", node_type="script")
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[agent, validator, after],
        edges=[
            WorkflowEdge(source="agent_l1", target="script_validate"),
            WorkflowEdge(source="script_validate", target="after"),
        ],
    )
    definition._rebuild_caches()
    task = WorkflowTask(workflow_id=workflow_id, status="running")
    engine = _RejectThenTransientFailureEngine()

    result = asyncio.run(
        engine._execute_node_sequence(
            definition=definition,
            task=task,
            node_ids=["agent_l1", "script_validate", "after"],
            disabled_ids=set(),
            shared_ws=None,
            parent_id="workflow-main",
            on_node_started=lambda _state: None,
            needs_approval=False,
            run_record=WorkflowRunRecord(workflow_id=workflow_id),
        )
    )

    assert result == "completed"
    assert task.node_states["agent_l1"].status == "skipped"
    assert task.node_states["script_validate"].status == "skipped"
    assert task.node_states["after"].status == "completed"
    assert engine.after_attempts == 1


def test_three_loop_items_keep_unique_rejection_ids_and_complete_audit():
    workflow_id = "wf-reject-three-loop-items"
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[
            WorkflowNode(id="agent_l1", node_type="agent"),
            WorkflowNode(
                id="script_validate",
                node_type="script",
                node_params={"max_reject_count": 2},
            ),
        ],
        edges=[WorkflowEdge(source="agent_l1", target="script_validate")],
    )
    definition._rebuild_caches()
    task = WorkflowTask(workflow_id=workflow_id, status="running")
    engine = _DoubleRejectEngine()

    async def run_three_items():
        for iteration in range(1, 4):
            if iteration > 1:
                for node_id in ("agent_l1", "script_validate"):
                    state = task.node_states[node_id]
                    engine._snapshot_node_for_iteration(
                        state, iteration - 1, task.parameter_values
                    )
                    state.status = "pending"
                    state.session_id = ""
                    state.error = ""
                    engine._reset_rejection_state_for_iteration(state)
            result = await engine._execute_node_sequence(
                definition=definition,
                task=task,
                node_ids=["agent_l1", "script_validate"],
                disabled_ids=set(),
                shared_ws=None,
                parent_id="workflow-main",
                on_node_started=lambda _state: None,
                needs_approval=False,
                run_record=WorkflowRunRecord(workflow_id=workflow_id),
            )
            assert result == "completed"

    asyncio.run(run_three_items())

    aggregate = aggregate_token_usage(
        task.node_states,
        {"agent_l1": "test.agent", "script_validate": "test.validator"},
        pricing_config={
            "version": "test",
            "currency": "USD",
            "timezone": "UTC",
            "unit_tokens": 1_000_000,
            "rules": [],
        },
    )
    assert [event["rejection_id"] for event in aggregate["rejections"]] == [
        f"{task.task_id}:script_validate:agent_l1:1",
        f"{task.task_id}:script_validate:agent_l1:2",
        f"{task.task_id}:script_validate:agent_l1:3",
    ]


def test_reject_upstream_retries_agent_with_fresh_session(tmp_path, monkeypatch):
    workflow_id = "wf-reject-fresh-session"
    workflows_dir = tmp_path / "workflows"
    script_dir = workflows_dir / workflow_id / "script"
    script_dir.mkdir(parents=True)
    (script_dir / "validate.py").write_text(
        "from pathlib import Path\n"
        "value = Path('l1.txt').read_text(encoding='utf-8')\n"
        "if value != 'valid':\n"
        "    print('<WF_REJECT_UPSTREAM target=\"agent_l1\">输出必须为 valid</WF_REJECT_UPSTREAM>')\n"
        "else:\n"
        "    print('ok')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "WORKFLOWS_DIR", workflows_dir)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_node = WorkflowNode(
        id="agent_l1",
        node_type="agent",
        first_message="生成 L1 结果",
        output_variable="l1_output",
        save_output_to_file=True,
        output_file_path="l1.txt",
    )
    validator_node = WorkflowNode(
        id="script_validate",
        node_type="script",
        node_params={
            "script_type": "python",
            "script_name": "validate",
            "enable_reject_upstream": True,
            "max_reject_count": 2,
        },
    )
    definition = WorkflowDef(
        workflow_id=workflow_id,
        nodes=[agent_node, validator_node],
        edges=[WorkflowEdge(source="agent_l1", target="script_validate")],
    )
    definition._rebuild_caches()
    task = WorkflowTask(
        workflow_id=workflow_id,
        status="running",
        snapshot_definition=definition.to_dict(),
        node_states={
            "agent_l1": NodeExecutionState(
                node_id="agent_l1",
                status="pending",
                iteration_history=[{
                    "iteration": 1,
                    "rejection_history": [{
                        "rejection_id": "prior-loop-rejection",
                        "reason": "上一轮校验失败",
                    }],
                }],
            )
        },
    )
    session_manager = _RetrySessionManager()
    engine = WorkflowEngine(session_manager)

    result = asyncio.run(engine._execute_node_sequence(
        definition=definition,
        task=task,
        node_ids=["agent_l1", "script_validate"],
        disabled_ids=set(),
        shared_ws=workspace,
        parent_id="workflow-main",
        on_node_started=lambda _state: None,
        needs_approval=False,
        run_record=WorkflowRunRecord(workflow_id=workflow_id),
    ))

    assert result == "completed"
    assert list(session_manager.sessions) == ["session-1", "session-2"]
    assert "下游校验反馈" not in session_manager.task_descriptions[0]
    assert "输出必须为 valid" in session_manager.task_descriptions[1]
    assert task.node_states["agent_l1"].session_id == "session-2"
    assert task.node_states["agent_l1"].reject_upstream_count == 1
    assert task.node_states["agent_l1"].outputs["l1_output"] == "valid"
    assert task.node_states["agent_l1"].token_usage == {
        "test-model": {
            "prompt_tokens": 30,
            "completion_tokens": 15,
            "total_tokens": 45,
            "call_count": 2,
        }
    }
    assert [
        call["call_id"] for call in task.node_states["agent_l1"].token_usage_calls
    ] == ["session-1:1", "session-2:1"]
    assert task.node_states["agent_l1"].rejection_history == [
        {
            "rejection_id": (
                f"{task.task_id}:script_validate:agent_l1:2"
            ),
            "occurred_at": task.node_states["agent_l1"].rejection_history[0][
                "occurred_at"
            ],
            "validator_node_id": "script_validate",
            "target_node_id": "agent_l1",
            "retry_index": 1,
            "max_retries": 2,
            "error_codes": ["unclassified"],
            "reason": "输出必须为 valid",
            "resolution": "passed",
            "resolved_at": task.node_states["agent_l1"].rejection_history[0][
                "resolved_at"
            ],
            "retry_session_id": "session-2",
            "retry_call_ids": ["session-2:1"],
        }
    ]
    assert task.node_states["script_validate"].status == "completed"
    assert (workspace / "l1.txt").read_text(encoding="utf-8") == "valid"
