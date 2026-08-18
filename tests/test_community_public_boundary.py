"""Public-boundary checks for the community Executor Pool sync."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UNTRACKED_SECRET_PATHS = (
    "config/models_config.json",
    "config/mcp_servers.local.json",
)
FORBIDDEN_PATHS = (
    "deploy/production",
    "scripts/production",
)
FORBIDDEN_FRAGMENTS = (
    "deploy/production",
    "scripts/production",
)
COMMUNITY_RELEASE_FILES = (
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "README.md",
    "README.en.md",
    "desktop/release-notes/v1.0.9.md",
    "config/models_config.example.json",
)
EXECUTOR_GLOBS = (
    "src/workflow/executor_*.py",
    "src/workflow/execution_routing.py",
    "src/extension_host/executor_plane.py",
    "scripts/workflow_executor_pool_benchmark.py",
    "tests/test_workflow_executor_*.py",
    "tests/test_extension_executor_plane.py",
    "tests/workflow_executor_pool_*.py",
)


def test_community_release_files_remain() -> None:
    for relative in COMMUNITY_RELEASE_FILES:
        assert (REPO_ROOT / relative).is_file(), relative


def test_private_operations_and_secrets_stay_out_of_tree() -> None:
    import subprocess

    tracked = subprocess.check_output(
        ["git", "ls-files", "--", *UNTRACKED_SECRET_PATHS, *FORBIDDEN_PATHS],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    assert tracked == ""
    for relative in FORBIDDEN_PATHS:
        assert not (REPO_ROOT / relative).exists(), relative
    assert not (REPO_ROOT / "deploy").exists()
    assert not list((REPO_ROOT / "scripts").glob("production*"))


def test_executor_slice_does_not_copy_private_fragments() -> None:
    files: list[Path] = []
    for pattern in EXECUTOR_GLOBS:
        files.extend(REPO_ROOT.glob(pattern))
    assert files
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in combined, fragment
