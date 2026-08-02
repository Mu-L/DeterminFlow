from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.session import AgentSession
from src.workflow.definition import WorkflowDef, WorkflowNode, WorkflowTask
from src.workflow.manager import WorkflowManager
from src.workflow.nodes.base import NodeContext
from src.workflow.nodes.subprocess import SubprocessNode
from src.workflow.pricing import load_pricing_config, price_usage_calls
from src.workflow.runtime_models import NodeExecutionState
from src.workflow.token_usage import aggregate_token_usage
from src.web.workflow_routes import get_task_token_usage


def _pricing_config() -> dict:
    return {
        "version": "test-2026-07-18",
        "currency": "USD",
        "timezone": "UTC",
        "unit_tokens": 1_000_000,
        "rules": [
            {
                "id": "dspro-v1",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "effective_from": "2026-07-01T00:00:00+08:00",
                "effective_to": "2026-08-01T00:00:00+08:00",
                "rates": {
                    "prompt": "0.435",
                    "cached_prompt": "0.003625",
                    "completion": "0.87",
                },
                "time_bands": [],
            }
        ],
    }


def _tencent_pricing_config() -> dict:
    return {
        "version": "tencent-test-2026-07-18",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "unit_tokens": 1_000_000,
        "rules": [{
            "id": "tencent-dspro-v1",
            "provider": "tencent-cloudbase",
            "model": "deepseek-v4-pro",
            "effective_from": "2026-07-01T00:00:00+08:00",
            "effective_to": "2026-08-01T00:00:00+08:00",
            "rates": {
                "prompt": "3",
                "cached_prompt": "0.025",
                "completion": "6",
            },
            "time_bands": [{
                "id": "peak",
                "start": "08:00:00",
                "end": "18:00:00",
                "multiplier": "2",
            }],
        }],
    }


def _call(**overrides) -> dict:
    call = {
        "call_id": "session-1:1",
        "timestamp": "2026-07-18T04:00:00+00:00",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "model_id": "deepseek:deepseek-v4-pro",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 500_000,
        "total_tokens": 1_500_000,
        "cached_tokens": 200_000,
        "reasoning_tokens": 100_000,
        "usage_status": "complete",
        "missing_usage_fields": [],
        "call_count": 1,
        "call_index": 1,
        "session_id": "session-1",
    }
    call.update(overrides)
    return call


def _usage_from_test_call(call: dict) -> dict:
    return {
        key: call[key]
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "call_count",
        )
    }


def test_session_persists_call_level_usage_ledger():
    session = AgentSession(
        session_id="session-usage",
        agent_type="demo.writer",
        workflow_id="wf-demo",
        task_id="task-demo",
    )
    session.node_id = "writer"
    session.model_id = "deepseek:deepseek-v4-pro"
    output = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
                "completion_tokens_details": {"reasoning_tokens": 10},
            }
        },
        usage_metadata=None,
    )

    asyncio.run(session._extract_and_broadcast_token_usage(
        output, None, run_id="run-usage-1",
    ))
    # 同一完成事件被重放时，稳定 call_id 不应重复累计。
    asyncio.run(session._extract_and_broadcast_token_usage(
        output, None, run_id="run-usage-1",
    ))
    persisted = session.to_dict()
    restored = AgentSession.from_dict(persisted)

    assert restored.get_cumulative_token_usage() == {
        "deepseek:deepseek-v4-pro": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cached_tokens": 20,
            "reasoning_tokens": 10,
            "call_count": 1,
        }
    }
    assert restored.get_token_usage_calls() == [{
        "call_id": "session-usage:1",
        "timestamp": persisted["token_usage_calls"][0]["timestamp"],
        "completed_at": persisted["token_usage_calls"][0]["completed_at"],
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "model_id": "deepseek:deepseek-v4-pro",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "cached_tokens": 20,
        "reasoning_tokens": 10,
        "usage_status": "complete",
        "missing_usage_fields": [],
        "usage_errors": [],
        "usage_sources": {
            "prompt_tokens": "response_metadata.token_usage",
            "completion_tokens": "response_metadata.token_usage",
            "total_tokens": "response_metadata.token_usage",
            "cached_tokens": "response_metadata.token_usage",
            "reasoning_tokens": "response_metadata.token_usage",
        },
        "call_count": 1,
        "call_index": 1,
        "session_id": "session-usage",
        "workflow_id": "wf-demo",
        "task_id": "task-demo",
        "node_id": "writer",
        "agent_type": "demo.writer",
        "run_id": "run-usage-1",
    }]


