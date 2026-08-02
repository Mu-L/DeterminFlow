from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_standard_container_supports_git_managed_plugins() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "git \\" in dockerfile
    assert "AI_COMPANY_EXTENSIONS" not in dockerfile
    assert "AI_COMPANY_EXTENSIONS" not in compose
    assert "./data:/app/data" in compose
    assert "./config:/app/config" in compose
