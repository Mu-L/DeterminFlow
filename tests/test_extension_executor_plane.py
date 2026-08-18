from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware

from src.extension_api import (
    CoreRuntime,
    HealthCheckResult,
    PromptContextRequest,
    PromptContribution,
)
from src.extension_host import ExtensionManager, LayeredJsonConfig
from src.plugin_system import ProcessSpec


_EVENTS: list[str] = []
_CREATED: dict[str, object] = {}
_test_router = APIRouter()


@_test_router.get("/api/executor-plane/ping")
async def _executor_plane_ping():
    return {"ok": True}


class _ForbiddenList(list):
    def __iter__(self):
        raise AssertionError("executor plane must not handle HTTP contributions")


class _CountingMiddleware(BaseHTTPMiddleware):
    inits = 0

    def __init__(self, app, **kwargs):
        type(self).inits += 1
        super().__init__(app, **kwargs)

    async def dispatch(self, request, call_next):
        return await call_next(request)


class _PromptProvider:
    async def provide(self, request: PromptContextRequest):
        _EVENTS.append("prompt")
        return PromptContribution("executor-prompt")


class _SessionHook:
    async def on_session_end(self, session) -> None:
        _EVENTS.append("session")


class _FakeToolRegistry:
    def __init__(self):
        self.unregistered: list[str] = []
        self.registered: list[str] = []

    def unregister_owner(self, owner: str) -> None:
        self.unregistered.append(owner)
        self.registered.clear()


class _ExecutorPlaneExtension:
    def __init__(self) -> None:
        self.start_runtime = None

    def register(self, registrar) -> None:
        registrar.add_router(_test_router)
        registrar.add_middleware(_CountingMiddleware)
        registrar.add_health_check(self._health)
        registrar.add_tool_contributor(self._tool)
        registrar.add_prompt_context_provider(_PromptProvider())
        registrar.add_session_hook(_SessionHook())

    @staticmethod
    def _health(runtime):
        _EVENTS.append("health")
        return HealthCheckResult(False, "health should not run")

    @staticmethod
    def _tool(registry, runtime) -> None:
        _EVENTS.append("tool")
        registry.registered.append("executor-tool")

    async def start(self, runtime) -> None:
        _EVENTS.append("start")

    async def stop(self) -> None:
        _EVENTS.append("stop")

    async def start_executor(self, runtime) -> None:
        _EVENTS.append("start_executor")
        self.start_runtime = runtime

    async def stop_executor(self) -> None:
        _EVENTS.append("stop_executor")


class _ResourceContributingExtension:
    def register(self, registrar) -> None:
        registrar.add_router(_test_router)
        registrar.add_middleware(_CountingMiddleware)
        registrar.add_health_check(self._health)
        registrar.add_tool_contributor(self._tool)
        registrar.add_prompt_context_provider(_PromptProvider())
        registrar.add_session_hook(_SessionHook())

    @staticmethod
    def _health(runtime):
        _EVENTS.append("health")
        return HealthCheckResult(True, "ok")

    @staticmethod
    def _tool(registry, runtime) -> None:
        _EVENTS.append("tool")
        registry.registered.append("resource-tool")

    async def start(self, runtime) -> None:
        _EVENTS.append("start")

    async def stop(self) -> None:
        _EVENTS.append("stop")


class _FailingExecutorExtension:
    def register(self, registrar) -> None:
        registrar.add_tool_contributor(self._tool)

    @staticmethod
    def _tool(registry, runtime) -> None:
        _EVENTS.append("failing:tool")
        registry.registered.append("failing-tool")

    async def start(self, runtime) -> None:
        _EVENTS.append("start")

    async def stop(self) -> None:
        _EVENTS.append("stop")

    async def start_executor(self, runtime) -> None:
        _EVENTS.append("start_executor")
        raise RuntimeError("executor start exploded")

    async def stop_executor(self) -> None:
        _EVENTS.append("stop_executor")


