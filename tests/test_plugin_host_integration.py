from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from src.extension_api import CoreRuntime
from src.extension_host.manager import ExtensionManager
from src.plugin_system import PluginStore, PluginStoreError


_RUNTIME_EVENTS: list[object] = []


class _ConfigAwareExtension:
    def register(self, registrar) -> None:
        return None

    async def start(self, runtime: CoreRuntime) -> None:
        _RUNTIME_EVENTS.extend([
            "start",
            runtime.get_service("plugin_config"),
            runtime.get_service("plugin_config_file"),
            runtime.get_service("plugin_data_dir"),
            runtime.get_service("resource_resolver"),
        ])

    async def stop(self) -> None:
        _RUNTIME_EVENTS.append("stop")


def _create_config_aware_extension() -> _ConfigAwareExtension:
    return _ConfigAwareExtension()


class _ProcessStoppingExtension:
    def __init__(self) -> None:
        self.stop_file: Path | None = None

    def register(self, registrar) -> None:
        return None

    async def start(self, runtime: CoreRuntime) -> None:
        self.stop_file = runtime.get_service("plugin_data_dir") / "stop"
        _RUNTIME_EVENTS.append("process-stop:start")

    async def stop(self) -> None:
        assert self.stop_file is not None
        self.stop_file.touch()
        await asyncio.sleep(0.1)
        _RUNTIME_EVENTS.append("process-stop:stop")


def _create_process_stopping_extension() -> _ProcessStoppingExtension:
    return _ProcessStoppingExtension()


class _ToolRegistry:
    def __init__(self) -> None:
        self.unregistered: list[str] = []

    def unregister_owner(self, owner: str) -> None:
        self.unregistered.append(owner)


class _Reloadable:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def reload(self) -> bool | None:
        self.calls += 1
        if self.fail:
            return False
        return None


def _runtime(services: dict | None = None) -> CoreRuntime:
    return CoreRuntime(
        app=object(),
        session_manager=object(),
        workflow_runtime=object(),
        tool_registry=_ToolRegistry(),
        event_publisher=None,
        services=services or {},
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _create_plugin_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Plugin Test")
    _git(repo, "config", "user.email", "plugin-test@example.invalid")
    (repo / "extension.toml").write_text(
        f"""
[extension]
id = "demo-plugin"
name = "Demo Plugin"
version = "1.0.0"
api_version = "1"
backend = "{__name__}:_create_config_aware_extension"

[settings]
schema = "settings.schema.json"

[[processes]]
id = "worker"
command = ["${{PYTHON}}", "-c", "import time; time.sleep(30)"]
working_directory = "."
start_timeout_seconds = 2
stop_timeout_seconds = 2
""",
        encoding="utf-8",
    )
    (repo / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
        }),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _write_enabled(base_dir: Path, enabled: list[str], *, strict: bool = False) -> Path:
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "extensions.json"
    config_file.write_text(
        json.dumps({"enabled": enabled, "strict_startup": strict}),
        encoding="utf-8",
    )
    return config_file


def test_external_plugin_is_loaded_from_locked_checkout_with_process_and_config(
    tmp_path: Path,
):
    _RUNTIME_EVENTS.clear()
    repo = _create_plugin_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo), ref="main")
    config_root = store.root / "config"
    config_root.mkdir()
    (config_root / "demo-plugin.json").write_text(
        '{"message":"from config"}',
        encoding="utf-8",
    )
    base_dir = tmp_path / "core"
    config_file = _write_enabled(base_dir, ["demo-plugin"])

    manager = ExtensionManager(
        base_dir,
        config_file=config_file,
        plugin_store=store,
        discover_entry_points=False,
    )

    before = manager.get_statuses()[0]
    assert before["status"] == "loaded"
    assert before["enabled"] is True
    assert before["version"] == "1.0.0"
    assert before["processes"] == []
    assert store.get("demo-plugin").pending_action is None
    applied_config = manager.applied_plugin_config_store.path_for("demo-plugin")
    desired_config = manager.plugin_config_store.path_for("demo-plugin")
    manager.plugin_config_store.save(
        "demo-plugin",
        json.loads((repo / "settings.schema.json").read_text(encoding="utf-8")),
        {"message": "after restart"},
    )
    assert json.loads(applied_config.read_text(encoding="utf-8")) == {
        "message": "from config"
    }
    assert json.loads(desired_config.read_text(encoding="utf-8")) == {
        "message": "after restart"
    }

    async def run_lifecycle() -> None:
        await manager.start(_runtime())
        running = manager.get_statuses()[0]
        assert running["status"] == "running"
        assert running["processes"][0]["status"] == "running"
        assert _RUNTIME_EVENTS[0:2] == ["start", {"message": "from config"}]
        assert _RUNTIME_EVENTS[2] == applied_config
        assert _RUNTIME_EVENTS[3] == store.root / "data" / "demo-plugin"
        assert _RUNTIME_EVENTS[4] is None
        await manager.stop()

    asyncio.run(run_lifecycle())

    assert _RUNTIME_EVENTS[-1] == "stop"
    assert applied_config.exists() is False
    assert manager.process_manager.statuses("demo-plugin")[0]["status"] == "stopped"