def test_session_merges_usage_sources_field_by_field():
    session = AgentSession(
        session_id="session-merge",
        agent_type="demo.writer",
        workflow_id="wf-demo",
        task_id="task-demo",
    )
    session.model_id = "deepseek:deepseek-v4-pro"
    output = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 120,
                "prompt_cache_hit_tokens": 20,
            }
        },
        usage_metadata={
            "input_tokens": 999,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_token_details": {"cache_read": 999},
            "output_token_details": {"reasoning": 10},
        },
    )

    asyncio.run(session._extract_and_broadcast_token_usage(
        output, None, run_id="run-merge-1",
    ))

    call = session.get_token_usage_calls()[0]
    assert call["prompt_tokens"] == 120
    assert call["completion_tokens"] == 30
    assert call["total_tokens"] == 150
    assert call["cached_tokens"] == 20
    assert call["reasoning_tokens"] == 10
    assert call["usage_status"] == "complete"
    assert call["usage_sources"] == {
        "prompt_tokens": "response_metadata.token_usage",
        "completion_tokens": "usage_metadata",
        "total_tokens": "usage_metadata",
        "cached_tokens": "response_metadata.token_usage",
        "reasoning_tokens": "usage_metadata",
    }


def test_session_marks_missing_prompt_or_completion_usage_incomplete():
    session = AgentSession(
        session_id="session-incomplete",
        agent_type="demo.writer",
    )
    session.model_id = "deepseek:deepseek-v4-pro"
    output = SimpleNamespace(
        response_metadata={"token_usage": {"total_tokens": 30}},
        usage_metadata=None,
    )

    asyncio.run(session._extract_and_broadcast_token_usage(
        output, None, run_id="run-incomplete-1",
    ))
    call = session.get_token_usage_calls()[0]
    priced = price_usage_calls([call], pricing_config=_pricing_config())

    assert call["usage_status"] == "incomplete"
    assert call["missing_usage_fields"] == [
        "prompt_tokens",
        "completion_tokens",
    ]
    assert priced["calls"][0]["cost_status"] == "unpriced"
    assert priced["calls"][0]["cost"]["reason"] == "incomplete_usage"


def test_session_and_pricing_reject_inconsistent_usage_total():
    session = AgentSession(
        session_id="session-mismatch",
        agent_type="demo.writer",
    )
    session.model_id = "deepseek:deepseek-v4-pro"
    output = SimpleNamespace(
        response_metadata={"token_usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 999,
        }},
        usage_metadata=None,
    )

    asyncio.run(session._extract_and_broadcast_token_usage(
        output, None, run_id="run-mismatch-1",
    ))
    call = session.get_token_usage_calls()[0]
    result = price_usage_calls([call], pricing_config=_pricing_config())

    assert call["usage_status"] == "incomplete"
    assert call["usage_errors"] == ["total_tokens_mismatch"]
    assert result["calls"][0]["cost_status"] == "unpriced"
    assert result["calls"][0]["cost"]["reason"] == "incomplete_usage"


