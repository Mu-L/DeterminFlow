"""Start the frozen backend and verify the real status endpoint."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.workflow.executor_process import force_kill_pid, process_is_alive


LOGGER = logging.getLogger("desktop.smoke_backend")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _seed_smoke_model(user_root: Path) -> None:
    config_dir = user_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    models = {
        "default_params": {
            "thinking_enabled": False,
            "reasoning_effort": "low",
            "temperature": 0,
            "top_p": 1.0,
            "presence_penalty": 0.0,
            "thinking_budget": None,
        },
        "providers": {
            "desktop-smoke": {
                "name": "Desktop Smoke",
                "provider_type": "openai_compatible",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "smoke-key-not-used",
                "models": ["script-only"],
            },
        },
    }
    (config_dir / "models_config.json").write_text(
        json.dumps(models, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    defaults = (
        Path(__file__).resolve().parents[1]
        / "generated"
        / "default-config"
        / "agents_config.json"
    )
    agents = json.loads(defaults.read_text(encoding="utf-8"))
    agents["agents"]["main"]["model"] = "desktop-smoke:script-only"
    (config_dir / "agents_config.json").write_text(
        json.dumps(agents, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 5.0,
) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}: {url}")
            parsed = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code}: {url}: {details[:2000]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"JSON response is not an object: {url}")
    return parsed


def _assert_process_pool(status: dict, controller_pid: int) -> list[int]:
    pool = status.get("workflow_executor")
    if not isinstance(pool, dict):
        raise RuntimeError("status did not include workflow_executor")
    expected = {
        "mode": "process",
        "configured_count": 4,
        "member_count": 4,
        "ready_count": 4,
        "reachable_count": 4,
    }
    mismatches = {
        key: {"expected": value, "actual": pool.get(key)}
        for key, value in expected.items()
        if pool.get(key) != value
    }
    if pool.get("available") is not True or pool.get("degraded") is not False:
        mismatches["availability"] = {
            "available": pool.get("available"),
            "degraded": pool.get("degraded"),
            "reason": pool.get("reason"),
        }
    members = pool.get("members")
    if not isinstance(members, list):
        members = []
    pids = [member.get("pid") for member in members if isinstance(member, dict)]
    if (
        len(pids) != 4
        or any(not isinstance(pid, int) or pid <= 0 for pid in pids)
        or len(set(pids)) != 4
        or controller_pid in pids
    ):
        mismatches["member_pids"] = pids
    if mismatches:
        raise RuntimeError(
            "frozen backend process/4 status mismatch: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )
    return [int(pid) for pid in pids]


def _exercise_workflow_distribution(base_url: str, user_root: Path) -> None:
    workflow_id = "desktop-process-pool-smoke"
    script_name = "smoke"
    _request_json(
        f"{base_url}/api/workflows",
        method="POST",
        payload={
            "workflow_id": workflow_id,
            "name": "Desktop process pool smoke",
            "nodes": [{
                "id": script_name,
                "label": script_name,
                "node_type": "script",
                "node_params": {
                    "script_source": "inline",
                    "script_type": "python",
                    "script_name": script_name,
                    "timeout": "30",
                },
            }],
            "edges": [
                {
                    "id": "start-smoke",
                    "source": "__start__",
                    "target": script_name,
                },
                {
                    "id": "smoke-end",
                    "source": script_name,
                    "target": "__end__",
                },
            ],
        },
    )
    script_dir = user_root / "data" / "workflows" / workflow_id / "script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / f"{script_name}.py").write_text(
        'print("<script_out>ok</script_out>")\n', encoding="utf-8",
    )

    task_ids: list[str] = []
    for _ in range(20):
        created = _request_json(
            f"{base_url}/api/workflows/{workflow_id}/tasks",
            method="POST",
            payload={},
        )
        task_id = created.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError(f"invalid task creation response: {created}")
        task_ids.append(task_id)
    for task_id in task_ids:
        _request_json(
            f"{base_url}/api/workflows/{workflow_id}/tasks/{task_id}/run",
            method="POST",
            payload={},
        )

    deadline = time.monotonic() + 60.0
    completed: dict[str, dict] = {}
    while time.monotonic() < deadline and len(completed) < len(task_ids):
        for task_id in task_ids:
            if task_id in completed:
                continue
            detail = _request_json(
                f"{base_url}/api/workflows/{workflow_id}/tasks/{task_id}",
            )
            task = detail.get("task")
            if isinstance(task, dict) and task.get("status") in {
                "completed", "failed", "stopped",
            }:
                completed[task_id] = task
        if len(completed) < len(task_ids):
            time.sleep(0.1)
    if len(completed) != len(task_ids):
        missing = sorted(set(task_ids) - set(completed))
        raise RuntimeError(f"workflow tasks did not finish: {missing}")
    failed = {}
    for task_id, task in completed.items():
        if task.get("status") == "completed":
            continue
        node = (task.get("node_states") or {}).get(script_name) or {}
        failed[task_id] = {
            "status": task.get("status"),
            "node_status": node.get("status"),
            "error": node.get("error"),
            "stderr": str(node.get("stderr") or "")[-1000:],
        }
    if failed:
        raise RuntimeError(f"workflow tasks failed: {failed}")
    assignments = Counter(task.get("executor_id") for task in completed.values())
    expected = {f"workflow-executor-{index}": 5 for index in range(4)}
    if dict(assignments) != expected:
        raise RuntimeError(
            f"process/4 assignment mismatch: expected={expected}, "
            f"actual={dict(assignments)}"
        )


def _wait_for_processes_exit(pids: list[int], timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = [pid for pid in pids if process_is_alive(pid)]
        if not live:
            return []
        time.sleep(0.1)
    return [pid for pid in pids if process_is_alive(pid)]


def smoke_backend(executable: Path, timeout: float = 60.0) -> None:
    port = _free_port()
    member_pids: list[int] = []
    with tempfile.TemporaryDirectory(prefix="determinflow-desktop-smoke-") as root:
        user_root = Path(root)
        _seed_smoke_model(user_root)
        log_path = Path(root) / "backend.log"
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                [
                    str(executable),
                    "--port",
                    str(port),
                    "--user-data-dir",
                    root,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + timeout
                url = f"http://127.0.0.1:{port}/api/system/status"
                base_url = f"http://127.0.0.1:{port}"
                last_status_error: Exception | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        status = _request_json(url, timeout=2)
                        member_pids = _assert_process_pool(status, process.pid)
                        break
                    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                        last_status_error = exc
                        time.sleep(0.25)
                if member_pids:
                    try:
                        _exercise_workflow_distribution(base_url, user_root)
                    except Exception as exc:
                        log_file.flush()
                        details = log_path.read_text(
                            encoding="utf-8", errors="replace",
                        )[-10000:]
                        raise RuntimeError(
                            f"冻结后端 Workflow smoke 失败: {exc}\n{details}"
                        ) from exc
                    LOGGER.info("冻结后端 process/4 与 20 Task 分配验证通过")
                    return
                log_file.flush()
                details = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
                raise RuntimeError(
                    f"冻结后端未就绪，退出码={process.poll()}，"
                    f"最后状态错误={last_status_error}\n{details}"
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                leftovers = _wait_for_processes_exit(member_pids, 30.0)
                if leftovers:
                    for pid in leftovers:
                        force_kill_pid(pid)
                    raise RuntimeError(
                        f"frozen backend left Executor processes: {leftovers}"
                    )


def main() -> int:
    default_name = "determinflow-backend.exe" if sys.platform == "win32" else "determinflow-backend"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "executable",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "runtime" / "backend" / default_name,
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    options = parser.parse_args()
    smoke_backend(options.executable.resolve(), timeout=options.timeout)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
