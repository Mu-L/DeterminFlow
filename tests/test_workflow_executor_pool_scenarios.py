"""Real process/4 distribution and focused member-recovery scenarios."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def _load_harness():
    path = Path(__file__).with_name("workflow_executor_pool_harness.py")
    spec = importlib.util.spec_from_file_location(
        "workflow_executor_pool_harness", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_harness = _load_harness()
HOLD_SCRIPT = _harness.HOLD_SCRIPT
PROCESS_WAIT_TIMEOUT = _harness.PROCESS_WAIT_TIMEOUT
QUICK_SCRIPT = _harness.QUICK_SCRIPT
RESTART_WAIT_TIMEOUT = _harness.RESTART_WAIT_TIMEOUT
TASK_WAIT_TIMEOUT = _harness.TASK_WAIT_TIMEOUT
ProcessTracker = _harness.ProcessTracker
assert_distinct_member_pids = _harness.assert_distinct_member_pids
controller_manager = _harness.controller_manager
install_script_workflow = _harness.install_script_workflow
isolate_executor_runtime = _harness.isolate_executor_runtime
process_alive = _harness.process_alive
read_int_file = _harness.read_int_file
start_real_pool = _harness.start_real_pool
stop_pool_and_collect_leftovers = _harness.stop_pool_and_collect_leftovers
task_binding = _harness.task_binding
wait_processes_dead = _harness.wait_processes_dead
wait_until = _harness.wait_until


FAIL_SCRIPT = """\
import os
import sys
from pathlib import Path

path = Path(os.environ["DETERMINFLOW_MARKER_DIR"]) / (os.environ["TASK_ID"] + "-attempts")
with path.open("a", encoding="utf-8") as handle:
    handle.write(str(os.getppid()) + "\\n")
sys.exit(7)
"""

REJECT_UPSTREAM_SCRIPT = """\
import os
from pathlib import Path

path = Path(os.environ["DETERMINFLOW_MARKER_DIR"]) / (os.environ["TASK_ID"] + "-reject")
with path.open("a", encoding="utf-8") as handle:
    handle.write(str(os.getppid()) + "\\n")
