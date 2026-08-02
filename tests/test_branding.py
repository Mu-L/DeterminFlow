from __future__ import annotations

from pathlib import Path

from src.environment import determinflow_env_is_set, get_determinflow_env


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_current_environment_name_has_priority(monkeypatch) -> None:
    monkeypatch.setenv("AI_COMPANY_DATA_DIR", "/legacy-data")
    monkeypatch.setenv("DETERMINFLOW_DATA_DIR", "/current-data")

    assert get_determinflow_env("DATA_DIR") == "/current-data"
    assert determinflow_env_is_set("DATA_DIR") is True


def test_legacy_environment_name_remains_supported(monkeypatch) -> None:
    monkeypatch.delenv("DETERMINFLOW_CONFIG_DIR", raising=False)
    monkeypatch.setenv("AI_COMPANY_CONFIG_DIR", "/legacy-config")

    assert get_determinflow_env("CONFIG_DIR") == "/legacy-config"
    assert determinflow_env_is_set("CONFIG_DIR") is True


def test_public_brand_uses_one_name_and_asset_source() -> None:
    public_surfaces = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "README.en.md",
            "web/index.html",
            "web/src/App.tsx",
            "web/src/pages/ChatPage.tsx",
            "src/web_server.py",
            "pyproject.toml",
        )
    )

    assert "DeterminFlow" in public_surfaces
    assert "AI Agent Control Panel" not in public_surfaces
    assert "Agent Control Panel" not in public_surfaces
    assert "Determin Flow" not in public_surfaces
    assert 'name = "determinflow"' in _read("pyproject.toml")

    brand_root = REPO_ROOT / "web/public/brand"
    expected_assets = {
        "determinflow-lockup.svg",
        "determinflow-lockup-dark.svg",
        "determinflow-mark.svg",
        "determinflow-mark-dark.svg",
    }
    assert {path.name for path in brand_root.glob("*.svg")} == expected_assets
    assert not (REPO_ROOT / "docs/assets/brand").exists()
    assert not (REPO_ROOT / "web/public/vite.svg").exists()
    assert not (REPO_ROOT / "web/src/assets/react.svg").exists()
