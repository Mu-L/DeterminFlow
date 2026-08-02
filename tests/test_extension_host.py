from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from src.extension_api import CoreRuntime, HealthCheckResult
from src.extension_api.registrar import OwnedPath
from src.extension_host import ExtensionManager, LayeredJsonConfig
from src.tools.registry import ToolRegistry
from src.web_server import create_app


class _InstalledExtension:
    manifest = SimpleNamespace(
        extension_id="installed-demo",
        name="Installed Demo",
        version="1.0.0",
        api_version="1",
        description="",
        dependencies=(),
        backend="",
        frontend="",
        capabilities=(),
        base_path=None,
        resources={},
    )

    def register(self, registrar) -> None:
        return None

    async def start(self, runtime) -> None:
        return None

    async def stop(self) -> None:
        return None


class _UnhealthyExtension:
    def register(self, registrar) -> None:
        registrar.add_health_check(
            lambda runtime: HealthCheckResult(False, "dependency unavailable")
        )

    async def start(self, runtime) -> None:
        return None

    async def stop(self) -> None:
        return None


def _create_unhealthy_extension() -> _UnhealthyExtension:
    return _UnhealthyExtension()


_test_router = APIRouter()


@_test_router.get("/api/test-extension/ping")
async def _test_extension_ping():
    return {"ok": True}


class _MarkerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["x-test-extension"] = "active"
        return response


class _RoutedUnhealthyExtension(_UnhealthyExtension):
    def register(self, registrar) -> None:
        registrar.add_router(_test_router)
        registrar.add_middleware(_MarkerMiddleware)
        super().register(registrar)


def _create_routed_unhealthy_extension() -> _RoutedUnhealthyExtension:
    return _RoutedUnhealthyExtension()


class _BrokenRegistrationExtension:
    def register(self, registrar) -> None:
        registrar.add_router(_test_router)
        raise RuntimeError("register exploded")


def _create_broken_registration_extension() -> _BrokenRegistrationExtension:
    return _BrokenRegistrationExtension()


_lifecycle_events: list[str] = []


class _TrackedUnhealthyExtension:
    def register(self, registrar) -> None:
        registrar.add_health_check(
            lambda runtime: HealthCheckResult(False, "base unavailable")
        )
        registrar.add_tool_contributor(
            lambda registry, runtime: _lifecycle_events.append("base:tool")
        )

    async def start(self, runtime) -> None:
        _lifecycle_events.append("base:start")

    async def stop(self) -> None:
        _lifecycle_events.append("base:stop")


class _TrackedDependentExtension:
    def register(self, registrar) -> None:
        return None

    async def start(self, runtime) -> None:
        _lifecycle_events.append("dependent:start")

    async def stop(self) -> None:
        _lifecycle_events.append("dependent:stop")


class _TrackedProcessExtension:
    def register(self, registrar) -> None:
        return None

    async def start(self, runtime) -> None:
        _lifecycle_events.append("process:start")

    async def stop(self) -> None:
        _lifecycle_events.append("process:stop")


def _create_tracked_process_extension() -> _TrackedProcessExtension:
    return _TrackedProcessExtension()


class _SlowTrackedDependentExtension:
    def register(self, registrar) -> None:
        return None

    async def start(self, runtime) -> None:
        _lifecycle_events.append("slow:start")
        await asyncio.sleep(0.4)
        _lifecycle_events.append("slow:start-done")

    async def stop(self) -> None:
        _lifecycle_events.append("slow:stop")


def _create_slow_tracked_dependent_extension() -> _SlowTrackedDependentExtension:
    return _SlowTrackedDependentExtension()


def _create_tracked_unhealthy_extension() -> _TrackedUnhealthyExtension:
    return _TrackedUnhealthyExtension()


def _create_tracked_dependent_extension() -> _TrackedDependentExtension:
    return _TrackedDependentExtension()


class _FailingToolExtension:
    def register(self, registrar) -> None:
        registrar.add_tool_contributor(self._register_tool)

    @staticmethod
    def _register_tool(registry, runtime) -> None:
        registry.registered.append("tool-extension")
        raise RuntimeError("tool registration exploded")

    async def start(self, runtime) -> None:
        _lifecycle_events.append("tool:start")

    async def stop(self) -> None:
        _lifecycle_events.append("tool:stop")