class _FailingToolExecutorExtension:
    def register(self, registrar) -> None:
        registrar.add_tool_contributor(self._tool)

    @staticmethod
    def _tool(registry, runtime) -> None:
        registry.registered.append("partial-tool")
        raise RuntimeError("tool registration exploded")

    async def start(self, runtime) -> None:
        _EVENTS.append("start")

    async def stop(self) -> None:
        _EVENTS.append("stop")

    async def start_executor(self, runtime) -> None:
        _EVENTS.append("start_executor")

    async def stop_executor(self) -> None:
        _EVENTS.append("stop_executor")


class _DependentExecutorExtension:
    def register(self, registrar) -> None:
        registrar.add_tool_contributor(self._tool)

    @staticmethod
    def _tool(registry, runtime) -> None:
        _EVENTS.append("dependent:tool")
        registry.registered.append("dependent-tool")

    async def start(self, runtime) -> None:
        _EVENTS.append("dependent:start")

    async def stop(self) -> None:
        _EVENTS.append("dependent:stop")

    async def start_executor(self, runtime) -> None:
        _EVENTS.append("dependent:start_executor")

    async def stop_executor(self) -> None:
        _EVENTS.append("dependent:stop_executor")


def _create_executor_plane_extension() -> _ExecutorPlaneExtension:
    extension = _ExecutorPlaneExtension()
    _CREATED["hook"] = extension
    return extension


def _create_resource_contributing_extension() -> _ResourceContributingExtension:
    return _ResourceContributingExtension()


def _create_failing_executor_extension() -> _FailingExecutorExtension:
    return _FailingExecutorExtension()


def _create_failing_tool_executor_extension() -> _FailingToolExecutorExtension:
    return _FailingToolExecutorExtension()


def _create_dependent_executor_extension() -> _DependentExecutorExtension:
    return _DependentExecutorExtension()


def _runtime(tool_registry=None) -> CoreRuntime:
    return CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=tool_registry or _FakeToolRegistry(),
        event_publisher=None,
    )


def _write_manifest(
    path: Path,
    extension_id: str,
    *,
    dependencies: list[str] | None = None,
    extra: str = "",
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    dependency_values = ", ".join(f'"{item}"' for item in dependencies or [])
    (path / "extension.toml").write_text(
        "\n".join([
            "[extension]",
            f'id = "{extension_id}"',
            f'name = "{extension_id}"',
            'version = "1.0.0"',
            'api_version = "1"',
            f"dependencies = [{dependency_values}]",
            extra,
        ]),
        encoding="utf-8",
    )
    return path


def _backend(factory: str) -> str:
    return f'backend = "{__name__}:{factory}"'


def _write_agents(extension_dir: Path, agent_id: str) -> None:
    resources = extension_dir / "resources"
    resources.mkdir(exist_ok=True)
    (resources / "agents.json").write_text(
        json.dumps({"agents": {agent_id: {"model": agent_id}}}),
        encoding="utf-8",
    )


def _controller_side_effects() -> str:
    return """
[lifecycle]
migrate_command = ["${PYTHON}", "-c", "open('MIGRATED','w').write('1')"]
verify_command = ["${PYTHON}", "-c", "open('VERIFIED','w').write('1')"]

[[processes]]
id = "worker"
command = ["${PYTHON}", "-c", "open('PROCESS_STARTED','w').write('1'); import time; time.sleep(30)"]
"""


def _manager(
    tmp_path: Path,
    enabled: list[str],
    *,
    strict: bool = False,
) -> ExtensionManager:
    if strict:
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "extensions.json").write_text(
            json.dumps({"enabled": enabled, "strict_startup": True}),
            encoding="utf-8",
        )
        return ExtensionManager(tmp_path, discover_entry_points=False)
    return ExtensionManager(
        tmp_path,
        enabled=enabled,
        discover_entry_points=False,
    )


