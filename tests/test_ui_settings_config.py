import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).parents[1]


def _run_isolated_config(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DETERMINFLOW_CONFIG_DIR"] = str(tmp_path)
    env.pop("SHOW_SYSTEM_PROMPT_TAB", None)
    env.pop("AI_COMPANY_SHOW_SYSTEM_PROMPT_TAB", None)
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
