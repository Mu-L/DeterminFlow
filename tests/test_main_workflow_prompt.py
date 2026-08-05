from __future__ import annotations

import json
from pathlib import Path

from src.core.utils import estimate_tokens
from src.prompts.manager import PromptManager
from src.prompts.orchestrator import PromptOrchestrator
from src.workflow.tools import create_start_workflow_task_tool


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_CONFIG = REPO_ROOT / "config" / "prompts_config.json"


def _workflow_practice_section() -> dict:
    config = json.loads(PROMPT_CONFIG.read_text(encoding="utf-8"))
    sections = config["agents"]["main"]["sections"]
    matches = [
        section
        for section in sections
        if section["name"] == "workflow_task_practice"
    ]
    assert len(matches) == 1
    return matches[0]


def test_main_prompt_defines_one_chat_only_workflow_task_path() -> None:
    section = _workflow_practice_section()
    content = section["content"]

    assert section["enabled"] is True
    assert section["chat_only"] is True
    assert section["workflow_only"] is False
    assert section["token_estimate"] == estimate_tokens(content)
    for required_text in (
        "workflow-guide",
        "create_and_attach_task",
        "workspace_mode=task_isolated",
        "main_takeover=false",
        "workflow_id + task_id",
        "start_workflow_task",
        'get_task_result(..., wait_for="terminal_or_attention", timeout_seconds=null)',
        "attention_required=true",
        "read_task_artifact",
    ):
        assert required_text in content
    assert "不要循环调用 `list_tasks` 或 `get_task_status`" in content


def test_workflow_task_path_is_excluded_from_workflow_main(tmp_path) -> None:
    manager = PromptManager(
        config_file=PROMPT_CONFIG,
        cache_file=tmp_path / "system_prompt.json",
        history_file=tmp_path / "prompt_history.json",
    )
    orchestrator = PromptOrchestrator(manager)

    chat_prompt = orchestrator.build_effective_prompt(
        include_skills=False,
        include_rules=False,
        is_workflow=False,
    )
    workflow_prompt = orchestrator.build_effective_prompt(
        include_skills=False,
        include_rules=False,
        is_workflow=True,
    )

    assert "## Workflow Task 最优执行路径" in chat_prompt
    assert "## Workflow Task 最优执行路径" not in workflow_prompt


def test_start_task_tool_describes_default_auto_flow() -> None:
    tool = create_start_workflow_task_tool(object(), object())

    assert "按 Workflow 拓扑自动执行" in tool.description
    assert "每个节点完成后你需要审批" not in tool.description
