import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).parents[1]


def _run_isolated_config(
    tmp_path: Path,
    code: str,
    env_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DETERMINFLOW_CONFIG_DIR"] = str(tmp_path)
    env.pop("SHOW_SYSTEM_PROMPT_TAB", None)
    env.pop("AI_COMPANY_SHOW_SYSTEM_PROMPT_TAB", None)
    env.pop("CODING_WORKSPACE_BASE", None)
    if env_updates:
        env.update(env_updates)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_system_prompt_navigation_setting_defaults_to_hidden(tmp_path: Path) -> None:
    result = _run_isolated_config(
        tmp_path,
        """
import json
from src import config
item = next(entry for entry in config.CONFIG_ITEMS if entry["key"] == "SHOW_SYSTEM_PROMPT_TAB")
print(json.dumps({"value": config.SHOW_SYSTEM_PROMPT_TAB, "item": item}))
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["value"] is False
    assert payload["item"] == {
        "key": "SHOW_SYSTEM_PROMPT_TAB",
        "label": "顶部展示系统提示词",
        "group": "system",
        "type": "boolean",
    }


def test_system_prompt_navigation_setting_can_be_persisted(tmp_path: Path) -> None:
    result = _run_isolated_config(
        tmp_path,
        """
import json
from src import config
updated = config.update_config({"SHOW_SYSTEM_PROMPT_TAB": True}, persist=True)
stored = json.loads(config.SETTINGS_CONFIG_FILE.read_text(encoding="utf-8"))
print(json.dumps({"updated": updated["SHOW_SYSTEM_PROMPT_TAB"], "stored": stored["system"]["SHOW_SYSTEM_PROMPT_TAB"]}))
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"updated": True, "stored": "true"}


def test_desktop_workspace_setting_overrides_redirected_data_dir(tmp_path: Path) -> None:
    custom_workspace = tmp_path / "custom-workspaces"
    (tmp_path / "settings.json").write_text(
        json.dumps({"coding": {"CODING_WORKSPACE_BASE": str(custom_workspace)}}),
        encoding="utf-8",
    )

    result = _run_isolated_config(
        tmp_path,
        """
import json
from src import config
from src.core.workspace_manager import WorkspaceManager
manager = WorkspaceManager()
print(json.dumps({"configured": config.CODING_WORKSPACE_BASE, "resolved": str(manager.base_dir)}))
""",
        {
            "DETERMINFLOW_DATA_DIR": str(tmp_path / "desktop-user" / "data"),
            "DETERMINFLOW_DESKTOP": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "configured": str(custom_workspace),
        "resolved": str(custom_workspace),
    }


def test_desktop_default_workspace_stays_under_user_data_dir(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"coding": {"CODING_WORKSPACE_BASE": "data/workspaces"}}),
        encoding="utf-8",
    )
    data_dir = tmp_path / "desktop-user" / "data"

    result = _run_isolated_config(
        tmp_path,
        """
import json
from src.core.workspace_manager import WorkspaceManager
print(json.dumps({"resolved": str(WorkspaceManager().base_dir)}))
""",
        {
            "DETERMINFLOW_DATA_DIR": str(data_dir),
            "DETERMINFLOW_DESKTOP": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "resolved": str(data_dir / "workspaces"),
    }


def test_workspace_setting_refreshes_chat_and_workflow_managers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src import config
    from src.core.workspace_manager import WorkspaceManager
    from src.web.api_routes import UpdateConfigRequest, update_config_api

    old_root = tmp_path / "old-workspaces"
    new_root = tmp_path / "new-workspaces"
    chat_manager = WorkspaceManager(base_dir=old_root)
    workflow_workspace_manager = WorkspaceManager(base_dir=old_root)
    existing = chat_manager.create_workspace("existing-session")
    monkeypatch.setattr(config, "CODING_WORKSPACE_BASE", str(old_root))

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                workspace_manager=chat_manager,
                workflow_manager=SimpleNamespace(
                    _ws_manager=workflow_workspace_manager,
                ),
            )
        )
    )
    result = asyncio.run(
        update_config_api(
            UpdateConfigRequest(
                updates={"CODING_WORKSPACE_BASE": str(new_root)},
                persist=False,
            ),
            request,
        )
    )

    assert result["success"] is True
    assert result["config"]["CODING_WORKSPACE_BASE"] == str(new_root)
    assert chat_manager.base_dir == new_root
    assert workflow_workspace_manager.base_dir == new_root
    assert chat_manager.get_workspace("existing-session") == existing
    assert chat_manager.create_workspace("new-session") == new_root / "new-session"
