from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _now_iso() -> str:
    """返回当前北京时间 ISO 字符串。"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def _generate_id(prefix: str = "wf") -> str:
    """生成短唯一 ID。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
