from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.extension_host.manager import ExtensionManager


def workflow_provision_fixture(tmp_path: Path):
    extension_dir = tmp_path / "extensions" / "workflows"
    extension_dir.mkdir(parents=True)
    (extension_dir / "extension.toml").write_text(
        """[extension]
id = "workflows"
name = "workflows"
version = "1.0.0"
api_version = "1"
dependencies = []
settings_schema = "settings.schema.json"

[resources]
workflows = "resources/workflows"
""",
        encoding="utf-8",
    )
    (extension_dir / "settings.schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "PLUGIN_DB_HOST": {"type": "string"},
            },
        }),
        encoding="utf-8",
    )
    config_dir = tmp_path / "data" / "plugins" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workflows.json").write_text(
        json.dumps({"PLUGIN_DB_HOST": "database.internal"}),
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


def test_workflow_provision_removes_modified_owned_python_cache(tmp_path: Path):
    manager, source_script, target_dir = workflow_provision_fixture(tmp_path)
    source_cache = source_script.parent / "__pycache__" / "run.cpython-313.pyc"
    source_cache.parent.mkdir(parents=True)
    source_cache.write_bytes(b"generated-cache")
    target_cache = target_dir / "script" / "__pycache__" / source_cache.name
    target_cache.parent.mkdir(parents=True)
    target_cache.write_bytes(b"runtime-regenerated-cache")
    marker_path = target_dir / ".extension.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    relative_cache = target_cache.relative_to(target_dir).as_posix()
    marker["files"][relative_cache] = {
        "installed_hash": hashlib.sha256(b"generated-cache").hexdigest(),
    }
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    warnings = manager.provision_workflows(target_dir.parent)

    assert warnings == []
    assert not target_cache.exists()
    refreshed_marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert relative_cache not in refreshed_marker["files"]


def test_workflow_provision_removes_untracked_runtime_python_cache(tmp_path: Path):
    manager, _, target_dir = workflow_provision_fixture(tmp_path)
    target_cache = target_dir / "script" / "__pycache__" / "run.cpython-313.pyc"
    target_cache.parent.mkdir(parents=True)
    target_cache.write_bytes(b"runtime-cache")

    warnings = manager.provision_workflows(target_dir.parent)

    assert warnings == []
    assert not target_cache.exists()


def test_workflow_environment_uses_owner_scoped_plugin_settings(tmp_path: Path):
    manager, _, target_dir = workflow_provision_fixture(tmp_path)
    manager._set_state("workflows", "running")

    assert manager.workflow_environment(target_dir.name) == {
        "PLUGIN_DB_HOST": "database.internal",
    }
    assert manager.workflow_environment("../outside") == {}