def test_extension_driven_process_exit_is_normal_shutdown(tmp_path: Path):
    _RUNTIME_EVENTS.clear()
    repo = _create_plugin_repo(tmp_path)
    manifest = repo / "extension.toml"
    process_code = (
        "import pathlib,time;"
        "p=pathlib.Path('${DATA_DIR}/stop');"
        "exec('while not p.exists():\\\\n time.sleep(0.01)')"
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        .replace(
            f"{__name__}:_create_config_aware_extension",
            f"{__name__}:_create_process_stopping_extension",
        )
        .replace("import time; time.sleep(30)", process_code),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "extension stops worker")
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    base_dir = tmp_path / "core"
    manager = ExtensionManager(
        base_dir,
        config_file=_write_enabled(base_dir, ["demo-plugin"]),
        plugin_store=store,
        discover_entry_points=False,
    )

    async def exercise() -> None:
        await manager.start(_runtime())
        assert manager.get_state("demo-plugin")["status"] == "running"
        await manager.stop()

    asyncio.run(exercise())

    assert _RUNTIME_EVENTS == ["process-stop:start", "process-stop:stop"]
    assert manager.get_state("demo-plugin")["status"] == "loaded"
    process = manager.process_manager.statuses("demo-plugin")[0]
    assert process["status"] == "stopped"
    assert process["error"] == ""


def test_non_strict_missing_desired_plugin_is_reported_without_crashing(
    tmp_path: Path,
):
    config_file = _write_enabled(tmp_path, ["not-installed"])

    manager = ExtensionManager(
        tmp_path,
        config_file=config_file,
        discover_entry_points=False,
    )

    status = manager.get_statuses()[0]
    assert status["id"] == "not-installed"
    assert status["enabled"] is False
    assert status["status"] == "missing"
    assert "尚未安装" in status["error"]


def test_strict_startup_rejects_missing_desired_plugin(tmp_path: Path):
    config_file = _write_enabled(tmp_path, ["not-installed"], strict=True)

    with pytest.raises(ValueError, match="未知扩展"):
        ExtensionManager(
            tmp_path,
            config_file=config_file,
            discover_entry_points=False,
        )


def test_locked_plugin_content_tampering_is_rejected_at_cold_start(
    tmp_path: Path,
):
    repo = _create_plugin_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    record = store.install("demo-plugin", str(repo))
    checkout = Path(record.active_revision.checkout_path)
    (checkout / "settings.schema.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PluginStoreError, match="content hash mismatch"):
        ExtensionManager(
            tmp_path / "core",
            plugin_store=store,
            enabled=[],
            discover_entry_points=False,
        )


def test_managed_process_exit_degrades_running_plugin(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    _RUNTIME_EVENTS.clear()
    repo = _create_plugin_repo(tmp_path)
    manifest = repo / "extension.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "time.sleep(30)",
            "time.sleep(0.15)",
        ),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "short lived worker")
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    base_dir = tmp_path / "core"
    manager = ExtensionManager(
        base_dir,
        config_file=_write_enabled(base_dir, ["demo-plugin"]),
        plugin_store=store,
        discover_entry_points=False,
    )

    async def run_lifecycle() -> None:
        reloadables = {
            "agent_config_manager": _Reloadable(),
            "prompt_manager": _Reloadable(fail=True),
            "skill_manager": _Reloadable(),
            "rule_manager": _Reloadable(),
        }
        runtime = _runtime(reloadables)
        await manager.start(runtime)
        assert manager.is_running("demo-plugin") is True
        await asyncio.sleep(0.25)
        state = manager.get_state("demo-plugin")
        assert state["status"] == "degraded"
        assert "异常退出" in state["error"]
        assert _RUNTIME_EVENTS[-1] == "stop"
        assert runtime.tool_registry.unregistered == ["demo-plugin"]
        assert [item.calls for item in reloadables.values()] == [1, 1, 1, 1]
        assert "扩展资源缓存刷新失败: prompt_manager" in caplog.text
        status = manager.get_statuses()[0]
        assert status["status"] == "degraded"
        assert status["processes"][0]["status"] == "exited"
        assert "unexpectedly" in status["processes"][0]["error"]
        assert manager.is_running("demo-plugin") is False
        await manager.stop()

    asyncio.run(run_lifecycle())


def test_invalid_plugin_config_degrades_only_that_plugin(tmp_path: Path):
    repo = _create_plugin_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    config_root = store.root / "config"
    config_root.mkdir()
    (config_root / "demo-plugin.json").write_text(
        '{"message": 42}',
        encoding="utf-8",
    )
    config_file = _write_enabled(tmp_path / "core", ["demo-plugin"])

    manager = ExtensionManager(
        tmp_path / "core",
        config_file=config_file,
        plugin_store=store,
        discover_entry_points=False,
    )

    state = manager.get_state("demo-plugin")
    assert state["status"] == "degraded"
    assert "Plugin 配置无效" in state["error"]


def test_invalid_settings_schema_degrades_only_that_plugin(tmp_path: Path):
    repo = _create_plugin_repo(tmp_path)
    (repo / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "minimum": "invalid",
                    "default": 1,
                },
            },
        }),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "invalid schema")
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    config_file = _write_enabled(tmp_path / "core", ["demo-plugin"])

    manager = ExtensionManager(
        tmp_path / "core",
        config_file=config_file,
        plugin_store=store,
        discover_entry_points=False,
    )

    state = manager.get_state("demo-plugin")
    assert state["status"] == "degraded"
    assert "minimum/maximum 必须是数字" in state["error"]


def test_strict_startup_rejects_invalid_plugin_config(tmp_path: Path):
    repo = _create_plugin_repo(tmp_path)
    store = PluginStore(
        tmp_path / "runtime" / "plugins",
        official_sources=[str(repo)],
    )
    store.install("demo-plugin", str(repo))
    config_root = store.root / "config"
    config_root.mkdir()
    (config_root / "demo-plugin.json").write_text(
        '{"message": 42}',
        encoding="utf-8",
    )
    config_file = _write_enabled(
        tmp_path / "core",
        ["demo-plugin"],
        strict=True,
    )

    with pytest.raises(ValueError, match="message.*string"):
        ExtensionManager(
            tmp_path / "core",
            config_file=config_file,
            plugin_store=store,
            discover_entry_points=False,
        )
