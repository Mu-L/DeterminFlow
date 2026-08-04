from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from desktop.python.entrypoint import _run_python_compatibility_mode
from desktop.python.runtime import seed_user_config
from desktop.scripts import stage_defaults as defaults_module
from desktop.scripts.verify_bundle import verify_defaults, write_checksum


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tauri_bundle_is_a_per_user_nsis_installer() -> None:
    config = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["productName"] == "DeterminFlow"
    assert config["bundle"]["targets"] == ["nsis"]
    assert config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"
    assert (
        config["bundle"]["windows"]["webviewInstallMode"]["type"]
        == "downloadBootstrapper"
    )
    assert "plugins" not in config or "updater" not in config["plugins"]


def test_desktop_workflow_uses_a_real_windows_runner_and_only_uploads_artifact() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "desktop-windows.yml"
    ).read_text(encoding="utf-8")

    assert "runs-on: windows-2025" in workflow
    assert "desktop/scripts/smoke_backend.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "tauri-action" not in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "gh release" not in workflow.lower()
    assert "contents: write" not in workflow


def test_stage_defaults_uses_sanitized_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_read(_repo_root: Path, relative_path: str) -> object:
        if relative_path.endswith("models_config.example.json"):
            return {"providers": {"safe": {"api_key": "${SAFE_API_KEY}"}}}
        return {"source": relative_path}

    monkeypatch.setattr(defaults_module, "_read_git_json", fake_read)
    output = tmp_path / "defaults"
    defaults_module.stage_defaults(tmp_path, output)

    assert json.loads((output / "extensions.json").read_text())["enabled"] == []
    assert json.loads((output / "mcp_servers.json").read_text()) == {
        "mcpServers": {}
    }
    plugin_source = json.loads((output / "plugin-sources.json").read_text())
    assert plugin_source["official_sources"][0]["url"].startswith("https://github.com/")
    assert (output / "models_config.json").read_text() == (
        output / "models_config.example.json"
    ).read_text()
    verify_defaults(output)


def test_plaintext_api_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="明文凭据"):
        defaults_module._validate_no_plaintext_secrets(
            {"provider": {"api_key": "not-an-env-reference"}}
        )


def test_seed_user_config_preserves_existing_files(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "settings.json").write_text('{"source": "default"}', encoding="utf-8")
    (defaults / "models_config.json").write_text(
        '{"providers": {}}', encoding="utf-8"
    )
    user_root = tmp_path / "user"
    existing = user_root / "config" / "settings.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"source": "user"}', encoding="utf-8")

    created = seed_user_config(user_root, defaults)

    assert existing.read_text(encoding="utf-8") == '{"source": "user"}'
    assert user_root / "config" / "models_config.json" in created


def test_frozen_backend_can_execute_python_workflow_scripts(tmp_path: Path) -> None:
    output = tmp_path / "result.txt"
    script = tmp_path / "workflow.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )

    assert _run_python_compatibility_mode([str(script), str(output)]) is True
    assert output.read_text(encoding="utf-8") == "ok"


def test_runtime_config_consumers_follow_redirected_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name, payload in {
        "compression_config.json": {"general": {"enabled": True}},
        "mcp_servers.json": {"mcpServers": {}},
        "user_injection_config.json": {"sections": []},
    }.items():
        (config_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    environment = os.environ.copy()
    environment["DETERMINFLOW_CONFIG_DIR"] = str(config_dir)
    code = (
        "from pathlib import Path\n"
        "from src.compression.config import CompressionConfigManager\n"
        "from src.mcp.client import MCPClient\n"
        "from src.web.api_routes import USER_INJECTION_CONFIG_FILE\n"
        f"expected = Path({str(config_dir)!r}).resolve()\n"
        "assert Path(CompressionConfigManager._DEFAULT_CONFIG_PATH).parent == expected\n"
        "assert MCPClient()._resolve_config_path() == expected / 'mcp_servers.json'\n"
        "assert USER_INJECTION_CONFIG_FILE == expected / 'user_injection_config.json'\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )


def test_checksum_file_is_portable_across_line_endings(tmp_path: Path) -> None:
    installer = tmp_path / "DeterminFlow-setup.exe"
    installer.write_bytes(b"installer")

    checksum = write_checksum(installer)

    assert checksum.read_bytes().endswith(b"\n")
    assert b"\r\n" not in checksum.read_bytes()