def test_aggregate_prices_deepseek_direct_and_keeps_exact_decimal_cost():
    usage = {
        "deepseek:deepseek-v4-pro": {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
            "total_tokens": 1_500_000,
            "cached_tokens": 200_000,
            "reasoning_tokens": 100_000,
            "call_count": 1,
        }
    }
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "session_id": "session-1",
                "token_usage": usage,
                "token_usage_calls": [_call()],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["currency"] == "USD"
    assert result["cost_status"] == "priced"
    assert result["cost"]["amount"] == "0.783725"
    assert result["total"]["cost"]["amount"] == "0.783725"
    assert result["by_model"]["deepseek:deepseek-v4-pro"]["cost"]["amount"] == "0.783725"
    assert result["by_agent_type"]["demo.writer"]["cost"]["amount"] == "0.783725"
    assert result["nodes"][0]["cost"]["amount"] == "0.783725"
    assert result["calls"][0]["pricing_snapshot"]["time_band"] is None
    assert result["calls"][0]["pricing_snapshot"]["multiplier"] == "1"
    assert result["pricing_snapshot"]["version"] == "test-2026-07-18"


def test_repository_deepseek_direct_pricing_has_no_peak_windows():
    calls = [
        _call(call_id="morning:1", timestamp="2026-07-18T01:30:00+00:00"),
        _call(call_id="offpeak:1", timestamp="2026-07-18T05:30:00+00:00"),
        _call(call_id="afternoon:1", timestamp="2026-07-18T06:30:00+00:00"),
    ]

    result = price_usage_calls(calls)

    assert result["pricing_snapshot"]["currency"] == "USD"
    assert [call["cost"]["amount"] for call in result["calls"]] == [
        "0.783725",
        "0.783725",
        "0.783725",
    ]
    assert [
        (call.get("pricing_snapshot") or {}).get("time_band", {}).get("id")
        if (call.get("pricing_snapshot") or {}).get("time_band")
        else None
        for call in result["calls"]
    ] == [None, None, None]


def test_tencent_peak_rule_is_isolated_from_deepseek_direct_provider():
    deepseek_call = _call()
    tencent_call = _call(
        call_id="tencent:1",
        provider="tencent-cloudbase",
        model_id="tencent-cloudbase:deepseek-v4-pro",
    )

    result = price_usage_calls(
        [deepseek_call, tencent_call],
        pricing_config=_tencent_pricing_config(),
    )

    assert result["calls"][0]["cost_status"] == "unpriced"
    assert result["calls"][0]["cost"]["reason"] == "no_matching_price_rule"
    assert result["calls"][1]["cost"]["amount"] == "10.81"
    assert result["calls"][1]["pricing_snapshot"]["time_band"]["id"] == "peak"


@pytest.mark.parametrize(
    "case",
    [
        "nan_rate",
        "infinite_rate",
        "bad_timezone",
        "bad_effective_window",
        "rates_not_object",
        "bad_rates_key",
        "time_bands_not_list",
        "bad_band_time",
        "bad_multiplier",
        "bad_unit_tokens",
    ],
)
def test_invalid_pricing_config_fails_closed_without_exception(case):
    pricing = deepcopy(_pricing_config())
    rule = pricing["rules"][0]
    if case == "nan_rate":
        rule["rates"]["prompt"] = "NaN"
    elif case == "infinite_rate":
        rule["rates"]["completion"] = "Infinity"
    elif case == "bad_timezone":
        rule["timezone"] = "Mars/Olympus_Mons"
    elif case == "bad_effective_window":
        rule["effective_to"] = "2026-06-30T00:00:00Z"
    elif case == "rates_not_object":
        rule["rates"] = []
    elif case == "bad_rates_key":
        rule["rates"][1] = "0.1"
    elif case == "time_bands_not_list":
        rule["time_bands"] = {}
    elif case == "bad_unit_tokens":
        pricing["unit_tokens"] = 1.5
    else:
        rule["time_bands"] = [{
            "id": "peak",
            "start": "99:00:00" if case == "bad_band_time" else "08:00:00",
            "end": "18:00:00",
            "multiplier": "Infinity" if case == "bad_multiplier" else "2",
        }]

    result = price_usage_calls([_call()], pricing_config=pricing)

    assert result["pricing_snapshot"]["config_status"] == "config_invalid"
    assert result["pricing_snapshot"]["validation_errors"]
    assert result["calls"][0]["cost_status"] == "unpriced"
    assert result["calls"][0]["cost"]["reason"] == "config_invalid"


