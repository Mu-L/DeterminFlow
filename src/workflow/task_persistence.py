"""Workflow Task JSON persistence helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
import uuid


logger = logging.getLogger(__name__)

WINDOWS_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
_TRANSIENT_WINDOWS_REPLACE_ERRORS = {5, 32}


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace a Task file, retrying only transient Windows sharing errors."""
    total_attempts = len(WINDOWS_REPLACE_RETRY_DELAYS) + 1
    for attempt in range(total_attempts):
        if attempt:
            time.sleep(WINDOWS_REPLACE_RETRY_DELAYS[attempt - 1])
        try:
            os.replace(source, target)
            if attempt:
                logger.info(
                    "任务状态文件占用已解除: target=%s retries=%s",
                    target,
                    attempt,
                )
            return
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            if (
                winerror not in _TRANSIENT_WINDOWS_REPLACE_ERRORS
                or attempt == total_attempts - 1
            ):
                raise
            if attempt == 0:
                logger.warning(
                    "任务状态文件暂时被占用，开始有限退避重试: "
                    "target=%s winerror=%s max_wait_seconds=%.2f",
                    target,
                    winerror,
                    sum(WINDOWS_REPLACE_RETRY_DELAYS),
                )


def write_task_state_file(task_file: Path, task_data: dict) -> None:
    """Atomically write a Task snapshot and clean up its unique temp file."""
    task_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = task_file.parent / (
        f"{task_file.stem}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(task_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _replace_with_retry(temporary, task_file)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "清理任务状态临时文件失败: %s",
                temporary,
                exc_info=True,
            )