def _create_failing_tool_extension() -> _FailingToolExtension:
    return _FailingToolExtension()


class _ImplicitOwnerToolExtension:
    def register(self, registrar) -> None:
        registrar.add_tool_contributor(self._register_tool)

    @staticmethod
    def _register_tool(registry, runtime) -> None:
        assert runtime.tool_registry is registry
        registry.register("implicit-owner-tool", "test", {})

    async def start(self, runtime) -> None:
        return None

    async def stop(self) -> None:
        return None


def _create_implicit_owner_tool_extension() -> _ImplicitOwnerToolExtension:
    return _ImplicitOwnerToolExtension()


def _write_manifest(path: Path, extension_id: str, dependencies: list[str] | None = None) -> None:
    path.mkdir(parents=True)
    dependency_values = ", ".join(f'"{item}"' for item in dependencies or [])
    (path / "extension.toml").write_text(
        "\n".join([
            "[extension]",
            f'id = "{extension_id}"',
            f'name = "{extension_id}"',
            'version = "1.0.0"',
            'api_version = "1"',
            f"dependencies = [{dependency_values}]",
        ]),
        encoding="utf-8",
    )


class _FakeToolRegistry:
    def __init__(self):
        self.unregistered: list[str] = []
        self.registered: list[str] = []

    def unregister_owner(self, owner: str) -> None:
        self.unregistered.append(owner)
        if owner == "tool-extension":
            self.registered.clear()


def _runtime(tool_registry=None) -> CoreRuntime:
    return CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=tool_registry or _FakeToolRegistry(),
        event_publisher=None,
    )


def test_extension_dependencies_load_before_dependents(tmp_path: Path):
    _write_manifest(tmp_path / "extensions" / "base", "base")
    _write_manifest(tmp_path / "extensions" / "feature", "feature", ["base"])

    manager = ExtensionManager(
        tmp_path,
        enabled=["feature"],
        discover_entry_points=False,
    )

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["base"]["enabled"] is True
    assert statuses["feature"]["enabled"] is True
    assert manager._load_order == ["base", "feature"]


def test_missing_dependency_blocks_only_dependent_in_non_strict_mode(
    tmp_path: Path,
):
    _write_manifest(
        tmp_path / "extensions" / "feature",
        "feature",
        ["not-installed"],
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["feature"],
        discover_entry_points=False,
    )

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["not-installed"]["status"] == "missing"
    assert statuses["feature"]["status"] == "blocked"
    assert "依赖扩展 not-installed" in statuses["feature"]["error"]