def test_pricing_snapshot_does_not_expose_absolute_config_path(tmp_path):
    pricing_path = tmp_path / "private-pricing.json"
    pricing_path.write_text(json.dumps(_pricing_config()), encoding="utf-8")

    _config, snapshot = load_pricing_config(pricing_path)

    assert snapshot["config_status"] == "loaded"
    assert snapshot["source"] == "private-pricing.json"
    assert str(tmp_path) not in json.dumps(snapshot)


def test_call_without_usage_completeness_marker_is_not_priced_as_zero():
    call = _call(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    call.pop("usage_status")
    call.pop("missing_usage_fields")

    result = price_usage_calls([call], pricing_config=_pricing_config())

    assert result["calls"][0]["cost_status"] == "unpriced"
    assert result["calls"][0]["cost"]["reason"] == "incomplete_usage"


def test_nonzero_legacy_call_is_priced_only_from_consistent_breakdown():
    call = _call()
    call.pop("usage_status")
    call.pop("missing_usage_fields")

    result = price_usage_calls([call], pricing_config=_pricing_config())

    assert result["calls"][0]["cost_status"] == "priced"
    assert result["calls"][0]["cost"]["amount"] == "0.783725"
    assert result["calls"][0]["usage_pricing_status"] == (
        "legacy_inferred_complete"
    )


def test_current_config_reprices_and_replaces_stale_cost_snapshot():
    stale_call = _call(
        currency="CNY",
        cost_status="priced",
        cost={"amount": "10.81"},
        pricing_snapshot={
            "rule_id": "old-tencent-peak",
            "currency": "CNY",
            "multiplier": "2",
        },
    )

    result = price_usage_calls([stale_call])
    call = result["calls"][0]

    assert result["pricing_snapshot"]["valuation_mode"] == "current_config"
    assert call["currency"] == "USD"
    assert call["cost"]["amount"] == "0.783725"
    assert call["pricing_snapshot"]["rule_id"] == (
        "deepseek-direct-v4-pro-2026-07-18"
    )
    assert "old-tencent-peak" not in json.dumps(call)


def test_legacy_usage_without_call_ledger_is_unpriced_but_not_lost():
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "deepseek:legacy": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                },
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["calls"] == []
    assert result["total"]["total_tokens"] == 15
    assert result["cost_status"] == "unpriced"
    assert result["cost"]["unpriced_usage"]["total_tokens"] == 15


def test_rejection_history_includes_retry_usage_and_cost():
    call = _call(call_id="session-retry:1", session_id="session-retry")
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "deepseek:deepseek-v4-pro": _usage_from_test_call(call),
                },
                "token_usage_calls": [call],
                "rejection_history": [{
                    "rejection_id": "task:validator:writer:1",
                    "error_codes": ["anchor_not_literal"],
                    "reason": "anchor mismatch",
                    "retry_call_ids": ["session-retry:1"],
                    "resolution": "passed",
                }],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["rejections"][0]["error_codes"] == ["anchor_not_literal"]
    assert result["rejections"][0]["retry_usage"]["total_tokens"] == 1_500_000
    assert result["rejections"][0]["retry_cost"]["amount"] == "0.783725"