print("<script_out>candidate</script_out>")
"""

REJECT_VALIDATOR_SCRIPT = """\
print('<WF_REJECT_UPSTREAM target="producer">[invalid] retry producer</WF_REJECT_UPSTREAM>')
"""


def _identities(pool):
    return {identity.executor_id: identity for identity in pool.identities}


def _read_pid_lines(path: Path) -> list[int]:
    return [
        int(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _install_reject_workflow(manager, runtime, workflow_id: str) -> None:
    created = manager.create_workflow({
        "workflow_id": workflow_id,
        "name": workflow_id,
        "nodes": [
            {
                "id": "producer",
                "label": "producer",
                "node_type": "script",
                "node_params": {
                    "script_source": "inline",
                    "script_type": "python",
                    "script_name": "producer",
                    "timeout": "30",
                },
            },
            {
                "id": "validator",
                "label": "validator",
                "node_type": "script",
                "enable_reject_upstream": True,
                "max_reject_count": 1,
                "node_params": {
                    "script_source": "inline",
                    "script_type": "python",
                    "script_name": "validator",
                    "timeout": "30",
                    "enable_reject_upstream": True,
                },
            },
        ],
        "edges": [{
            "id": "producer-validator",
            "source": "producer",
            "target": "validator",
        }],
    })
    assert "definition" in created, created
    script_dir = runtime.workflows_dir / workflow_id / "script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "producer.py").write_text(
        REJECT_UPSTREAM_SCRIPT, encoding="utf-8",
    )
    (script_dir / "validator.py").write_text(
        REJECT_VALIDATOR_SCRIPT, encoding="utf-8",
    )


def test_real_default_pool_distributes_twenty_tasks_and_keeps_sticky_binding(
    tmp_path, monkeypatch,
):
    runtime = isolate_executor_runtime(tmp_path, monkeypatch)

    async def scenario():
        pool = None
        tracker = ProcessTracker()
        leftover = []
        try:
            pool = await start_real_pool(runtime, executor_count=4)
            member_pids = assert_distinct_member_pids(pool, expected_count=4)
            tracker.add_pool(pool)
            identities = _identities(pool)

            manager = controller_manager()
            manager.attach_execution_delegate(pool)
            workflow_id = "wf-pool-quick"
            install_script_workflow(
                manager,
                runtime,
                workflow_id=workflow_id,
                script_name="quick",
                source=QUICK_SCRIPT,
            )
            task_ids = [
                manager.create_task(workflow_id)["task_id"] for _ in range(20)
            ]
            started_results = await asyncio.gather(*(
                manager.run_task(workflow_id, task_id) for task_id in task_ids
            ))
            for started in started_results:
                assert started.get("success") is True, started

            def task_completed(task_id: str) -> bool:
                task = manager._load_task(workflow_id, task_id)
                return task is not None and task.status == "completed"

            for task_id in task_ids:
                await wait_until(
                    lambda task_id=task_id: task_completed(task_id),
                    timeout=TASK_WAIT_TIMEOUT,
                    message=f"script task {task_id} to complete",
                )

            persisted = [
                manager._load_task(workflow_id, task_id) for task_id in task_ids
            ]
            assigned_ids = [task.executor_id for task in persisted]
            assert {
                executor_id: assigned_ids.count(executor_id)
                for executor_id in set(assigned_ids)
            } == {
                f"workflow-executor-{index}": 5 for index in range(4)
            }
            for task in persisted:
                identity = identities[task.executor_id]
                assert task.executor_epoch == identity.epoch
                node = task.node_states.get("quick")
                assert node is not None, task_binding(task)
                assert node.status == "completed", {
                    "task_id": task.task_id,
                    "error": node.error,
                    "stdout": node.stdout,
                    "stderr": node.stderr,
                }
                marker = runtime.marker_dir / task.task_id
                marker_data = json.loads(
                    marker.read_text(encoding="utf-8")
                )
                assert marker_data["ppid"] == member_pids[task.executor_id]
                assert marker_data["completed_at"] >= marker_data["started_at"]

            first_bindings = [
                (task.executor_id, task.executor_epoch) for task in persisted
            ]
            for task_id in task_ids:
                await manager.stop_task(workflow_id, task_id)
            reloaded = [
                manager._load_task(workflow_id, task_id) for task_id in task_ids
            ]
            assert [
                (task.executor_id, task.executor_epoch) for task in reloaded
            ] == first_bindings
            assert pool.member_pids == member_pids
            assert _identities(pool) == identities
        finally:
            leftover = await stop_pool_and_collect_leftovers(pool, tracker, runtime)

        assert leftover == []
        if pool is not None:
            assert pool.member_pids == {}

    asyncio.run(scenario())


def test_real_pool_routes_retry_and_skip_to_original_executor(
    tmp_path, monkeypatch,
):
    runtime = isolate_executor_runtime(tmp_path, monkeypatch)

    async def scenario():
        pool = None
        tracker = ProcessTracker()
        try:
            pool = await start_real_pool(runtime)
            member_pids = assert_distinct_member_pids(pool)
            tracker.add_pool(pool)
            manager = controller_manager()
            manager.attach_execution_delegate(pool)
            workflow_id = "wf-pool-sticky-controls"
            install_script_workflow(
                manager,
                runtime,
                workflow_id=workflow_id,
                script_name="fail",
                source=FAIL_SCRIPT,
            )
            task_id = manager.create_task(workflow_id)["task_id"]
            started = await manager.run_task(workflow_id, task_id)
            assert started.get("success") is True, started

            await wait_until(
                lambda: (
                    (task := manager._load_task(workflow_id, task_id)) is not None
                    and task.status == "failed"
                ),
                timeout=TASK_WAIT_TIMEOUT,
                message="initial Script failure",
            )
            initial = manager._load_task(workflow_id, task_id)
            binding = (initial.executor_id, initial.executor_epoch)
            first_attempt = initial.node_states["fail"].attempt_count

            retried = await manager.retry_node(
                workflow_id, task_id, "fail", first_attempt,
            )
            assert retried.get("success") is True, retried
            await wait_until(
                lambda: (
                    (task := manager._load_task(workflow_id, task_id)) is not None
                    and task.status == "failed"
                    and task.node_states["fail"].attempt_count > first_attempt
                ),
                timeout=TASK_WAIT_TIMEOUT,
                message="manual retry to fail on its second attempt",
            )
            after_retry = manager._load_task(workflow_id, task_id)
            assert (after_retry.executor_id, after_retry.executor_epoch) == binding
            second_attempt = after_retry.node_states["fail"].attempt_count

            skipped = await manager.skip_node(
                workflow_id, task_id, "fail", second_attempt,
            )
            assert skipped.get("success") is True, skipped
            await wait_until(
                lambda: (
                    (task := manager._load_task(workflow_id, task_id)) is not None
                    and task.status == "completed"
                ),
                timeout=TASK_WAIT_TIMEOUT,
                message="skipped Task to complete",
            )
            final = manager._load_task(workflow_id, task_id)
            assert (final.executor_id, final.executor_epoch) == binding
            assert final.node_states["fail"].status == "skipped"
            attempt_pids = _read_pid_lines(
                runtime.marker_dir / f"{task_id}-attempts",
            )
            assert len(attempt_pids) == 2
            assert set(attempt_pids) == {member_pids[binding[0]]}
            assert pool.member_pids == member_pids
        finally:
            leftover = await stop_pool_and_collect_leftovers(
                pool, tracker, runtime,
            )
        assert leftover == []

    asyncio.run(scenario())


def test_real_pool_reject_upstream_retries_inside_original_executor(
    tmp_path, monkeypatch,
):
    runtime = isolate_executor_runtime(tmp_path, monkeypatch)

    async def scenario():
        pool = None
        tracker = ProcessTracker()
        try:
            pool = await start_real_pool(runtime)
            member_pids = assert_distinct_member_pids(pool)
            tracker.add_pool(pool)
            manager = controller_manager()
            manager.attach_execution_delegate(pool)
            workflow_id = "wf-pool-sticky-reject"
            _install_reject_workflow(manager, runtime, workflow_id)
            task_id = manager.create_task(workflow_id)["task_id"]
            started = await manager.run_task(workflow_id, task_id)
            assert started.get("success") is True, started

            await wait_until(
                lambda: (
                    (task := manager._load_task(workflow_id, task_id)) is not None
                    and task.status in {"failed", "completed"}
                ),
                timeout=TASK_WAIT_TIMEOUT,
                message="reject_upstream scenario to reach a terminal state",
            )
            task = manager._load_task(workflow_id, task_id)
            assert task.status == "failed", task_binding(task)
            binding = (task.executor_id, task.executor_epoch)
            producer = task.node_states["producer"]
            assert producer.attempt_count == 2
            assert producer.reject_upstream_count == 1
            producer_pids = _read_pid_lines(
                runtime.marker_dir / f"{task_id}-reject",
            )
            assert len(producer_pids) == 2
            assert set(producer_pids) == {member_pids[binding[0]]}
            assert pool.member_pids == member_pids
        finally:
            leftover = await stop_pool_and_collect_leftovers(
                pool, tracker, runtime,
            )
        assert leftover == []

    asyncio.run(scenario())


def test_real_pool_sigkill_recovers_only_victim_and_reaps_old_script_tree(
    tmp_path, monkeypatch,
):
    runtime = isolate_executor_runtime(tmp_path, monkeypatch)

    async def scenario():
        pool = None
        tracker = ProcessTracker()
        leftover = []
        try:
            handoffs = []
            manager = None
            pool = None

            async def on_restart(previous, current):
                reassigned = manager.reassign_dead_executor_generation(
                    previous, current,
                )
                recovery = await pool.client_for(current.executor_id).call(
                    "recover_owned_tasks",
                )
                handoffs.append((previous, current, reassigned, recovery))

            pool = await start_real_pool(runtime, on_restart=on_restart)
            original_pids = assert_distinct_member_pids(pool)
            tracker.add_pool(pool)
            original_identities = _identities(pool)

            manager = controller_manager()
            manager.attach_execution_delegate(pool)
            workflow_id = "wf-pool-hold"
            install_script_workflow(
                manager,
                runtime,
                workflow_id=workflow_id,
                script_name="hold",
                source=HOLD_SCRIPT,
                auto_retry_count=1,
                auto_retry_interval_seconds=0,
            )
            task_ids = [
                manager.create_task(workflow_id)["task_id"] for _ in range(2)
            ]
            for task_id in task_ids:
                started = await manager.run_task(workflow_id, task_id)
                assert started.get("success") is True, started

            for task_id in task_ids:
                await wait_until(
                    lambda task_id=task_id: (
                        runtime.hold_dir / task_id / "ready"
                    ).is_file(),
                    timeout=TASK_WAIT_TIMEOUT,
                    message=f"hold script {task_id} to become ready",
                )

            assigned = {
                task.executor_id: task
                for task in (
                    manager._load_task(workflow_id, task_id)
                    for task_id in task_ids
                )
            }
            assert set(assigned) == {
                "workflow-executor-0",
                "workflow-executor-1",
            }
            victim_task = assigned["workflow-executor-0"]
            sibling_task = assigned["workflow-executor-1"]
            sibling_snapshot = task_binding(sibling_task)
            assert sibling_snapshot["executor_epoch"] == (
                original_identities["workflow-executor-1"].epoch
            )
            assert sibling_snapshot["status"] == "running"

            def hold_pids(task_id: str) -> tuple[int, int]:
                script_pid = read_int_file(
                    runtime.hold_dir / task_id / "script.pid",
                )
                child_pid = read_int_file(
                    runtime.hold_dir / task_id / "child.pid",
                )
                assert script_pid is not None
                assert child_pid is not None
                assert process_alive(script_pid)
                assert process_alive(child_pid)
                return script_pid, child_pid

            victim_script_pid, victim_child_pid = hold_pids(victim_task.task_id)
            sibling_script_pid, sibling_child_pid = hold_pids(
                sibling_task.task_id,
            )
            tracker.add_pid(victim_script_pid)
            tracker.add_pid(victim_child_pid)
            tracker.add_pid(sibling_script_pid)
            tracker.add_pid(sibling_child_pid)

            _harness.force_kill_pid(original_pids["workflow-executor-0"])
            await wait_until(
                lambda: (
                    pool.member_pids.get("workflow-executor-0")
                    not in {None, original_pids["workflow-executor-0"]}
                    and _identities(pool)["workflow-executor-0"].epoch
                    != original_identities["workflow-executor-0"].epoch
                    and handoffs
                ),
                timeout=RESTART_WAIT_TIMEOUT,
                message="victim executor to restart with a new pid and epoch",
            )
            tracker.add_pool(pool)

            await wait_processes_dead(
                [victim_script_pid, victim_child_pid],
                timeout=PROCESS_WAIT_TIMEOUT,
                message="old victim script descendants to be reaped",
            )
            assert process_alive(sibling_script_pid)
            assert process_alive(sibling_child_pid)
            assert pool.member_pids["workflow-executor-1"] == original_pids[
                "workflow-executor-1"
            ]
            assert (
                _identities(pool)["workflow-executor-1"].epoch
                == original_identities["workflow-executor-1"].epoch
            )
            sibling_after_kill = manager._load_task(
                workflow_id, sibling_task.task_id,
            )
            assert task_binding(sibling_after_kill) == sibling_snapshot

            previous, current, reassigned, recovery = handoffs[0]
            assert previous.executor_id == "workflow-executor-0"
            assert current.executor_id == "workflow-executor-0"
            assert reassigned == 1
            assert recovery["scanned"] == 1
            await wait_until(
                lambda: (
                    (new_pid := read_int_file(
                        runtime.hold_dir / victim_task.task_id / "script.pid"
                    )) is not None
                    and new_pid != victim_script_pid
                    and process_alive(new_pid)
                ),
                timeout=TASK_WAIT_TIMEOUT,
                message="victim Task to resume inside the new Executor generation",
            )
            recovered_victim = manager._load_task(
                workflow_id, victim_task.task_id,
            )
            recovered_sibling = manager._load_task(
                workflow_id, sibling_task.task_id,
            )
            assert recovered_victim.executor_id == "workflow-executor-0"
            assert recovered_victim.executor_epoch == current.epoch
            assert recovered_victim.executor_epoch != previous.epoch
            assert recovered_victim.status == "running"
            assert task_binding(recovered_sibling) == sibling_snapshot
            for path in (runtime.hold_dir / victim_task.task_id).glob("*.pid"):
                tracker.add_pid(read_int_file(path))
            assert process_alive(sibling_script_pid)
            assert process_alive(sibling_child_pid)
            assert pool.member_pids["workflow-executor-1"] == original_pids[
                "workflow-executor-1"
            ]
            assert (
                _identities(pool)["workflow-executor-1"].epoch
                == original_identities["workflow-executor-1"].epoch
            )

            _harness.release_hold(runtime, victim_task.task_id)
            await wait_until(
                lambda: manager._load_task(
                    workflow_id, victim_task.task_id,
                ).status == "completed",
                timeout=TASK_WAIT_TIMEOUT,
                message="recovered victim Task to complete",
            )

            stopped_sibling = await manager.stop_task(
                workflow_id, sibling_task.task_id,
            )
            assert stopped_sibling.get("success") is True, stopped_sibling
            stopped_binding = task_binding(
                manager._load_task(workflow_id, sibling_task.task_id)
            )
            assert stopped_binding["executor_id"] == sibling_snapshot["executor_id"]
            assert stopped_binding["executor_epoch"] == (
                sibling_snapshot["executor_epoch"]
            )
        finally:
            leftover = await stop_pool_and_collect_leftovers(
                pool, tracker, runtime,
            )

        assert leftover == []
        if pool is not None:
            assert pool.member_pids == {}

    asyncio.run(scenario())