def _forbid_http(manager: ExtensionManager) -> None:
    manager.contributions.routers = _ForbiddenList(manager.contributions.routers)
    manager.contributions.middleware = _ForbiddenList(manager.contributions.middleware)
    manager.contributions.health_checks = _ForbiddenList(
        manager.contributions.health_checks
    )


def _agent_store(tmp_path: Path, manager: ExtensionManager) -> LayeredJsonConfig:
    base_file = tmp_path / "config" / "agents.json"
    base_file.parent.mkdir(parents=True, exist_ok=True)
    if not base_file.exists():
        base_file.write_text(
            json.dumps({"agents": {"main": {"model": "core"}}}),
            encoding="utf-8",
        )
    return LayeredJsonConfig(
        base_file,
        manager.resource_paths("agents"),
        dict_sections=["agents"],
        owner_enabled=manager.is_running,
    )


@pytest.fixture(autouse=True)
def _reset_plane_globals():
    _EVENTS.clear()
    _CREATED.clear()
    _CountingMiddleware.inits = 0
    yield


def test_start_executor_skips_lifecycle_processes_start_health_and_http(
    tmp_path: Path,
    monkeypatch,
):
    extension_dir = tmp_path / "extensions" / "with-hook"
    _write_manifest(
        extension_dir,
        "with-hook",
        extra="\n".join([
            _backend("_create_executor_plane_extension"),
            _controller_side_effects(),
            '[resources]\nagents = "resources/agents.json"',
        ]),
    )
    _write_agents(extension_dir, "with-hook.agent")
    manager = _manager(tmp_path, ["with-hook"])
    registry = _FakeToolRegistry()
    process_starts: list[str] = []
    original_start = manager.process_manager.start

    async def wrapped_start(owner, specs, **kwargs):
        process_starts.append(owner)
        return await original_start(owner, specs, **kwargs)

    manager.process_manager.start = wrapped_start
    monkeypatch.setattr(
        "src.extension_host.manager.load_extension_lifecycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lifecycle must not load on executor plane")
        ),
    )
    monkeypatch.setattr(
        "src.extension_host.manager.run_extension_lifecycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lifecycle must not run on executor plane")
        ),
    )
    monkeypatch.setattr(
        "src.extension_host.manager.start_manifest_processes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("manifest processes must not start on executor plane")
        ),
    )
    _forbid_http(manager)

    asyncio.run(manager.start_executor(_runtime(registry)))

    assert manager.is_running("with-hook") is True
    assert manager.get_state("with-hook")["status"] == "running"
    assert "with-hook" not in manager._started_extensions
    assert _EVENTS == ["start_executor", "tool"]
    assert _CountingMiddleware.inits == 0
    assert process_starts == []
    assert manager.process_manager.statuses("with-hook") == []
    assert not (extension_dir / "MIGRATED").exists()
    assert not (extension_dir / "VERIFIED").exists()
    assert not (extension_dir / "PROCESS_STARTED").exists()
    assert registry.registered == ["executor-tool"]
    runtime = _CREATED["hook"].start_runtime
    assert runtime is not None
    assert runtime.resource_owner == "with-hook"
    assert runtime.get_service("plugin_data_dir") == manager.plugin_data_dir / "with-hook"