def test_loop_iteration_rejections_are_aggregated_once_after_state_reset():
    call = _call(call_id="session-loop:1", session_id="session-loop")
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "deepseek:deepseek-v4-pro": _usage_from_test_call(call),
                },
                "token_usage_calls": [call],
                "iteration_history": [
                    {
                        "iteration": 1,
                        "rejection_history": [{
                            "rejection_id": "task:validator:writer:loop-1",
                            "reason": "first iteration failed",
                            "retry_call_ids": ["session-loop:1"],
                        }],
                    },
                    {
                        "iteration": 2,
                        "rejection_history": [{
                            "rejection_id": "task:validator:writer:loop-2",
                            "reason": "second iteration failed",
                            "retry_call_ids": [],
                        }],
                    },
                ],
                # Persistence may temporarily expose the last event in both
                # places; aggregation must not double count it.
                "rejection_history": [{
                    "rejection_id": "task:validator:writer:loop-2",
                    "reason": "second iteration failed",
                    "retry_call_ids": [],
                }],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert [item["rejection_id"] for item in result["rejections"]] == [
        "task:validator:writer:loop-1",
        "task:validator:writer:loop-2",
    ]
    assert result["rejections"][0]["retry_usage"]["total_tokens"] == 1_500_000


def test_subprocess_child_usage_is_included_in_workflow_total():
    result = aggregate_token_usage(
        {
            "subflow": {
                "status": "completed",
                "child_states": {
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
                    }
                },
            }
        },
        {"subflow": "subprocess", "writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["total"]["total_tokens"] == 5
    assert result["nodes"][0]["node_id"] == "subflow/writer"
    assert result["nodes"][0]["agent_type"] == "demo.writer"


def test_subprocess_records_usage_from_real_child_agent_execution(monkeypatch):
    usage = {
        "deepseek:deepseek-v4-pro": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cached_tokens": 20,
            "reasoning_tokens": 10,
            "call_count": 1,
        }
    }
    call = _call(
        call_id="child-session:1",
        session_id="child-session",
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        cached_tokens=20,
        reasoning_tokens=10,
    )

    class CompletedChildSession:
        record = [{"type": "assistant", "content": "child output"}]

        @staticmethod
        def get_cumulative_token_usage():
            return usage

        @staticmethod
        def get_token_usage_calls():
            return [call]

    class ChildSessionManager:
        def __init__(self):
            self.sessions = {}

        async def create_sub_session(self, **kwargs):
            session_id = "child-session"
            self.sessions[session_id] = CompletedChildSession()
            kwargs["on_auto_complete"](
                session_id, "child completed", "success", "",
            )
            return {"success": True, "session_id": session_id}

    child_definition = WorkflowDef(
        workflow_id="wf-child",
        nodes=[WorkflowNode(
            id="writer",
            node_type="agent",
            agent_type="demo.writer",
            first_message="write",
            auto_flow=True,
        )],
    )
    parent_node = WorkflowNode(
        id="subflow",
        node_type="subprocess",
        sub_workflow_id="wf-child",
    )
    parent_state = NodeExecutionState(node_id="subflow")
    parent_definition = WorkflowDef(
        workflow_id="wf-parent",
        nodes=[parent_node],
    )
    monkeypatch.setattr(
        SubprocessNode,
        "_load_child_definition",
        staticmethod(lambda _workflow_id: child_definition),
    )
    checkpoints: list[str] = []

    async def checkpoint():
        child_state = parent_state.child_states.get("writer")
        checkpoints.append(child_state.session_id if child_state else "")

    result = asyncio.run(SubprocessNode().execute(NodeContext(
        definition=parent_definition,
        node_def=parent_node,
        node_state=parent_state,
        workflow_id="wf-parent",
        task_id="task-parent",
        session_manager=ChildSessionManager(),
        checkpoint=checkpoint,
    )))

    assert result.status == "success"
    child_state = parent_state.child_states["writer"]
    assert child_state.session_id == "child-session"
    assert child_state.token_usage == usage
    assert child_state.token_usage_calls == [call]
    assert "child-session" in checkpoints

    aggregate = aggregate_token_usage(
        {"subflow": parent_state},
        {"subflow": "subprocess", "writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )
    assert aggregate["total"]["total_tokens"] == 150
    assert aggregate["calls"][0]["call_id"] == "child-session:1"
    assert aggregate["nodes"][0]["node_id"] == "subflow/writer"


def test_partially_migrated_usage_marks_unledgered_calls_unpriced():
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "deepseek:deepseek-v4-pro": {
                        "prompt_tokens": 2_000_000,
                        "completion_tokens": 1_000_000,
                        "total_tokens": 3_000_000,
                        "cached_tokens": 400_000,
                        "reasoning_tokens": 200_000,
                        "call_count": 2,
                    }
                },
                "token_usage_calls": [_call()],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["cost_status"] == "partially_priced"
    assert result["cost"]["amount"] == "0.783725"
    assert result["cost"]["unpriced_call_count"] == 1
    assert result["cost"]["unpriced_usage"]["total_tokens"] == 1_500_000


def test_unmatched_model_is_explicitly_unpriced():
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "other:model": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "call_count": 1,
                    }
                },
                "token_usage_calls": [_call(
                    provider="other",
                    model="model",
                    model_id="other:model",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cached_tokens=0,
                    reasoning_tokens=0,
                )],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )

    assert result["calls"][0]["cost_status"] == "unpriced"
    assert result["calls"][0]["cost"]["reason"] == "no_matching_price_rule"
    assert result["cost_status"] == "unpriced"


