from __future__ import annotations

import asyncio

import pytest

from src.workflow.variable_resolution import (
    resolve_placeholders,
    resolve_workspace_file_path,
)


def test_file_variable_reads_only_inside_workflow_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.txt").write_text("合法输入", encoding="utf-8")
    variables = [{"key": "input_file", "type": "file", "required": True}]

    resolved = asyncio.run(
        resolve_placeholders(
            "内容：{{input_file}}",
            {"input_file": "input.txt"},
            variables,
            str(workspace),
        )
    )

    assert resolved == "内容：合法输入"


@pytest.mark.parametrize("target", ("../secret.txt", "/etc/passwd"))
def test_workflow_file_path_rejects_workspace_escape(tmp_path, target):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="越出 workspace"):
        resolve_workspace_file_path(workspace, target)


def test_workflow_file_path_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="越出 workspace"):
        resolve_workspace_file_path(workspace, "linked/output.json")


def test_workflow_file_path_allows_absolute_path_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "nested/output.json"

    assert resolve_workspace_file_path(workspace, str(target)) == target.resolve()