def test_start_executor_does_not_run_real_lifecycle_or_manifest_processes(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "side-effects"
    _write_manifest(
        extension_dir,
        "side-effects",
        extra="\n".join([
            _backend("_create_resource_contributing_extension"),
            _controller_side_effects(),
        ]),
    )
    manager = _manager(tmp_path, ["side-effects"])

    asyncio.run(manager.start_executor(_runtime()))

    assert manager.is_running("side-effects") is True
    assert _EVENTS == []
    assert not (extension_dir / "MIGRATED").exists()
    assert not (extension_dir / "VERIFIED").exists()
    assert not (extension_dir / "PROCESS_STARTED").exists()
    assert manager.process_manager.statuses("side-effects") == []


def test_start_executor_makes_loaded_resources_visible_without_runtime_hooks(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "files-only"
    _write_manifest(
        extension_dir,
        "files-only",
        extra='[resources]\nagents = "resources/agents.json"\nworkflows = "resources/workflows"',
    )
    _write_agents(extension_dir, "files-only.agent")
    workflow_dir = extension_dir / "resources" / "workflows" / "wf-demo"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps({"workflow_id": "wf-demo", "name": "Demo", "version": 1}),
        encoding="utf-8",
    )
    manager = _manager(tmp_path, ["files-only"])
    store = _agent_store(tmp_path, manager)
    target_root = tmp_path / "data" / "workflows"
    manager.provision_workflows(target_root)

    assert manager.get_state("files-only")["status"] == "loaded"
    assert manager.is_running("files-only") is False
    assert set(store.load()["agents"]) == {"main"}
    assert manager.workflow_owner_enabled(target_root / "wf-demo") is False

    asyncio.run(manager.start_executor(_runtime()))

    assert manager.is_running("files-only") is True
    assert set(store.load()["agents"]) == {"main", "files-only.agent"}
    assert manager.workflow_owner_enabled(target_root / "wf-demo") is True
    assert "files-only" not in manager._executor_runtime_owners
    assert "files-only" not in manager._started_extensions
    assert asyncio.run(
        manager.build_prompt_context(PromptContextRequest(agent_type="main"))
    ) == ""
    asyncio.run(manager.notify_session_end(object()))
    assert _EVENTS == []


def test_start_executor_without_hook_does_not_activate_runtime_contributions(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "no-hook"
    _write_manifest(
        extension_dir,
        "no-hook",
        extra="\n".join([
            _backend("_create_resource_contributing_extension"),
            '[resources]\nagents = "resources/agents.json"',
        ]),
    )
    _write_agents(extension_dir, "no-hook.agent")
    manager = _manager(tmp_path, ["no-hook"])
    registry = _FakeToolRegistry()
    store = _agent_store(tmp_path, manager)
    _forbid_http(manager)

    asyncio.run(manager.start_executor(_runtime(registry)))

    assert manager.is_running("no-hook") is True
    assert set(store.load()["agents"]) == {"main", "no-hook.agent"}
    assert registry.registered == []
    assert "no-hook" not in manager._registered_tool_owners
    assert asyncio.run(
        manager.build_prompt_context(PromptContextRequest(agent_type="main"))
    ) == ""
    asyncio.run(manager.notify_session_end(object()))
    assert _EVENTS == []
    assert _CountingMiddleware.inits == 0


def test_start_executor_hook_registers_tools_prompt_and_session_hooks(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "with-hook"
    _write_manifest(
        extension_dir,
        "with-hook",
        extra=_backend("_create_executor_plane_extension"),
    )
    manager = _manager(tmp_path, ["with-hook"])
    registry = _FakeToolRegistry()

    asyncio.run(manager.start_executor(_runtime(registry)))

    assert registry.registered == ["executor-tool"]
    assert asyncio.run(
        manager.build_prompt_context(PromptContextRequest(agent_type="main"))
    ) == "executor-prompt"
    asyncio.run(manager.notify_session_end(object()))
    assert _EVENTS == ["start_executor", "tool", "prompt", "session"]


def test_start_executor_dependency_failure_is_fail_closed(tmp_path: Path):
    base_dir = tmp_path / "extensions" / "base"
    dependent_dir = tmp_path / "extensions" / "dependent"
    _write_manifest(
        base_dir,
        "base",
        extra=_backend("_create_failing_executor_extension"),
    )
    _write_manifest(
        dependent_dir,
        "dependent",
        dependencies=["base"],
        extra="\n".join([
            _backend("_create_dependent_executor_extension"),
            '[resources]\nagents = "resources/agents.json"',
        ]),
    )
    _write_agents(dependent_dir, "dependent.agent")
    manager = _manager(tmp_path, ["dependent"])
    registry = _FakeToolRegistry()
    store = _agent_store(tmp_path, manager)

    asyncio.run(manager.start_executor(_runtime(registry)))

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["base"]["status"] == "degraded"
    assert "executor start exploded" in statuses["base"]["error"]
    assert statuses["dependent"]["status"] == "blocked"
    assert "依赖扩展 base" in statuses["dependent"]["error"]
    assert manager.is_running("base") is False
    assert manager.is_running("dependent") is False
    assert set(store.load()["agents"]) == {"main"}
    assert registry.registered == []
    assert _EVENTS == ["start_executor", "stop_executor"]


def test_start_executor_missing_dependency_stays_blocked(tmp_path: Path):
    feature_dir = tmp_path / "extensions" / "feature"
    _write_manifest(
        feature_dir,
        "feature",
        dependencies=["not-installed"],
        extra='[resources]\nagents = "resources/agents.json"',
    )
    _write_agents(feature_dir, "feature.agent")
    manager = _manager(tmp_path, ["feature"])
    store = _agent_store(tmp_path, manager)

    asyncio.run(manager.start_executor(_runtime()))

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["not-installed"]["status"] == "missing"
    assert statuses["feature"]["status"] == "blocked"
    assert manager.is_running("feature") is False
    assert set(store.load()["agents"]) == {"main"}


def test_start_executor_strict_startup_raises(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "strict-fail"
    _write_manifest(
        extension_dir,
        "strict-fail",
        extra=_backend("_create_failing_executor_extension"),
    )
    manager = _manager(tmp_path, ["strict-fail"], strict=True)
    registry = _FakeToolRegistry()

    with pytest.raises(RuntimeError, match="executor start exploded"):
        asyncio.run(manager.start_executor(_runtime(registry)))

    assert manager.get_state("strict-fail")["status"] == "degraded"
    assert _EVENTS == ["start_executor", "stop_executor"]
    assert "start" not in _EVENTS
    assert "stop" not in _EVENTS


def test_start_executor_tool_registration_failure_rolls_back(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "tool-extension"
    _write_manifest(
        extension_dir,
        "tool-extension",
        extra=_backend("_create_failing_tool_executor_extension"),
    )
    manager = _manager(tmp_path, ["tool-extension"])
    registry = _FakeToolRegistry()

    asyncio.run(manager.start_executor(_runtime(registry)))

    status = manager.get_state("tool-extension")
    assert status["status"] == "degraded"
    assert status["error"] == "tool registration exploded"
    assert registry.registered == []
    assert registry.unregistered == ["tool-extension"]
    assert _EVENTS == ["start_executor", "stop_executor"]
    assert "start" not in _EVENTS
    assert "stop" not in _EVENTS
    assert manager.is_running("tool-extension") is False


def test_stop_executor_revokes_tools_and_hook_without_stopping_processes(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "with-hook"
    _write_manifest(
        extension_dir,
        "with-hook",
        extra="\n".join([
            _backend("_create_executor_plane_extension"),
            '[resources]\nagents = "resources/agents.json"',
        ]),
    )
    _write_agents(extension_dir, "with-hook.agent")
    manager = _manager(tmp_path, ["with-hook"])
    registry = _FakeToolRegistry()
    store = _agent_store(tmp_path, manager)
    begin_stop_calls: list[str] = []
    stop_calls: list[str] = []
    original_begin_stop = manager.process_manager.begin_stop
    original_stop = manager.process_manager.stop

    def wrapped_begin_stop(owner):
        begin_stop_calls.append(owner)
        return original_begin_stop(owner)

    async def wrapped_stop(owner):
        stop_calls.append(owner)
        return await original_stop(owner)

    manager.process_manager.begin_stop = wrapped_begin_stop
    manager.process_manager.stop = wrapped_stop

    async def exercise() -> None:
        await manager.start_executor(_runtime(registry))
        await manager.process_manager.start(
            "with-hook",
            [
                ProcessSpec(
                    process_id="controller-worker",
                    argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                    startup_timeout_seconds=2,
                    shutdown_timeout_seconds=1,
                )
            ],
        )
        assert manager.process_manager.statuses("with-hook")[0]["status"] == "running"
        pid = manager.process_manager.statuses("with-hook")[0]["pid"]
        assert manager.is_running("with-hook") is True
        assert set(store.load()["agents"]) == {"main", "with-hook.agent"}
        await manager.stop_executor()
        status = manager.process_manager.statuses("with-hook")[0]
        assert status["status"] == "running"
        assert status["pid"] == pid
        await manager.process_manager.stop("with-hook")

    asyncio.run(exercise())

    assert manager.is_running("with-hook") is False
    assert manager.get_state("with-hook")["status"] == "loaded"
    assert set(store.load()["agents"]) == {"main"}
    assert registry.unregistered == ["with-hook"]
    assert registry.registered == []
    assert begin_stop_calls == []
    assert stop_calls == ["with-hook"]
    assert _EVENTS == ["start_executor", "tool", "stop_executor"]
    assert "stop" not in _EVENTS
    assert asyncio.run(
        manager.build_prompt_context(PromptContextRequest(agent_type="main"))
    ) == ""


def test_stop_executor_resource_only_hides_resources_without_stop_hooks(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "no-hook"
    _write_manifest(
        extension_dir,
        "no-hook",
        extra="\n".join([
            _backend("_create_resource_contributing_extension"),
            '[resources]\nagents = "resources/agents.json"',
        ]),
    )
    _write_agents(extension_dir, "no-hook.agent")
    manager = _manager(tmp_path, ["no-hook"])
    store = _agent_store(tmp_path, manager)

    asyncio.run(manager.start_executor(_runtime()))
    assert manager.is_running("no-hook") is True
    asyncio.run(manager.stop_executor())

    assert manager.is_running("no-hook") is False
    assert manager.get_state("no-hook")["status"] == "loaded"
    assert set(store.load()["agents"]) == {"main"}
    assert _EVENTS == []


def test_stop_executor_resets_blocked_dependents_to_loaded(tmp_path: Path):
    base_dir = tmp_path / "extensions" / "base"
    dependent_dir = tmp_path / "extensions" / "dependent"
    _write_manifest(
        base_dir,
        "base",
        extra=_backend("_create_failing_executor_extension"),
    )
    _write_manifest(
        dependent_dir,
        "dependent",
        dependencies=["base"],
        extra=_backend("_create_dependent_executor_extension"),
    )
    manager = _manager(tmp_path, ["dependent"])

    asyncio.run(manager.start_executor(_runtime()))
    assert manager.get_state("dependent")["status"] == "blocked"

    asyncio.run(manager.stop_executor())

    assert manager.get_state("dependent")["status"] == "loaded"
    assert manager.get_state("base")["status"] == "degraded"
    assert _EVENTS == ["start_executor", "stop_executor"]
    assert "dependent:start_executor" not in _EVENTS
    assert "dependent:stop" not in _EVENTS


def test_controller_start_still_runs_existing_plane(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "controller"
    _write_manifest(
        extension_dir,
        "controller",
        extra=_backend("_create_resource_contributing_extension"),
    )
    manager = _manager(tmp_path, ["controller"])
    registry = _FakeToolRegistry()

    asyncio.run(manager.start(_runtime(registry)))

    assert manager.is_running("controller") is True
    assert "controller" in manager._started_extensions
    assert registry.registered == ["resource-tool"]
    assert asyncio.run(
        manager.build_prompt_context(PromptContextRequest(agent_type="main"))
    ) == "executor-prompt"
    asyncio.run(manager.notify_session_end(object()))
    assert "start" in _EVENTS
    assert "health" in _EVENTS
    assert "prompt" in _EVENTS
    assert "session" in _EVENTS
    assert "start_executor" not in _EVENTS
