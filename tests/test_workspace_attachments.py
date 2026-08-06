from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.attachment_routes import _safe_attachment_name, router


def _client(workspace: Path, *, session_id: str = "session-1") -> TestClient:
    workspace.mkdir(parents=True, exist_ok=True)
    session = SimpleNamespace(workspace_path=str(workspace))
    session_manager = SimpleNamespace(
        get_session=lambda requested: session if requested == session_id else None,
    )
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)
    return TestClient(app)


def test_attachment_name_discards_client_directories():
    assert _safe_attachment_name("../private/report.txt") == "report.txt"
    assert _safe_attachment_name(r"C:\\Users\\me\\notes.md") == "notes.md"


def test_upload_stores_file_in_session_workspace_and_returns_absolute_path(tmp_path):
    workspace = tmp_path / "workspace"
    response = _client(workspace).post(
        "/api/workspace/session-1/attachments",
        params={"filename": "report.txt"},
        content=b"attachment body",
    )

    assert response.status_code == 200
    payload = response.json()
    stored = Path(payload["absolute_path"])
    assert stored == (workspace / "attachments" / "report.txt").resolve()
    assert payload["relative_path"] == "attachments/report.txt"
    assert payload["size"] == len(b"attachment body")
    assert stored.read_bytes() == b"attachment body"


def test_upload_never_overwrites_an_existing_attachment(tmp_path):
    workspace = tmp_path / "workspace"
    client = _client(workspace)

    first = client.post(
        "/api/workspace/session-1/attachments",
        params={"filename": "report.txt"},
        content=b"first",
    ).json()
    second = client.post(
        "/api/workspace/session-1/attachments",
        params={"filename": "report.txt"},
        content=b"second",
    ).json()

    assert first["name"] == "report.txt"
    assert second["name"] == "report (1).txt"
    assert Path(first["absolute_path"]).read_bytes() == b"first"
    assert Path(second["absolute_path"]).read_bytes() == b"second"


def test_upload_rejects_unknown_session(tmp_path):
    response = _client(tmp_path / "workspace").post(
        "/api/workspace/missing/attachments",
        params={"filename": "report.txt"},
        content=b"body",
    )

    assert response.status_code == 404
