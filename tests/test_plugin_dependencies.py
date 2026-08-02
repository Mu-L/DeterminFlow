from pathlib import Path
from types import SimpleNamespace

import pytest

from src.extension_api import ExtensionManifest
from src.plugin_system.dependencies import (
    PluginDependencyError,
    install_plugin_requirements,
)


def test_install_plugin_requirements_uses_argv_and_current_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("example==1.0\n", encoding="utf-8")
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "src.plugin_system.dependencies.shutil.which",
        lambda name: "/usr/local/bin/uv",
    )
    monkeypatch.setattr(
        "src.plugin_system.dependencies.subprocess.run",
        run,
    )
    manifest = ExtensionManifest(
        extension_id="demo",
        name="Demo",
        version="1.0.0",
        base_path=tmp_path,
        requirements="requirements.txt",
    )

    install_plugin_requirements(manifest)

    assert observed["command"][0:3] == [
        "/usr/local/bin/uv",
        "pip",
        "install",
    ]
    assert observed["command"][-2:] == ["-r", str(requirements)]
    assert observed["kwargs"]["cwd"] == tmp_path
    assert observed["kwargs"]["check"] is True
    assert observed["kwargs"].get("shell") is not True


def test_install_plugin_requirements_rejects_symlink_escape(tmp_path: Path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    outside = tmp_path / "requirements.txt"
    outside.write_text("example==1.0\n", encoding="utf-8")
    (plugin / "requirements.txt").symlink_to(outside)
    manifest = ExtensionManifest(
        extension_id="demo",
        name="Demo",
        version="1.0.0",
        base_path=plugin,
        requirements="requirements.txt",
    )

    with pytest.raises(PluginDependencyError, match="必须位于 Plugin 目录内"):
        install_plugin_requirements(manifest)
