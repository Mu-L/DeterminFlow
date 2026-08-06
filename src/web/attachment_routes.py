"""Chat composer attachment uploads.

Browser file drops cannot expose a client-side absolute path. This endpoint
copies the dropped file into the selected session workspace and returns the
absolute server-side path that can be inserted into the LLM message.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, Query, Request

import src.config as config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["attachments"])

_ATTACHMENTS_DIR = "attachments"


def _safe_attachment_name(filename: str) -> str:
    """Return a basename safe to create inside the attachments directory."""
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    basename = "".join(character for character in basename if ord(character) >= 32)
    if basename in {"", ".", ".."}:
        raise ValueError("文件名无效")

    # Keep common filesystems below their 255-byte component limit without
    # discarding the extension that helps tools identify the file type.
    encoded = basename.encode("utf-8")
    if len(encoded) <= 240:
        return basename

    suffix = Path(basename).suffix
    suffix_bytes = suffix.encode("utf-8")
    available = max(1, 240 - len(suffix_bytes))
    stem_bytes = Path(basename).stem.encode("utf-8")[:available]
    stem = stem_bytes.decode("utf-8", errors="ignore") or "file"
    return f"{stem}{suffix}"


def _reserve_attachment_path(directory: Path, filename: str) -> Path:
    """Atomically reserve a non-overwriting path for a new attachment."""
    source = Path(filename)
    stem = source.stem or "file"
    suffix = source.suffix

    for index in range(1000):
        candidate_name = filename if index == 0 else f"{stem} ({index}){suffix}"
        candidate = directory / candidate_name
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate

    raise RuntimeError("同名附件过多")


@router.post("/workspace/{session_id}/attachments")
async def upload_workspace_attachment(
    session_id: str,
    request: Request,
    filename: str = Query(min_length=1),
):
    """Store one raw request body in the selected session workspace."""
    session_manager = request.app.state.session_manager
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")
    if not session.workspace_path:
        raise HTTPException(status_code=400, detail=f"会话 {session_id} 没有 workspace")

    try:
        safe_name = _safe_attachment_name(filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    workspace = Path(session.workspace_path).resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=400, detail=f"会话 {session_id} 的 workspace 不可用")

    attachments_dir = workspace / _ATTACHMENTS_DIR
    attachments_dir.mkdir(parents=True, exist_ok=True)
    attachments_dir = attachments_dir.resolve()
    if not attachments_dir.is_relative_to(workspace):
        raise HTTPException(status_code=403, detail="附件目录超出会话 workspace")

    max_size = config.CODING_WORKSPACE_MAX_SIZE
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_size:
        raise HTTPException(status_code=413, detail="文件超过 workspace 单文件大小限制")

    try:
        destination = _reserve_attachment_path(attachments_dir, safe_name)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    total_size = 0
    try:
        async with aiofiles.open(destination, "wb") as output:
            async for chunk in request.stream():
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail="文件超过 workspace 单文件大小限制",
                    )
                await output.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as error:
        destination.unlink(missing_ok=True)
        logger.exception("保存会话附件失败")
        raise HTTPException(status_code=500, detail="保存附件失败，请重试") from error

    return {
        "name": destination.name,
        "relative_path": destination.relative_to(workspace).as_posix(),
        "absolute_path": str(destination.resolve()),
        "size": total_size,
    }