def test_latest_effective_price_rule_is_selected():
    pricing = _pricing_config()
    pricing["unit_tokens"] = 1
    pricing["rules"].append({
        "id": "dspro-v2",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "effective_from": "2026-07-15T00:00:00+08:00",
        "rates": {
            "prompt": "3",
            "cached_prompt": "1",
            "completion": "9",
        },
    })
    result = aggregate_token_usage(
        {
            "writer": {
                "status": "completed",
                "token_usage": {
                    "deepseek:deepseek-v4-pro": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "call_count": 1,
                    }
                },
                "token_usage_calls": [_call(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    cached_tokens=0,
                    reasoning_tokens=0,
                )],
            }
        },
        {"writer": "demo.writer"},
        pricing_config=pricing,
    )

    assert result["cost"]["amount"] == "12"
    assert result["calls"][0]["pricing_snapshot"]["rule_id"] == "dspro-v2"


def test_from_node_retry_resets_state_but_preserves_usage_ledger():
    definition = WorkflowDef(
        workflow_id="wf-demo",
        nodes=[WorkflowNode(id="writer", node_type="agent")],
    )
    task = WorkflowTask(
        task_id="task-demo",
        workflow_id="wf-demo",
        status="pending",
        snapshot_definition=definition.to_dict(),
        node_states={
            "writer": NodeExecutionState(
                node_id="writer",
                status="failed",
                session_id="session-old",
                started_at="2026-07-18T10:00:00+08:00",
                completed_at="2026-07-18T10:01:00+08:00",
                summary="old summary",
                error="old error",
                rejection_count=2,
                rejection_reason="old rejection",
                reject_upstream_count=2,
                outputs={"draft": "old output"},
                stdout="old stdout",
                stderr="old stderr",
                token_usage={
                    "deepseek:deepseek-v4-pro": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "call_count": 1,
                    }
                },
                token_usage_calls=[_call(
                    call_id="session-old:1",
                    session_id="session-old",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cached_tokens=0,
                    reasoning_tokens=0,
                )],
                rejection_history=[{
                    "rejection_id": "task-demo:validator:writer:2",
                    "resolution": "passed",
                }],
                iteration_history=[{
                    "iteration": 1,
                    "status": "completed",
                    "rejection_history": [{
                        "rejection_id": "task-demo:validator:writer:1",
                        "resolution": "passed",
                    }],
                }],
                child_states={
                    "child": NodeExecutionState(
                        node_id="child", status="completed", session_id="child-old",
                    )
                },
                is_skipped=True,
            )
        },
        completed_at="2026-07-18T10:01:00+08:00",
        current_node_id="writer",
        run_id="run-old",
        control_flow_state={
            "conditions": {"choice": {"selected_target": "old-branch"}},
            "loops": {"loop": {"status": "completed"}},
        },
    )
    manager = WorkflowManager.__new__(WorkflowManager)
    manager._extension_manager = None
    manager._running_tasks = {}
    manager._load_task = lambda *_args: task
    manager._save_task = lambda _task: None

    async def _finish_without_execution(*_args, **_kwargs):
        return None

    manager._run_task_coroutine = _finish_without_execution
    result = asyncio.run(manager.run_task("wf-demo", "task-demo", from_node_id="writer"))

    assert result["success"] is True
    assert task.control_flow_state == {}
    state = task.node_states["writer"]
    assert state.status == "pending"
    assert state.session_id == ""
    assert state.started_at is None
    assert state.completed_at is None
    assert state.summary == ""
    assert state.error == ""
    assert state.rejection_count == 0
    assert state.rejection_reason == ""
    assert state.reject_upstream_count == 0
    assert state.outputs == {}
    assert state.stdout == ""
    assert state.stderr == ""
    assert state.iteration_history == [{
        "iteration": 1,
        "status": "completed",
        "rejection_history": [{
            "rejection_id": "task-demo:validator:writer:1",
            "resolution": "passed",
        }],
    }]
    assert state.child_states == {}
    assert state.is_skipped is False
    assert state.token_usage["deepseek:deepseek-v4-pro"]["total_tokens"] == 15
    assert state.token_usage_calls[0]["call_id"] == "session-old:1"
    assert state.rejection_history == [{
        "rejection_id": "task-demo:validator:writer:2",
        "resolution": "passed",
    }]
    aggregate = aggregate_token_usage(
        {"writer": state},
        {"writer": "demo.writer"},
        pricing_config=_pricing_config(),
    )
    assert [item["rejection_id"] for item in aggregate["rejections"]] == [
        "task-demo:validator:writer:1",
        "task-demo:validator:writer:2",
    ]
    assert task.completed_at is None
    assert task.current_node_id is None
    assert task.run_id is None


