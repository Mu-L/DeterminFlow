"""Fast, stable representative tests for the Executor pool load harness."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "scripts" / "workflow_executor_pool_benchmark.py"


def _load_harness():
    path = Path(__file__).with_name("workflow_executor_pool_load_harness.py")
    spec = importlib.util.spec_from_file_location(
        "workflow_executor_pool_load_harness", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_harness = _load_harness()
QUICK_CPU_BUSY_SECONDS = _harness.QUICK_CPU_BUSY_SECONDS
ArgumentError = _harness.ArgumentError
build_report = _harness.build_report
expected_assignment = _harness.expected_assignment
linux_pss_available = _harness.linux_pss_available
overlapping_task_ids = _harness.overlapping_task_ids
parse_benchmark_args = _harness.parse_benchmark_args
parse_pss_kb = _harness.parse_pss_kb
read_process_sample = _harness.read_process_sample
run_cli = _harness.run_cli
run_fault_scenario = _harness.run_fault_scenario
run_load_scenario = _harness.run_load_scenario


def _assert_member_metrics(scenario: dict) -> None:
    metrics = scenario["member_metrics"]
    assert len(metrics) == scenario["members"]
    for member in metrics:
        assert set(member) >= {
            "executor_id",
            "pid",
            "epoch",
            "assigned_tasks",
            "cpu_seconds",
            "rss_kb",
            "pss_kb",
        }
        assert member["pid"]
        assert member["assigned_tasks"] >= 0
        assert member["rss_kb"] is None or member["rss_kb"] >= 0
        assert member["cpu_seconds"] is None or member["cpu_seconds"] >= 0
        if sys.platform.startswith("linux") and linux_pss_available():
            assert member["pss_kb"] is None or isinstance(member["pss_kb"], int)
        else:
            assert member["pss_kb"] is None


def test_expected_assignment_follows_round_robin():
    assert expected_assignment(1, 20) == {"workflow-executor-0": 20}
    assert expected_assignment(2, 20) == {
        "workflow-executor-0": 10,
        "workflow-executor-1": 10,
    }
    assert expected_assignment(4, 50) == {
        "workflow-executor-0": 13,
        "workflow-executor-1": 13,
        "workflow-executor-2": 12,
        "workflow-executor-3": 12,
    }


def test_parse_pss_from_smaps_rollup():
    text = "Rss:              2048 kB\nPss:              1234 kB\nPss_Anon:         100 kB\n"
    assert parse_pss_kb(text) == 1234
    assert parse_pss_kb("Rss: 10 kB\n") is None


def test_process_sample_sets_pss_null_off_linux():
    sample = read_process_sample(os.getpid())
    assert sample["alive"] is True
    assert sample["pid"] == os.getpid()
    assert sample["rss_kb"] is None or isinstance(sample["rss_kb"], int)
    assert sample["cpu_seconds"] is None or isinstance(sample["cpu_seconds"], float)
    if sys.platform.startswith("linux"):
        assert sample["pss_kb"] is None or isinstance(sample["pss_kb"], int)
    else:
        assert sample["pss_kb"] is None


def test_overlapping_executions_ignore_sequential_restart():
    events = [
        {"event": "start", "task_id": "t1", "pid": 11, "ts": 1.0},
        {"event": "complete", "task_id": "t1", "pid": 11, "ts": 1.5},
        {"event": "start", "task_id": "t1", "pid": 12, "ts": 2.0},
        {"event": "complete", "task_id": "t1", "pid": 12, "ts": 3.0},
        {"event": "start", "task_id": "t2", "pid": 21, "ts": 1.0},
        {"event": "complete", "task_id": "t2", "pid": 21, "ts": 2.0},
    ]
    assert overlapping_task_ids(events) == []


def test_overlapping_executions_detect_completed_intervals_that_intersect():
    events = [
        {"event": "start", "task_id": "t1", "pid": 11, "ts": 1.0},
        {"event": "start", "task_id": "t1", "pid": 12, "ts": 2.0},
        {"event": "complete", "task_id": "t1", "pid": 11, "ts": 3.0},
        {"event": "complete", "task_id": "t1", "pid": 12, "ts": 4.0},
    ]
    assert overlapping_task_ids(events) == ["t1"]


def test_cli_requires_explicit_matrix_or_counts():
    with pytest.raises(ArgumentError, match="--matrix"):
        parse_benchmark_args([])
    with pytest.raises(ArgumentError, match="both --members and --tasks"):
        parse_benchmark_args(["--members", "2"])
    with pytest.raises(ArgumentError, match="between 1 and 32"):
        parse_benchmark_args(["--members", "0", "--tasks", "20"])
    args = parse_benchmark_args(["--matrix"])
    assert args.member_counts == [1, 2, 4]
    assert args.task_counts == [20, 50]
    assert args.include_fault is True
    quick = parse_benchmark_args(["--quick", "--no-fault"])
    assert quick.member_counts == [2]
    assert quick.task_counts == [4]
    assert quick.include_fault is False
    single = parse_benchmark_args(["--members", "2", "--tasks", "20"])
    assert single.include_fault is False


def test_benchmark_cli_help_and_invalid_args_json():
    help_proc = subprocess.run(
        [sys.executable, str(BENCHMARK), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_proc.returncode == 0
    assert "WorkflowExecutorPool" in help_proc.stdout

    bad = subprocess.run(
        [sys.executable, str(BENCHMARK), "--members", "0", "--tasks", "20"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode != 0
    report = json.loads(bad.stdout)
    assert report["ok"] is False
    assert report["errors"]
    assert report["scenarios"] == []
    if sys.platform.startswith("linux"):
        assert isinstance(report["pss_available"], bool)
    else:
        assert report["pss_available"] is False


@pytest.mark.skipif(os.name == "nt", reason="Load harness scripts use POSIX fcntl")
def test_load_report_two_members_four_tasks(tmp_path, monkeypatch):
    result = asyncio.run(run_load_scenario(
        tmp_path=tmp_path,
        patcher=monkeypatch,
        executor_count=2,
        task_count=4,
        cpu_busy_seconds=QUICK_CPU_BUSY_SECONDS,
    ))
    assert result["ok"] is True, result["errors"]
    assert result["name"] == "load"
    assert result["members"] == 2
    assert result["tasks"] == 4
    assert result["assignments"] == expected_assignment(2, 4)
    assert result["elapsed_seconds"] > 0
    assert result["throughput_tasks_per_second"] > 0
    assert result["double_execution"] is False
    assert result["leftover_pids"] == []
    _assert_member_metrics(result)
    report = build_report([result])
    parsed = json.loads(json.dumps(report))
    assert parsed["ok"] is True
    if not sys.platform.startswith("linux"):
        assert parsed["pss_available"] is False
        assert all(
            member["pss_kb"] is None
            for member in parsed["scenarios"][0]["member_metrics"]
        )


@pytest.mark.skipif(os.name == "nt", reason="Load harness scripts use POSIX fcntl")
def test_fault_sigkill_keeps_identity_and_rejects_double_execution(
    tmp_path, monkeypatch,
):
    result = asyncio.run(run_fault_scenario(
        tmp_path=tmp_path,
        patcher=monkeypatch,
        executor_count=2,
    ))
    assert result["ok"] is True, result["errors"]
    assert result["name"] == "fault_sigkill"
    assert result["double_execution"] is False
    assert result["overlapping_task_ids"] == []
    assert result["leftover_pids"] == []
    victim = result["victim"]
    assert victim["executor_id"] == "workflow-executor-0"
    assert victim["executor_id_unchanged"] is True
    assert victim["epoch_before"] != victim["epoch_after"]
    assert victim["pid_before"] != victim["pid_after"]
    assert len(result["siblings"]) == 1
    sibling = result["siblings"][0]
    assert sibling["executor_id"] == "workflow-executor-1"
    assert sibling["pid_unchanged"] is True
    assert sibling["epoch_unchanged"] is True
    _assert_member_metrics(result)
