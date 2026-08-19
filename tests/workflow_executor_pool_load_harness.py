"""Real WorkflowExecutorPool load and single-member SIGKILL acceptance helpers."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _load_base():
    path = Path(__file__).with_name("workflow_executor_pool_harness.py")
    spec = importlib.util.spec_from_file_location(
        "workflow_executor_pool_harness", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_assets():
    path = Path(__file__).with_name("workflow_executor_pool_load_assets.py")
    spec = importlib.util.spec_from_file_location(
        "workflow_executor_pool_load_assets", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_base = _load_base()
_assets = _load_assets()
PROCESS_WAIT_TIMEOUT = _base.PROCESS_WAIT_TIMEOUT
RESTART_WAIT_TIMEOUT = _base.RESTART_WAIT_TIMEOUT
WAIT_INTERVAL = _base.WAIT_INTERVAL
ProcessTracker = _base.ProcessTracker
SimplePatcher = _base.SimplePatcher
assert_distinct_member_pids = _base.assert_distinct_member_pids
controller_manager = _base.controller_manager
expected_executor_ids = _base.expected_executor_ids
install_script_workflow = _base.install_script_workflow
isolate_executor_runtime = _base.isolate_executor_runtime
force_kill_pid = _base.force_kill_pid
process_alive = _base.process_alive
read_int_file = _base.read_int_file
release_hold = _base.release_hold
start_real_pool = _base.start_real_pool
stop_pool_and_collect_leftovers = _base.stop_pool_and_collect_leftovers
task_binding = _base.task_binding
CPU_BUSY_SCRIPT = _assets.CPU_BUSY_SCRIPT
FAULT_HOLD_SCRIPT = _assets.FAULT_HOLD_SCRIPT
linux_pss_available = _assets.linux_pss_available
parse_pss_kb = _assets.parse_pss_kb
read_process_sample = _assets.read_process_sample

SUPPORTED_MEMBER_COUNTS = (1, 2, 4)
SUPPORTED_TASK_COUNTS = (20, 50)
DEFAULT_CPU_BUSY_SECONDS = 0.15
QUICK_MEMBER_COUNT = 2
QUICK_TASK_COUNT = 4
QUICK_CPU_BUSY_SECONDS = 0.05

class ArgumentError(ValueError):
    """CLI argument error that should become JSON and a non-zero exit."""


def identities_by_id(pool) -> dict[str, Any]:
    return {identity.executor_id: identity for identity in pool.identities}


def expected_assignment(executor_count: int, task_count: int) -> dict[str, int]:
    counts = {executor_id: 0 for executor_id in expected_executor_ids(executor_count)}
    ids = expected_executor_ids(executor_count)
    for index in range(task_count):
        counts[ids[index % executor_count]] += 1
    return counts


def load_wait_timeout(
    task_count: int,
    members: int,
    cpu_busy_seconds: float,
) -> float:
    return max(30.0, (task_count * (cpu_busy_seconds + 1.5)) / max(1, members) + 20.0)


def script_timeout_seconds(cpu_busy_seconds: float) -> str:
    return str(max(45, int(cpu_busy_seconds) + 30))


async def await_until(predicate, *, timeout: float, message: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - surface last probe error
            last_error = exc
        await asyncio.sleep(WAIT_INTERVAL)
    suffix = f": {last_error}" if last_error is not None else ""
    raise TimeoutError(f"timed out waiting for {message}{suffix}")


async def await_processes_dead(pids: list[int], *, timeout: float, message: str) -> None:
    await await_until(
        lambda: all(not process_alive(pid) for pid in pids),
        timeout=timeout,
        message=message,
    )


def sample_pool_members(pool) -> dict[str, dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {}
    for executor_id, pid in pool.member_pids.items():
        if pid is None:
            samples[executor_id] = {
                "pid": None,
                "alive": False,
                "rss_kb": None,
                "pss_kb": None,
                "cpu_seconds": None,
            }
            continue
        samples[executor_id] = read_process_sample(int(pid))
    return samples


def merge_peak_samples(
    peaks: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    for executor_id, sample in current.items():
        previous = peaks.get(executor_id)
        if previous is None:
            peaks[executor_id] = dict(sample)
            continue
        merged = dict(sample)
        prev_rss = previous.get("rss_kb")
        curr_rss = sample.get("rss_kb")
        if prev_rss is not None and (curr_rss is None or curr_rss < prev_rss):
            merged["rss_kb"] = prev_rss
        prev_pss = previous.get("pss_kb")
        curr_pss = sample.get("pss_kb")
        if prev_pss is not None and (curr_pss is None or curr_pss < prev_pss):
            merged["pss_kb"] = prev_pss
        prev_cpu = previous.get("cpu_seconds")
        curr_cpu = sample.get("cpu_seconds")
        if prev_cpu is not None and (curr_cpu is None or curr_cpu < prev_cpu):
            merged["cpu_seconds"] = prev_cpu
        peaks[executor_id] = merged
    return peaks


async def sample_until(pool, peaks: dict[str, dict[str, Any]], stop: asyncio.Event) -> None:
    merge_peak_samples(peaks, sample_pool_members(pool))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            merge_peak_samples(peaks, sample_pool_members(pool))
    merge_peak_samples(peaks, sample_pool_members(pool))


def read_execution_log(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def overlapping_task_ids(events: list[dict[str, Any]]) -> list[str]:
    by_task: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        task_id = event.get("task_id")
        if isinstance(task_id, str):
            by_task.setdefault(task_id, []).append(event)
    overlapped: list[str] = []
    for task_id, task_events in by_task.items():
        intervals: list[tuple[float, float, int]] = []
        completes = {
            (event.get("pid"), event.get("task_id")): event
            for event in task_events
            if event.get("event") == "complete"
        }
        for start in task_events:
            if start.get("event") != "start":
                continue
            try:
                started = float(start["ts"])
                pid = int(start["pid"])
            except (KeyError, TypeError, ValueError):
                continue
            complete = completes.get((pid, task_id))
            if complete is not None:
                try:
                    ended = float(complete["ts"])
                except (KeyError, TypeError, ValueError):
                    ended = started
            elif process_alive(pid):
                ended = time.time()
            else:
                ended = started
            intervals.append((started, ended, pid))
        intervals.sort()
        found = False
        for index, (start_a, end_a, pid_a) in enumerate(intervals):
            for start_b, end_b, pid_b in intervals[index + 1:]:
                if pid_a != pid_b and start_a < end_b and start_b < end_a:
                    found = True
                    break
            if found:
                break
        if found:
            overlapped.append(task_id)
    return overlapped


def _empty_scenario(
    *,
    name: str,
    members: int,
    tasks: int,
    cpu_busy_seconds: float | None,
    errors: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "ok": False,
        "errors": errors,
        "members": members,
        "tasks": tasks,
        "elapsed_seconds": None,
        "throughput_tasks_per_second": None,
        "assignments": {},
        "member_metrics": [],
        "leftover_pids": [],
        "double_execution": None,
        "overlapping_task_ids": [],
    }
    if cpu_busy_seconds is not None:
        payload["cpu_busy_seconds"] = cpu_busy_seconds
    return payload


def _member_metrics(
    pool,
    assignments: dict[str, int],
    peaks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    metrics = []
    for identity in pool.identities:
        sample = peaks.get(identity.executor_id) or read_process_sample(
            int(pool.member_pids[identity.executor_id] or 0),
        )
        metrics.append({
            "executor_id": identity.executor_id,
            "pid": pool.member_pids.get(identity.executor_id),
            "epoch": identity.epoch,
            "assigned_tasks": assignments.get(identity.executor_id, 0),
            "cpu_seconds": sample.get("cpu_seconds"),
            "rss_kb": sample.get("rss_kb"),
            "pss_kb": sample.get("pss_kb"),
        })
    return metrics


def _configure_load_env(patcher, tmp_path: Path, *, cpu_busy_seconds: float) -> Path:
    exec_log = tmp_path / "exec.log"
    exec_log.touch()
    patcher.setenv("DETERMINFLOW_EXEC_LOG", str(exec_log))
    patcher.setenv("DETERMINFLOW_CPU_BUSY_SECONDS", str(cpu_busy_seconds))
    return exec_log


async def run_load_scenario(
    *,
    tmp_path: Path,
    patcher,
    executor_count: int,
    task_count: int,
    cpu_busy_seconds: float,
) -> dict[str, Any]:
    runtime = isolate_executor_runtime(tmp_path, patcher)
    exec_log = _configure_load_env(
        patcher, tmp_path, cpu_busy_seconds=cpu_busy_seconds,
    )
    pool = None
    tracker = ProcessTracker()
    leftover: list[int] = []
    errors: list[str] = []
    assignments: dict[str, int] = {}
    peaks: dict[str, dict[str, Any]] = {}
    member_metrics: list[dict[str, Any]] = []
    elapsed = None
    overlapped: list[str] = []
    try:
        pool = await start_real_pool(runtime, executor_count=executor_count)
        member_pids = assert_distinct_member_pids(pool, expected_count=executor_count)
        tracker.add_pool(pool)
        identities = identities_by_id(pool)
        manager = controller_manager()
        manager.attach_execution_delegate(pool)
        workflow_id = f"wf-pool-load-{executor_count}-{task_count}"
        install_script_workflow(
            manager,
            runtime,
            workflow_id=workflow_id,
            script_name="busy",
            source=CPU_BUSY_SCRIPT,
            timeout=script_timeout_seconds(cpu_busy_seconds),
        )
        stop_sampling = asyncio.Event()
        sampler = asyncio.create_task(sample_until(pool, peaks, stop_sampling))
        started_at = time.perf_counter()
        try:
            task_ids = [
                manager.create_task(workflow_id)["task_id"]
                for _ in range(task_count)
            ]
            started_results = await asyncio.gather(*(
                manager.run_task(workflow_id, task_id) for task_id in task_ids
            ))
            for started in started_results:
                if started.get("success") is not True:
                    errors.append(f"run_task failed: {started}")

            timeout = load_wait_timeout(task_count, executor_count, cpu_busy_seconds)

            def all_completed() -> bool:
                for task_id in task_ids:
                    task = manager._load_task(workflow_id, task_id)
                    if task is None or task.status != "completed":
                        return False
                return True

            await await_until(
                all_completed,
                timeout=timeout,
                message="all load tasks to complete",
            )
        finally:
            elapsed = round(time.perf_counter() - started_at, 6)
            stop_sampling.set()
            await sampler

        persisted = [
            manager._load_task(workflow_id, task_id) for task_id in task_ids
        ]
        assigned_ids = [task.executor_id for task in persisted]
        assignments = {
            executor_id: assigned_ids.count(executor_id)
            for executor_id in expected_executor_ids(executor_count)
        }
        expected = expected_assignment(executor_count, task_count)
        if assignments != expected:
            errors.append(
                f"assignment mismatch: expected {expected}, got {assignments}"
            )
        for task in persisted:
            if task.status != "completed":
                errors.append(f"task {task.task_id} status={task.status}")
                continue
            identity = identities[task.executor_id]
            if task.executor_epoch != identity.epoch:
                errors.append(
                    f"task {task.task_id} epoch {task.executor_epoch} "
                    f"!= {identity.epoch}"
                )
            marker = runtime.marker_dir / task.task_id
            if not marker.is_file():
                errors.append(f"missing marker for {task.task_id}")
                continue
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
            tracker.add_pid(marker_data.get("pid"))
            if marker_data.get("ppid") != member_pids[task.executor_id]:
                errors.append(
                    f"task {task.task_id} ppid {marker_data.get('ppid')} "
                    f"!= {member_pids[task.executor_id]}"
                )
        events = read_execution_log(exec_log)
        overlapped = overlapping_task_ids(events)
        completes = [
            event for event in events if event.get("event") == "complete"
        ]
        if len(completes) != task_count:
            errors.append(
                f"expected {task_count} complete events, got {len(completes)}"
            )
        if overlapped:
            errors.append(f"overlapping executions: {overlapped}")
        if pool.member_pids != member_pids:
            errors.append("member pids changed during load")
        if identities_by_id(pool) != identities:
            errors.append("member identities changed during load")
        member_metrics = _member_metrics(pool, assignments, peaks)
    except Exception as exc:  # noqa: BLE001 - convert to report errors
        errors.append(f"{type(exc).__name__}: {exc}")
        overlapped = overlapping_task_ids(read_execution_log(exec_log))
        member_metrics = [
            {
                "executor_id": executor_id,
                "pid": sample.get("pid"),
                "epoch": None,
                "assigned_tasks": assignments.get(executor_id, 0),
                "cpu_seconds": sample.get("cpu_seconds"),
                "rss_kb": sample.get("rss_kb"),
                "pss_kb": sample.get("pss_kb"),
            }
            for executor_id, sample in sorted(peaks.items())
        ]
    else:
        overlapped = overlapping_task_ids(read_execution_log(exec_log))
    finally:
        leftover = await stop_pool_and_collect_leftovers(pool, tracker, runtime)

    if leftover:
        errors.append(f"leftover pids: {leftover}")
    throughput = None
    if elapsed and elapsed > 0:
        throughput = round(task_count / elapsed, 6)
    return {
        "name": "load",
        "ok": not errors,
        "errors": errors,
        "members": executor_count,
        "tasks": task_count,
        "cpu_busy_seconds": cpu_busy_seconds,
        "elapsed_seconds": elapsed,
        "throughput_tasks_per_second": throughput,
        "assignments": assignments,
        "member_metrics": member_metrics,
        "leftover_pids": leftover,
        "double_execution": bool(overlapped),
        "overlapping_task_ids": overlapped,
    }


async def run_fault_scenario(
    *,
    tmp_path: Path,
    patcher,
    executor_count: int = 2,
) -> dict[str, Any]:
    if executor_count < 2:
        return _empty_scenario(
            name="fault_sigkill",
            members=executor_count,
            tasks=0,
            cpu_busy_seconds=None,
            errors=["fault scenario requires at least 2 members"],
        )
    runtime = isolate_executor_runtime(tmp_path, patcher)
    exec_log = _configure_load_env(patcher, tmp_path, cpu_busy_seconds=0)
    pool = None
    tracker = ProcessTracker()
    leftover: list[int] = []
    errors: list[str] = []
    assignments: dict[str, int] = {}
    peaks: dict[str, dict[str, Any]] = {}
    victim_report: dict[str, Any] | None = None
    sibling_reports: list[dict[str, Any]] = []
    overlapped: list[str] = []
    member_metrics: list[dict[str, Any]] = []
    task_count = executor_count
    elapsed = None
    try:
        handoffs: list[tuple[Any, Any, int, Any]] = []
        manager = None

        async def on_restart(previous, current):
            reassigned = manager.reassign_dead_executor_generation(
                previous, current,
            )
            recovery = await pool.client_for(current.executor_id).call(
                "recover_owned_tasks",
            )
            handoffs.append((previous, current, reassigned, recovery))

        pool = await start_real_pool(
            runtime,
            on_restart=on_restart,
            executor_count=executor_count,
        )
        original_pids = assert_distinct_member_pids(
            pool, expected_count=executor_count,
        )
        tracker.add_pool(pool)
        original_identities = identities_by_id(pool)
        manager = controller_manager()
        manager.attach_execution_delegate(pool)
        workflow_id = f"wf-pool-fault-{executor_count}"
        install_script_workflow(
            manager,
            runtime,
            workflow_id=workflow_id,
            script_name="hold",
            source=FAULT_HOLD_SCRIPT,
            auto_retry_count=1,
            auto_retry_interval_seconds=0,
        )
        stop_sampling = asyncio.Event()
        sampler = asyncio.create_task(sample_until(pool, peaks, stop_sampling))
        started_at = time.perf_counter()
        try:
            task_ids = [
                manager.create_task(workflow_id)["task_id"]
                for _ in range(task_count)
            ]
            for task_id in task_ids:
                started = await manager.run_task(workflow_id, task_id)
                if started.get("success") is not True:
                    errors.append(f"run_task failed: {started}")
            for task_id in task_ids:
                await await_until(
                    lambda task_id=task_id: (
                        runtime.hold_dir / task_id / "ready"
                    ).is_file(),
                    timeout=max(20.0, PROCESS_WAIT_TIMEOUT + 10.0),
                    message=f"hold script {task_id} to become ready",
                )

            assigned_tasks = {
                task.executor_id: task
                for task in (
                    manager._load_task(workflow_id, task_id)
                    for task_id in task_ids
                )
            }
            expected_ids = set(expected_executor_ids(executor_count))
            if set(assigned_tasks) != expected_ids:
                errors.append(
                    f"expected one task per member, got {sorted(assigned_tasks)}"
                )
            assignments = {
                executor_id: 1 if executor_id in assigned_tasks else 0
                for executor_id in expected_executor_ids(executor_count)
            }
            victim_id = "workflow-executor-0"
            victim_task = assigned_tasks[victim_id]
            sibling_snapshots = {
                executor_id: task_binding(task)
                for executor_id, task in assigned_tasks.items()
                if executor_id != victim_id
            }

            def hold_pids(task_id: str) -> tuple[int, int]:
                script_pid = read_int_file(
                    runtime.hold_dir / task_id / "script.pid",
                )
                child_pid = read_int_file(
                    runtime.hold_dir / task_id / "child.pid",
                )
                if script_pid is None or child_pid is None:
                    raise AssertionError(f"missing hold pids for {task_id}")
                if not process_alive(script_pid) or not process_alive(child_pid):
                    raise AssertionError(f"hold processes not alive for {task_id}")
                return script_pid, child_pid

            victim_script_pid, victim_child_pid = hold_pids(victim_task.task_id)
            sibling_hold_pids = {
                executor_id: hold_pids(task.task_id)
                for executor_id, task in assigned_tasks.items()
                if executor_id != victim_id
            }
            tracker.add_pid(victim_script_pid)
            tracker.add_pid(victim_child_pid)
            for script_pid, child_pid in sibling_hold_pids.values():
                tracker.add_pid(script_pid)
                tracker.add_pid(child_pid)

            force_kill_pid(original_pids[victim_id])
            await await_until(
                lambda: (
                    pool.member_pids.get(victim_id)
                    not in {None, original_pids[victim_id]}
                    and identities_by_id(pool)[victim_id].epoch
                    != original_identities[victim_id].epoch
                    and handoffs
                ),
                timeout=RESTART_WAIT_TIMEOUT,
                message="victim executor to restart with a new pid and epoch",
            )
            tracker.add_pool(pool)
            await await_processes_dead(
                [victim_script_pid, victim_child_pid],
                timeout=PROCESS_WAIT_TIMEOUT,
                message="old victim script descendants to be reaped",
            )

            current_identities = identities_by_id(pool)
            if current_identities[victim_id].executor_id != victim_id:
                errors.append("victim executor_id changed")
            if current_identities[victim_id].epoch == original_identities[victim_id].epoch:
                errors.append("victim epoch did not change")
            if pool.member_pids.get(victim_id) == original_pids[victim_id]:
                errors.append("victim pid did not change")

            for executor_id, snapshot in sibling_snapshots.items():
                if pool.member_pids.get(executor_id) != original_pids[executor_id]:
                    errors.append(f"{executor_id} pid changed")
                if (
                    current_identities[executor_id].epoch
                    != original_identities[executor_id].epoch
                ):
                    errors.append(f"{executor_id} epoch changed")
                live_task = manager._load_task(
                    workflow_id, assigned_tasks[executor_id].task_id,
                )
                if task_binding(live_task) != snapshot:
                    errors.append(f"{executor_id} task binding changed")
                script_pid, child_pid = sibling_hold_pids[executor_id]
                if not process_alive(script_pid) or not process_alive(child_pid):
                    errors.append(f"{executor_id} hold processes died")

            previous, current, reassigned, recovery = handoffs[0]
            if previous.executor_id != victim_id or current.executor_id != victim_id:
                errors.append("restart handoff changed executor_id")
            if reassigned != 1:
                errors.append(f"expected 1 reassigned task, got {reassigned}")
            if recovery.get("scanned") != 1:
                errors.append(f"expected scanned=1, got {recovery}")

            await await_until(
                lambda: (
                    (new_pid := read_int_file(
                        runtime.hold_dir / victim_task.task_id / "script.pid"
                    )) is not None
                    and new_pid != victim_script_pid
                    and process_alive(new_pid)
                ),
                timeout=max(20.0, PROCESS_WAIT_TIMEOUT + 10.0),
                message="victim Task to resume inside the new Executor generation",
            )
            recovered_victim = manager._load_task(workflow_id, victim_task.task_id)
            if recovered_victim.executor_id != victim_id:
                errors.append("recovered victim executor_id changed")
            if recovered_victim.executor_epoch != current.epoch:
                errors.append("recovered victim epoch was not updated")
            if recovered_victim.executor_epoch == previous.epoch:
                errors.append("recovered victim still on previous epoch")
            if recovered_victim.status != "running":
                errors.append(
                    f"recovered victim status={recovered_victim.status}"
                )
            for executor_id, snapshot in sibling_snapshots.items():
                sibling_task = manager._load_task(
                    workflow_id, assigned_tasks[executor_id].task_id,
                )
                if task_binding(sibling_task) != snapshot:
                    errors.append(
                        f"{executor_id} task binding changed after recovery"
                    )
            for path in (runtime.hold_dir / victim_task.task_id).glob("*.pid"):
                tracker.add_pid(read_int_file(path))

            events = read_execution_log(exec_log)
            overlapped = overlapping_task_ids(events)
            if overlapped:
                errors.append(f"overlapping executions: {overlapped}")
            victim_starts = [
                event for event in events
                if event.get("task_id") == victim_task.task_id
                and event.get("event") == "start"
            ]
            if len(victim_starts) < 1:
                errors.append("victim produced no execution start")
            for executor_id, task in assigned_tasks.items():
                if executor_id == victim_id:
                    continue
                sibling_starts = [
                    event for event in events
                    if event.get("task_id") == task.task_id
                    and event.get("event") == "start"
                ]
                if len(sibling_starts) != 1:
                    errors.append(
                        f"{executor_id} start count={len(sibling_starts)}"
                    )

            release_hold(runtime, victim_task.task_id)
            await await_until(
                lambda: manager._load_task(
                    workflow_id, victim_task.task_id,
                ).status == "completed",
                timeout=max(20.0, PROCESS_WAIT_TIMEOUT + 10.0),
                message="recovered victim Task to complete",
            )
            for executor_id, task in assigned_tasks.items():
                if executor_id == victim_id:
                    continue
                stopped = await manager.stop_task(workflow_id, task.task_id)
                if stopped.get("success") is not True:
                    errors.append(f"stop_task failed for {executor_id}: {stopped}")
                stopped_binding = task_binding(
                    manager._load_task(workflow_id, task.task_id)
                )
                snapshot = sibling_snapshots[executor_id]
                if stopped_binding["executor_id"] != snapshot["executor_id"]:
                    errors.append(f"{executor_id} executor_id changed on stop")
                if stopped_binding["executor_epoch"] != snapshot["executor_epoch"]:
                    errors.append(f"{executor_id} epoch changed on stop")

            victim_report = {
                "executor_id": victim_id,
                "executor_id_unchanged": True,
                "epoch_before": original_identities[victim_id].epoch,
                "epoch_after": current.epoch,
                "pid_before": original_pids[victim_id],
                "pid_after": pool.member_pids.get(victim_id),
            }
            sibling_reports = [
                {
                    "executor_id": executor_id,
                    "pid": pool.member_pids.get(executor_id),
                    "epoch": current_identities[executor_id].epoch,
                    "task_id": assigned_tasks[executor_id].task_id,
                    "pid_unchanged": (
                        pool.member_pids.get(executor_id) == original_pids[executor_id]
                    ),
                    "epoch_unchanged": (
                        current_identities[executor_id].epoch
                        == original_identities[executor_id].epoch
                    ),
                }
                for executor_id in expected_executor_ids(executor_count)
                if executor_id != victim_id
            ]
            member_metrics = _member_metrics(pool, assignments, peaks)
        finally:
            elapsed = round(time.perf_counter() - started_at, 6)
            stop_sampling.set()
            await sampler
    except Exception as exc:  # noqa: BLE001 - convert to report errors
        errors.append(f"{type(exc).__name__}: {exc}")
        overlapped = overlapping_task_ids(read_execution_log(exec_log))
    finally:
        leftover = await stop_pool_and_collect_leftovers(pool, tracker, runtime)

    if leftover:
        errors.append(f"leftover pids: {leftover}")
    if not member_metrics and peaks:
        member_metrics = [
            {
                "executor_id": executor_id,
                "pid": sample.get("pid"),
                "epoch": None,
                "assigned_tasks": assignments.get(executor_id, 0),
                "cpu_seconds": sample.get("cpu_seconds"),
                "rss_kb": sample.get("rss_kb"),
                "pss_kb": sample.get("pss_kb"),
            }
            for executor_id, sample in sorted(peaks.items())
        ]
    return {
        "name": "fault_sigkill",
        "ok": not errors,
        "errors": errors,
        "members": executor_count,
        "tasks": task_count,
        "elapsed_seconds": elapsed,
        "throughput_tasks_per_second": None,
        "assignments": assignments,
        "member_metrics": member_metrics,
        "leftover_pids": leftover,
        "double_execution": bool(overlapped),
        "overlapping_task_ids": overlapped,
        "victim": victim_report,
        "siblings": sibling_reports,
    }


def build_report(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": all(scenario.get("ok") is True for scenario in scenarios) and bool(scenarios),
        "platform": sys.platform,
        "linux_pss_available": linux_pss_available(),
        "pss_available": linux_pss_available(),
        "scenarios": scenarios,
    }


def parse_int_list(text: str, *, label: str) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise ArgumentError(f"invalid {label}: {item!r}") from exc
    if not values:
        raise ArgumentError(f"{label} must not be empty")
    return values


def parse_benchmark_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run real WorkflowExecutorPool load and SIGKILL acceptance cases. "
            "Prints one JSON object to stdout."
        ),
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="run members 1,2,4 x tasks 20,50 plus a 2-member SIGKILL case",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run the pytest-sized representative: 2 members, 4 tasks, 0.05s CPU",
    )
    parser.add_argument("--members", help="comma-separated member counts, e.g. 1,2,4")
    parser.add_argument("--tasks", help="comma-separated task counts, e.g. 20,50")
    parser.add_argument(
        "--cpu-busy-seconds",
        type=float,
        default=None,
        help="CPU busy loop seconds per Script Task (default: 0.15, or 0.05 with --quick)",
    )
    parser.add_argument(
        "--fault",
        action="store_true",
        help="include a 2-member SIGKILL acceptance case",
    )
    parser.add_argument(
        "--no-fault",
        action="store_true",
        help="skip the SIGKILL acceptance case",
    )
    parser.add_argument(
        "--fault-members",
        type=int,
        default=2,
        help="member count for the SIGKILL case (default: 2, minimum 2)",
    )
    parser.add_argument(
        "--output",
        help="also write the JSON report to this path",
    )
    args = parser.parse_args(argv)
    if args.quick and args.matrix:
        raise ArgumentError("use either --quick or --matrix, not both")
    if args.fault and args.no_fault:
        raise ArgumentError("use either --fault or --no-fault, not both")
    if args.quick:
        args.member_counts = [QUICK_MEMBER_COUNT]
        args.task_counts = [QUICK_TASK_COUNT]
        if args.cpu_busy_seconds is None:
            args.cpu_busy_seconds = QUICK_CPU_BUSY_SECONDS
        args.include_fault = not args.no_fault
    elif args.matrix or (args.members is None and args.tasks is None):
        if not args.matrix and (args.members is None and args.tasks is None):
            raise ArgumentError(
                "specify --matrix, --quick, or both --members and --tasks"
            )
        args.member_counts = (
            parse_int_list(args.members, label="members")
            if args.members
            else list(SUPPORTED_MEMBER_COUNTS)
        )
        args.task_counts = (
            parse_int_list(args.tasks, label="tasks")
            if args.tasks
            else list(SUPPORTED_TASK_COUNTS)
        )
        if args.cpu_busy_seconds is None:
            args.cpu_busy_seconds = DEFAULT_CPU_BUSY_SECONDS
        args.include_fault = not args.no_fault
    else:
        if args.members is None or args.tasks is None:
            raise ArgumentError("both --members and --tasks are required")
        args.member_counts = parse_int_list(args.members, label="members")
        args.task_counts = parse_int_list(args.tasks, label="tasks")
        if args.cpu_busy_seconds is None:
            args.cpu_busy_seconds = DEFAULT_CPU_BUSY_SECONDS
        args.include_fault = args.fault and not args.no_fault
    for count in args.member_counts:
        if not 1 <= count <= 32:
            raise ArgumentError(f"members must be between 1 and 32: {count}")
    for count in args.task_counts:
        if count < 1:
            raise ArgumentError(f"tasks must be >= 1: {count}")
    if args.cpu_busy_seconds < 0:
        raise ArgumentError("cpu-busy-seconds must be >= 0")
    if args.fault_members < 2 or args.fault_members > 32:
        raise ArgumentError("fault-members must be between 2 and 32")
    return args


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    for members in args.member_counts:
        for tasks in args.task_counts:
            with tempfile.TemporaryDirectory(prefix="wf-exec-pool-load-") as tmp:
                patcher = SimplePatcher()
                try:
                    scenarios.append(asyncio.run(run_load_scenario(
                        tmp_path=Path(tmp),
                        patcher=patcher,
                        executor_count=members,
                        task_count=tasks,
                        cpu_busy_seconds=args.cpu_busy_seconds,
                    )))
                except Exception as exc:  # noqa: BLE001 - keep JSON output
                    scenarios.append(_empty_scenario(
                        name="load",
                        members=members,
                        tasks=tasks,
                        cpu_busy_seconds=args.cpu_busy_seconds,
                        errors=[f"{type(exc).__name__}: {exc}"],
                    ))
                finally:
                    patcher.undo()
    if args.include_fault:
        with tempfile.TemporaryDirectory(prefix="wf-exec-pool-fault-") as tmp:
            patcher = SimplePatcher()
            try:
                scenarios.append(asyncio.run(run_fault_scenario(
                    tmp_path=Path(tmp),
                    patcher=patcher,
                    executor_count=args.fault_members,
                )))
            except Exception as exc:  # noqa: BLE001 - keep JSON output
                scenarios.append(_empty_scenario(
                    name="fault_sigkill",
                    members=args.fault_members,
                    tasks=args.fault_members,
                    cpu_busy_seconds=None,
                    errors=[f"{type(exc).__name__}: {exc}"],
                ))
            finally:
                patcher.undo()
    return build_report(scenarios)


def emit_report(report: dict[str, Any], output: str | None = None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()
    if output:
        Path(output).write_text(text, encoding="utf-8")


def run_cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_benchmark_args(argv)
    except ArgumentError as exc:
        emit_report({
            "ok": False,
            "errors": [str(exc)],
            "platform": sys.platform,
            "linux_pss_available": linux_pss_available(),
            "pss_available": linux_pss_available(),
            "scenarios": [],
        })
        return 2
    except SystemExit as exc:
        if exc.code in (0, None):
            return 0
        emit_report({
            "ok": False,
            "errors": ["invalid arguments"],
            "platform": sys.platform,
            "linux_pss_available": linux_pss_available(),
            "pss_available": linux_pss_available(),
            "scenarios": [],
        })
        return int(exc.code or 2)
    report = run_benchmark(args)
    emit_report(report, args.output)
    return 0 if report.get("ok") is True else 1