def test_missing_dependency_fails_strict_startup(tmp_path: Path):
    _write_manifest(
        tmp_path / "extensions" / "feature",
        "feature",
        ["not-installed"],
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "extensions.json").write_text(
        json.dumps({"enabled": ["feature"], "strict_startup": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="缺少扩展依赖: not-installed"):
        ExtensionManager(tmp_path, discover_entry_points=False)


def test_extension_dependency_cycle_isolated_in_non_strict_mode(tmp_path: Path):
    _write_manifest(tmp_path / "extensions" / "one", "one", ["two"])
    _write_manifest(tmp_path / "extensions" / "two", "two", ["one"])

    manager = ExtensionManager(
        tmp_path,
        enabled=["one"],
        discover_entry_points=False,
    )

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["one"]["status"] == "degraded"
    assert "依赖形成环" in statuses["one"]["error"]
    assert statuses["two"]["status"] == "blocked"


def test_extension_dependency_cycle_rejected_in_strict_mode(tmp_path: Path):
    _write_manifest(tmp_path / "extensions" / "one", "one", ["two"])
    _write_manifest(tmp_path / "extensions" / "two", "two", ["one"])
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "extensions.json").write_text(
        json.dumps({"enabled": ["one"], "strict_startup": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="依赖形成环"):
        ExtensionManager(tmp_path, discover_entry_points=False)


def test_broken_disabled_manifest_is_isolated(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "broken_extension"
    extension_dir.mkdir(parents=True)
    (extension_dir / "extension.toml").write_text(
        "[extension\nid = [",
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=[],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["id"] == "broken-extension"
    assert status["enabled"] is False
    assert status["status"] == "degraded"
    assert "扩展清单无效" in status["error"]


def test_strict_startup_rejects_enabled_broken_manifest(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "extensions.json").write_text(
        json.dumps({"enabled": ["broken"], "strict_startup": True}),
        encoding="utf-8",
    )
    extension_dir = tmp_path / "extensions" / "broken"
    extension_dir.mkdir(parents=True)
    (extension_dir / "extension.toml").write_text("not valid toml = [", encoding="utf-8")

    with pytest.raises(ValueError, match="扩展清单无效"):
        ExtensionManager(tmp_path, discover_entry_points=False)


def test_unknown_manifest_resource_type_is_degraded(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "unknown-resource"
    _write_manifest(extension_dir, "unknown-resource")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nworkflow = "resources/workflows"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["unknown-resource"],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "未知资源类型: workflow" in status["error"]


def test_manifest_resource_cannot_escape_extension_directory(tmp_path: Path):
    outside_resource = tmp_path / "outside-agents.json"
    outside_resource.write_text('{"agents": {}}', encoding="utf-8")
    extension_dir = tmp_path / "extensions" / "escaping"
    _write_manifest(extension_dir, "escaping")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nagents = "../../outside-agents.json"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["escaping"],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "资源必须位于扩展目录内" in status["error"]


def test_layered_config_writes_extension_changes_to_override(tmp_path: Path):
    base_file = tmp_path / "config" / "agents.json"
    base_file.parent.mkdir(parents=True)
    base_file.write_text(json.dumps({"agents": {"main": {"model": "core"}}}), encoding="utf-8")
    extension_file = tmp_path / "extension-agents.json"
    extension_file.write_text(
        json.dumps({"agents": {"novel.writer": {"model": "extension"}}}),
        encoding="utf-8",
    )
    store = LayeredJsonConfig(
        base_file,
        [OwnedPath("novel", extension_file)],
        dict_sections=["agents"],
    )

    resolved = store.load()
    resolved["agents"]["novel.writer"]["model"] = "custom"
    store.save(resolved)

    persisted_base = json.loads(base_file.read_text(encoding="utf-8"))
    assert set(persisted_base["agents"]) == {"main"}
    assert store.load()["agents"]["novel.writer"]["model"] == "custom"


def test_layered_config_ignores_cross_owner_and_stale_overrides(tmp_path: Path):
    base_file = tmp_path / "config" / "agents.json"
    base_file.parent.mkdir(parents=True)
    base_file.write_text(
        json.dumps({"agents": {"main": {"model": "core"}}}),
        encoding="utf-8",
    )
    novel_file = tmp_path / "novel-agents.json"
    novel_file.write_text(
        json.dumps({"agents": {"novel.writer": {"model": "novel"}}}),
        encoding="utf-8",
    )
    other_file = tmp_path / "other-agents.json"
    other_file.write_text(
        json.dumps({"agents": {"other.editor": {"model": "other"}}}),
        encoding="utf-8",
    )
    override_file = tmp_path / "config" / "extension-overrides" / "agents.json"
    override_file.parent.mkdir(parents=True)
    override_file.write_text(
        json.dumps({
            "extensions": {
                "novel": {
                    "values": {
                        "agents": {
                            "main": {"model": "hijacked"},
                            "novel.writer": {"model": "custom"},
                            "other.editor": {"model": "hijacked"},
                            "novel.removed": {"model": "stale"},
                        }
                    },
                    "deleted": {"agents": ["main", "other.editor", "novel.removed"]},
                }
            }
        }),
        encoding="utf-8",
    )
    store = LayeredJsonConfig(
        base_file,
        [OwnedPath("novel", novel_file), OwnedPath("other", other_file)],
        dict_sections=["agents"],
        override_file=override_file,
    )

    resolved = store.load()

    assert resolved["agents"] == {
        "main": {"model": "core"},
        "novel.writer": {"model": "custom"},
        "other.editor": {"model": "other"},
    }

    store.save(resolved)
    persisted = json.loads(override_file.read_text(encoding="utf-8"))
    assert persisted == {
        "extensions": {
            "novel": {
                "values": {"agents": {"novel.writer": {"model": "custom"}}},
                "deleted": {},
            }
        }
    }


def test_inactive_extension_override_survives_core_save_and_reactivation(
    tmp_path: Path,
):
    active = {"novel": False}
    base_file = tmp_path / "config" / "agents.json"
    base_file.parent.mkdir(parents=True)
    base_file.write_text(
        json.dumps({"agents": {"main": {"model": "core"}}}),
        encoding="utf-8",
    )
    extension_file = tmp_path / "novel-agents.json"
    extension_file.write_text(
        json.dumps({"agents": {"novel.writer": {"model": "default"}}}),
        encoding="utf-8",
    )
    override_file = tmp_path / "config" / "extension-overrides" / "agents.json"
    override_file.parent.mkdir(parents=True)
    expected_override = {
        "extensions": {
            "novel": {
                "values": {"agents": {"novel.writer": {"model": "custom"}}},
                "deleted": {},
            }
        }
    }
    override_file.write_text(json.dumps(expected_override), encoding="utf-8")
    store = LayeredJsonConfig(
        base_file,
        [OwnedPath("novel", extension_file)],
        dict_sections=["agents"],
        override_file=override_file,
        owner_enabled=lambda owner: active[owner],
    )

    inactive_config = store.load()
    inactive_config["agents"]["main"]["model"] = "core-updated"
    store.save(inactive_config)

    assert json.loads(override_file.read_text(encoding="utf-8")) == expected_override
    active["novel"] = True
    assert store.load()["agents"] == {
        "main": {"model": "core-updated"},
        "novel.writer": {"model": "custom"},
    }


def test_core_app_has_no_product_extension_routes(tmp_path: Path):
    manager = ExtensionManager(tmp_path, enabled=[], discover_entry_points=False)
    paths = create_app(manager).openapi()["paths"]

    assert "/api/extensions" in paths
    assert not any(path.startswith("/api/product-extension/") for path in paths)


def test_failed_health_check_marks_extension_degraded(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "unhealthy"
    _write_manifest(extension_dir, "unhealthy")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_unhealthy_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["unhealthy"],
        discover_entry_points=False,
    )
    asyncio.run(manager.start(_runtime()))

    status = next(item for item in manager.get_statuses() if item["id"] == "unhealthy")
    assert status["status"] == "degraded"
    assert status["error"] == "dependency unavailable"


def test_start_failure_cleans_up_and_blocks_dependents(tmp_path: Path):
    _lifecycle_events.clear()
    base_dir = tmp_path / "extensions" / "base"
    dependent_dir = tmp_path / "extensions" / "dependent"
    _write_manifest(base_dir, "base")
    _write_manifest(dependent_dir, "dependent", ["base"])
    (base_dir / "extension.toml").write_text(
        (base_dir / "extension.toml").read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_tracked_unhealthy_extension"\n',
        encoding="utf-8",
    )
    (dependent_dir / "extension.toml").write_text(
        (dependent_dir / "extension.toml").read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_tracked_dependent_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["dependent"],
        discover_entry_points=False,
    )
    registry = _FakeToolRegistry()

    asyncio.run(manager.start(_runtime(registry)))

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["base"]["status"] == "degraded"
    assert statuses["dependent"]["status"] == "blocked"
    assert "依赖扩展 base" in statuses["dependent"]["error"]
    assert _lifecycle_events == ["base:start", "base:stop"]
    assert registry.unregistered == ["base"]


def test_runtime_process_failure_stops_and_blocks_dependents(tmp_path: Path):
    _lifecycle_events.clear()
    process_dir = tmp_path / "extensions" / "process"
    dependent_dir = tmp_path / "extensions" / "dependent"
    _write_manifest(process_dir, "process")
    _write_manifest(dependent_dir, "dependent", ["process"])
    (process_dir / "extension.toml").write_text(
        (process_dir / "extension.toml").read_text(encoding="utf-8")
        + f"""
backend = "{__name__}:_create_tracked_process_extension"

[[processes]]
id = "worker"
command = ["${{PYTHON}}", "-c", "import time; time.sleep(0.4)"]
""",
        encoding="utf-8",
    )
    (dependent_dir / "extension.toml").write_text(
        (dependent_dir / "extension.toml").read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_tracked_dependent_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["dependent"],
        discover_entry_points=False,
    )
    registry = _FakeToolRegistry()

    async def exercise() -> None:
        await manager.start(_runtime(registry))
        assert manager.get_state("process")["status"] == "running"
        assert manager.get_state("dependent")["status"] == "running"
        await asyncio.sleep(0.5)
        assert manager.get_state("process")["status"] == "degraded"
        dependent = manager.get_state("dependent")
        assert dependent["status"] == "blocked"
        assert "依赖扩展 process 运行时失败" in dependent["error"]
        assert _lifecycle_events == [
            "process:start",
            "dependent:start",
            "dependent:stop",
            "process:stop",
        ]
        assert registry.unregistered == ["dependent", "process"]
        await manager.stop()

    asyncio.run(exercise())


def test_runtime_failure_blocks_dependent_during_async_start(tmp_path: Path):
    _lifecycle_events.clear()
    process_dir = tmp_path / "extensions" / "process"
    dependent_dir = tmp_path / "extensions" / "slow"
    _write_manifest(process_dir, "process")
    _write_manifest(dependent_dir, "slow", ["process"])
    (process_dir / "extension.toml").write_text(
        (process_dir / "extension.toml").read_text(encoding="utf-8")
        + f"""
backend = "{__name__}:_create_tracked_process_extension"

[[processes]]
id = "worker"
command = ["${{PYTHON}}", "-c", "import time; time.sleep(0.15)"]
""",
        encoding="utf-8",
    )
    (dependent_dir / "extension.toml").write_text(
        (dependent_dir / "extension.toml").read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_slow_tracked_dependent_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["slow"],
        discover_entry_points=False,
    )

    asyncio.run(manager.start(_runtime()))

    assert manager.get_state("process")["status"] == "degraded"
    dependent = manager.get_state("slow")
    assert dependent["status"] == "blocked"
    assert "依赖扩展 process 运行时失败" in dependent["error"]
    assert _lifecycle_events == [
        "process:start",
        "slow:start",
        "process:stop",
        "slow:start-done",
        "slow:stop",
    ]
    assert "slow" not in manager._started_extensions


def test_tool_registration_failure_rolls_back_and_stops_extension(tmp_path: Path):
    _lifecycle_events.clear()
    extension_dir = tmp_path / "extensions" / "tool-extension"
    _write_manifest(extension_dir, "tool-extension")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_failing_tool_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["tool-extension"],
        discover_entry_points=False,
    )
    registry = _FakeToolRegistry()

    asyncio.run(manager.start(_runtime(registry)))

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert status["error"] == "tool registration exploded"
    assert registry.registered == []
    assert registry.unregistered == ["tool-extension"]
    assert _lifecycle_events == ["tool:start", "tool:stop"]


def test_extension_tool_registry_forces_owner_and_unloads_cleanly(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "implicit-owner"
    _write_manifest(extension_dir, "implicit-owner")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_implicit_owner_tool_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["implicit-owner"],
        discover_entry_points=False,
    )
    registry = ToolRegistry(str(tmp_path / "missing-tool-groups.json"))

    asyncio.run(manager.start(_runtime(registry)))

    tool = next(item for item in registry.get_tools() if item["name"] == "implicit-owner-tool")
    assert tool["owner"] == "implicit-owner"

    asyncio.run(manager.stop())
    assert not any(item["name"] == "implicit-owner-tool" for item in registry.get_tools())


def test_non_strict_registration_failure_discards_partial_contributions(
    tmp_path: Path,
):
    broken_dir = tmp_path / "extensions" / "broken"
    dependent_dir = tmp_path / "extensions" / "dependent"
    _write_manifest(broken_dir, "broken")
    _write_manifest(dependent_dir, "dependent", ["broken"])
    (broken_dir / "extension.toml").write_text(
        (broken_dir / "extension.toml").read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_broken_registration_extension"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["dependent"],
        discover_entry_points=False,
    )

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["broken"]["status"] == "degraded"
    assert "register exploded" in statuses["broken"]["error"]
    assert statuses["dependent"]["status"] == "blocked"
    assert manager.routers == []


def test_non_strict_missing_resource_marks_extension_degraded(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "missing-resource"
    _write_manifest(extension_dir, "missing-resource")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nworkflows = "resources/missing"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["missing-resource"],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "资源不存在" in status["error"]
    assert manager.resource_paths("workflows") == []


def test_non_strict_malformed_json_resource_marks_extension_degraded(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "malformed"
    _write_manifest(extension_dir, "malformed")
    resources_dir = extension_dir / "resources"
    resources_dir.mkdir()
    (resources_dir / "agents.json").write_text("{invalid", encoding="utf-8")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nagents = "resources/agents.json"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["malformed"],
        discover_entry_points=False,
    )
    asyncio.run(manager.start(_runtime()))

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "扩展注册失败" in status["error"]
    assert manager.resource_paths("agents") == []


def test_non_strict_invalid_workflow_resource_marks_extension_degraded(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "invalid-workflow"
    _write_manifest(extension_dir, "invalid-workflow")
    workflow_dir = extension_dir / "resources" / "workflows" / "wf-demo"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "definition.json").write_text(
        json.dumps({"workflow_id": "wrong-id", "name": "Demo"}),
        encoding="utf-8",
    )
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nworkflows = "resources/workflows"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        enabled=["invalid-workflow"],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "Workflow definition ID 必须与目录名一致" in status["error"]
    assert manager.resource_paths("workflows") == []


def test_non_strict_cross_extension_resource_conflict_degrades_later_owner(
    tmp_path: Path,
):
    for extension_id in ("first", "second"):
        extension_dir = tmp_path / "extensions" / extension_id
        _write_manifest(extension_dir, extension_id)
        resources_dir = extension_dir / "resources"
        resources_dir.mkdir()
        (resources_dir / "agents.json").write_text(
            json.dumps({"agents": {"shared.agent": {"model": extension_id}}}),
            encoding="utf-8",
        )
        manifest = extension_dir / "extension.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[resources]\nagents = "resources/agents.json"\n',
            encoding="utf-8",
        )

    manager = ExtensionManager(
        tmp_path,
        enabled=["first", "second"],
        discover_entry_points=False,
    )
    asyncio.run(manager.start(_runtime()))

    statuses = {item["id"]: item for item in manager.get_statuses()}
    assert statuses["first"]["status"] == "running"
    assert statuses["second"]["status"] == "degraded"
    assert "扩展资源冲突: agents.shared.agent" in statuses["second"]["error"]
    assert [path.owner for path in manager.resource_paths("agents")] == ["first"]


def test_resource_validation_uses_redirected_config_directory(tmp_path: Path):
    custom_config = tmp_path / "instance-config"
    custom_config.mkdir()
    (custom_config / "agents_config.json").write_text(
        json.dumps({"agents": {"shared.agent": {"model": "core"}}}),
        encoding="utf-8",
    )
    extension_dir = tmp_path / "extensions" / "redirected"
    _write_manifest(extension_dir, "redirected")
    resources_dir = extension_dir / "resources"
    resources_dir.mkdir()
    (resources_dir / "agents.json").write_text(
        json.dumps({"agents": {"shared.agent": {"model": "extension"}}}),
        encoding="utf-8",
    )
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nagents = "resources/agents.json"\n',
        encoding="utf-8",
    )

    manager = ExtensionManager(
        tmp_path,
        config_file=custom_config / "extensions.json",
        enabled=["redirected"],
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["status"] == "degraded"
    assert "扩展资源冲突: agents.shared.agent" in status["error"]


def test_strict_startup_fails_fast_for_malformed_resource(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "extensions.json").write_text(
        json.dumps({"enabled": ["malformed"], "strict_startup": True}),
        encoding="utf-8",
    )
    extension_dir = tmp_path / "extensions" / "malformed"
    _write_manifest(extension_dir, "malformed")
    resources_dir = extension_dir / "resources"
    resources_dir.mkdir()
    (resources_dir / "agents.json").write_text("{invalid", encoding="utf-8")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nagents = "resources/agents.json"\n',
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        ExtensionManager(tmp_path, discover_entry_points=False)
    snapshot_root = tmp_path / "data" / "plugins" / "runtime-config"
    assert not snapshot_root.exists() or not any(snapshot_root.iterdir())


def test_degraded_extension_route_is_gated_with_503(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "unhealthy"
    _write_manifest(extension_dir, "unhealthy")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'\nbackend = "{__name__}:_create_routed_unhealthy_extension"\n',
        encoding="utf-8",
    )
    manager = ExtensionManager(
        tmp_path,
        enabled=["unhealthy"],
        discover_entry_points=False,
    )
    asyncio.run(manager.start(_runtime()))
    client = TestClient(create_app(manager))

    response = client.get("/api/test-extension/ping")

    assert response.status_code == 503
    assert "x-test-extension" not in response.headers
    assert response.json()["detail"] == {
        "code": "extension_unavailable",
        "extension_id": "unhealthy",
        "status": "degraded",
        "error": "dependency unavailable",
    }


def test_layered_resources_activate_only_for_running_extensions(tmp_path: Path):
    base_file = tmp_path / "config" / "agents.json"
    base_file.parent.mkdir(parents=True)
    base_file.write_text(
        json.dumps({"agents": {"main": {"model": "core"}}}),
        encoding="utf-8",
    )
    running_dir = tmp_path / "extensions" / "running"
    unhealthy_dir = tmp_path / "extensions" / "unhealthy"
    _write_manifest(running_dir, "running")
    _write_manifest(unhealthy_dir, "unhealthy")
    for extension_dir, agent_id in (
        (running_dir, "running.agent"),
        (unhealthy_dir, "unhealthy.agent"),
    ):
        resources_dir = extension_dir / "resources"
        resources_dir.mkdir()
        (resources_dir / "agents.json").write_text(
            json.dumps({"agents": {agent_id: {"model": agent_id}}}),
            encoding="utf-8",
        )
        manifest = extension_dir / "extension.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[resources]\nagents = "resources/agents.json"\n',
            encoding="utf-8",
        )
    unhealthy_manifest = unhealthy_dir / "extension.toml"
    # backend belongs in the extension table, before the resources table.
    raw_manifest = unhealthy_manifest.read_text(encoding="utf-8")
    raw_manifest = raw_manifest.replace(
        '\n[resources]',
        f'\nbackend = "{__name__}:_create_unhealthy_extension"\n\n[resources]',
        1,
    )
    unhealthy_manifest.write_text(raw_manifest, encoding="utf-8")
    manager = ExtensionManager(
        tmp_path,
        enabled=["running", "unhealthy"],
        discover_entry_points=False,
    )
    store = LayeredJsonConfig(
        base_file,
        manager.resource_paths("agents"),
        dict_sections=["agents"],
        owner_enabled=manager.is_running,
    )

    assert set(store.load()["agents"]) == {"main"}

    asyncio.run(manager.start(_runtime()))

    assert manager.is_running("running") is True
    assert manager.is_running("unhealthy") is False
    assert set(store.load()["agents"]) == {"main", "running.agent"}


def test_workflow_provision_updates_defaults_without_overwriting_user_changes(
    tmp_path: Path,
):
    extension_dir = tmp_path / "extensions" / "workflows"
    _write_manifest(extension_dir, "workflows")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nworkflows = "resources/workflows"\n',
        encoding="utf-8",
    )
    source_dir = extension_dir / "resources" / "workflows" / "wf-demo"
    script_dir = source_dir / "script"
    script_dir.mkdir(parents=True)
    definition = source_dir / "definition.json"
    script = script_dir / "run.py"
    definition.write_text(
        '{"workflow_id": "wf-demo", "name": "Demo", "version": 1}',
        encoding="utf-8",
    )
    script.write_text("version = 1\n", encoding="utf-8")
    manager = ExtensionManager(
        tmp_path,
        enabled=["workflows"],
        discover_entry_points=False,
    )
    target_root = tmp_path / "data" / "workflows"

    manager.provision_workflows(target_root)
    target_definition = target_root / "wf-demo" / "definition.json"
    target_script = target_root / "wf-demo" / "script" / "run.py"
    target_script.write_text("user_change = True\n", encoding="utf-8")
    definition.write_text(
        '{"workflow_id": "wf-demo", "name": "Demo", "version": 2}',
        encoding="utf-8",
    )
    script.write_text("version = 2\n", encoding="utf-8")

    manager.provision_workflows(target_root)

    assert json.loads(target_definition.read_text(encoding="utf-8"))["version"] == 2
    assert target_script.read_text(encoding="utf-8") == "user_change = True\n"


def _workflow_provision_fixture(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "workflows"
    _write_manifest(extension_dir, "workflows")
    manifest = extension_dir / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[resources]\nworkflows = "resources/workflows"\n',
        encoding="utf-8",
    )
    source_dir = extension_dir / "resources" / "workflows" / "wf-demo"
    script_dir = source_dir / "script"
    script_dir.mkdir(parents=True)
    (source_dir / "definition.json").write_text(
        '{"workflow_id": "wf-demo", "name": "Demo", "version": 1}',
        encoding="utf-8",
    )
    source_script = script_dir / "run.py"
    source_script.write_text("version = 1\n", encoding="utf-8")
    manager = ExtensionManager(
        tmp_path,
        enabled=["workflows"],
        discover_entry_points=False,
    )
    target_root = tmp_path / "data" / "workflows"
    manager.provision_workflows(target_root)
    return manager, source_script, target_root / "wf-demo"


def test_workflow_provision_removes_unchanged_deleted_extension_file(
    tmp_path: Path,
):
    manager, source_script, target_dir = _workflow_provision_fixture(tmp_path)
    target_script = target_dir / "script" / "run.py"
    source_script.unlink()

    warnings = manager.provision_workflows(target_dir.parent)

    assert warnings == []
    assert target_script.exists() is False
    marker = json.loads((target_dir / ".extension.json").read_text(encoding="utf-8"))
    assert "script/run.py" not in marker["files"]
    assert "orphaned_files" not in marker


def test_workflow_provision_preserves_and_reports_modified_deleted_file(
    tmp_path: Path,
):
    manager, source_script, target_dir = _workflow_provision_fixture(tmp_path)
    target_script = target_dir / "script" / "run.py"
    target_script.write_text("user_change = True\n", encoding="utf-8")
    source_script.unlink()

    warnings = manager.provision_workflows(target_dir.parent)

    assert warnings == [
        "扩展 Workflow wf-demo 已移除文件 script/run.py，检测到用户修改，已保留"
    ]
    assert target_script.read_text(encoding="utf-8") == "user_change = True\n"
    marker = json.loads((target_dir / ".extension.json").read_text(encoding="utf-8"))
    assert marker["orphaned_files"]["script/run.py"]["reason"] == "user_modified"


def test_workflow_removed_by_plugin_update_is_marked_inactive(tmp_path: Path):
    manager, source_script, target_dir = _workflow_provision_fixture(tmp_path)
    source_dir = source_script.parents[1]
    source_script.unlink()
    source_script.parent.rmdir()
    (source_dir / "definition.json").unlink()
    source_dir.rmdir()

    manager.provision_workflows(target_dir.parent)

    marker = json.loads(
        (target_dir / ".extension.json").read_text(encoding="utf-8")
    )
    assert marker["owner"] == "workflows"
    assert marker["active"] is False
    assert manager.workflow_owner_enabled(target_dir) is False


def test_removed_workflow_resource_declaration_deactivates_old_marker(
    tmp_path: Path,
):
    _manager, _source_script, target_dir = _workflow_provision_fixture(tmp_path)
    manifest_path = tmp_path / "extensions" / "workflows" / "extension.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").split(
            "\n[resources]",
            maxsplit=1,
        )[0],
        encoding="utf-8",
    )
    updated_manager = ExtensionManager(
        tmp_path,
        enabled=["workflows"],
        discover_entry_points=False,
    )

    updated_manager.provision_workflows(target_dir.parent)

    marker = json.loads(
        (target_dir / ".extension.json").read_text(encoding="utf-8")
    )
    assert marker["active"] is False


def test_disabled_entry_point_is_not_imported(tmp_path: Path, monkeypatch):
    loads = []
    groups = []

    class FakeEntryPoint:
        name = "installed-demo"
        dist = SimpleNamespace(version="1.0.0")

        def load(self):
            loads.append(self.name)
            return _InstalledExtension

    def fake_entry_points(**kwargs):
        groups.append(kwargs["group"])
        return [FakeEntryPoint()]

    monkeypatch.setattr(
        "src.extension_host.manager.metadata.entry_points",
        fake_entry_points,
    )

    ExtensionManager(tmp_path, enabled=[], discover_entry_points=True)

    assert loads == []

    enabled_manager = ExtensionManager(
        tmp_path,
        enabled=["installed-demo"],
        discover_entry_points=True,
    )

    assert loads == ["installed-demo"]
    assert enabled_manager.is_enabled("installed-demo")
    assert groups == [
        "determinflow.extensions",
        "ai_company.extensions",
        "determinflow.extensions",
        "ai_company.extensions",
    ]