def test_workflow_token_usage_endpoint_returns_calls_cost_and_snapshot(tmp_path, monkeypatch):
    import src.config as config

    workflow_dir = tmp_path / "wf-demo"
    tasks_dir = workflow_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task-demo.json").write_text(json.dumps({
        "task_id": "task-demo",
        "name": "Demo task",
        "status": "completed",
        "snapshot_definition": {
            "nodes": [{"id": "writer", "agent_type": "demo.writer"}],
        },
        "node_states": {
            "writer": {
                "status": "completed",
                "session_id": "session-1",
                "token_usage": {
                    "deepseek:deepseek-v4-pro": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "call_count": 1,
                    }
                },
                "token_usage_calls": [_call(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cached_tokens=0,
                    reasoning_tokens=0,
                    currency="CNY",
                    cost_status="priced",
                    cost={"amount": "999"},
                    pricing_snapshot={"rule_id": "stale-cny-peak"},
                )],
            }
        },
    }), encoding="utf-8")
    monkeypatch.setattr(config, "WORKFLOWS_DIR", tmp_path)

    response = asyncio.run(get_task_token_usage("wf-demo", "task-demo", None))

    assert response["task_name"] == "Demo task"
    assert response["calls"][0]["call_id"] == "session-1:1"
    assert response["nodes"][0]["agent_type"] == "demo.writer"
    assert response["total"]["total_tokens"] == 15
    assert response["cost_status"] == "priced"
    assert response["cost"]["amount"] == "0.0000087"
    assert response["currency"] == "USD"
    assert response["pricing_snapshot"]["valuation_mode"] == "current_config"
    assert "stale-cny-peak" not in json.dumps(response)
    assert response["pricing_snapshot"]["version"].startswith(
        "deepseek-direct-v4-usd-"
    )
