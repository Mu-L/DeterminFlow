from pathlib import Path

import pytest

from src import config
from src.core.workspace_manager import WorkspaceManager


@pytest.fixture
def workspace_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path / "project"
    data_root = tmp_path / "runtime-data"
    default_root = tmp_path / "default-workspaces"
    project_root.mkdir()
    data_root.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", project_root)
    monkeypatch.setattr(config, "DATA_DIR", data_root)
    return (
        WorkspaceManager(base_dir=str(default_root)),
        project_root,
        data_root,
        default_root,
    )


def test_absolute_override_allows_external_data_dir(
    workspace_roots,
) -> None:
    manager, _, data_root, _ = workspace_roots
    override = data_root / "plugins" / "data" / "example-plugin" / "batches"

    resolved = manager.resolve_workflow_workspace(
        "example-workflow",
        override=str(override),
    )

    assert resolved == override.resolve()
    assert resolved.is_dir()


def test_absolute_override_still_allows_project_root(
    workspace_roots,
) -> None:
    manager, project_root, _, _ = workspace_roots
    override = project_root / "data" / "legacy-workspace"

    resolved = manager.resolve_workflow_workspace(
        "legacy-workflow",
        override=str(override),
    )

    assert resolved == override.resolve()
    assert resolved.is_dir()


def test_absolute_override_rejects_arbitrary_external_path(
    workspace_roots,
) -> None:
    manager, _, _, default_root = workspace_roots
    outside = default_root.parent / "arbitrary-external" / "workspace"

    resolved = manager.resolve_workflow_workspace(
        "unsafe-workflow",
        override=str(outside),
    )

    assert resolved == default_root / "unsafe-workflow"
    assert not outside.exists()


def test_relative_override_cannot_escape_to_external_data_dir(
    workspace_roots,
) -> None:
    manager, project_root, data_root, default_root = workspace_roots
    relative_escape = Path("..") / data_root.name / "plugin-workspace"
    assert (project_root / relative_escape).resolve().is_relative_to(data_root)

    resolved = manager.resolve_workflow_workspace(
        "relative-escape",
        override=str(relative_escape),
    )

    assert resolved == default_root / "relative-escape"
    assert not (data_root / "plugin-workspace").exists()


def test_absolute_override_rejects_symlink_escape_from_data_dir(
    workspace_roots,
) -> None:
    manager, _, data_root, default_root = workspace_roots
    outside = default_root.parent / "outside"
    outside.mkdir()
    link = data_root / "plugin-data-link"
    link.symlink_to(outside, target_is_directory=True)

    resolved = manager.resolve_workflow_workspace(
        "symlink-escape",
        override=str(link / "batches"),
    )

    assert resolved == default_root / "symlink-escape"
    assert not (outside / "batches").exists()
