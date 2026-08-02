"""Runtime gates for extension-owned HTTP and middleware contributions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from .manager import ExtensionManager


def extension_route_guard(
    manager: "ExtensionManager",
    owner: str,
) -> Callable[[], Awaitable[None]]:
    """Return a FastAPI dependency that opens only for a running extension."""

    async def require_running() -> None:
        if manager.is_running(owner):
            return
        state = manager.get_state(owner)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "extension_unavailable",
                "extension_id": owner,
                "status": state["status"],
                "error": state["error"],
            },
        )

    return require_running


class ExtensionMiddlewareGate:
    """Run extension middleware only while its owner is running."""

    def __init__(
        self,
        app: Any,
        *,
        manager: "ExtensionManager",
        owner: str,
        middleware: type,
        middleware_options: dict[str, Any],
    ):
        self._app = app
        self._manager = manager
        self._owner = owner
        self._extension_app = middleware(app, **middleware_options)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        app = self._extension_app if self._manager.is_running(self._owner) else self._app
        await app(scope, receive, send)
