"""Loopback TCP transport shared by POSIX and Windows Executor IPC."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .executor_protocol import (
    MAX_FRAME_BYTES,
    ExecutorProtocolError,
)


LOOPBACK_HOST = "127.0.0.1"
AUTH_TOKEN_ENV = "DETERMINFLOW_EXECUTOR_AUTH_TOKEN"
AUTH_TOKEN_BYTES = 32


@dataclass(frozen=True)
class LoopbackEndpoint:
    """A single IPv4 loopback listener published after bind+listen."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ExecutorProtocolError("endpoint host must be loopback")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise ExecutorProtocolError("endpoint port is invalid")
        if not 1 <= self.port <= 65535:
            raise ExecutorProtocolError("endpoint port is invalid")

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)


def generate_auth_token() -> str:
    return secrets.token_urlsafe(AUTH_TOKEN_BYTES)


def require_auth_token(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise ExecutorProtocolError("auth_token must be non-empty")
    return token


def take_auth_token_from_env() -> str:
    """Read and drop the generation token so child scripts do not inherit it."""
    return require_auth_token(os.environ.pop(AUTH_TOKEN_ENV, ""))


def parse_loopback_endpoint(raw: Any) -> LoopbackEndpoint:
    if not isinstance(raw, dict):
        raise ExecutorProtocolError("endpoint must be an object")
    host = raw.get("host")
    port = raw.get("port")
    if host != LOOPBACK_HOST:
        raise ExecutorProtocolError("endpoint host must be loopback")
    if not isinstance(port, int) or isinstance(port, bool):
        raise ExecutorProtocolError("endpoint port is invalid")
    return LoopbackEndpoint(host, port)


def restrict_private_path(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        if os.name != "nt":
            raise


async def start_loopback_server(
    handler,
    *,
    limit: int = MAX_FRAME_BYTES + 1,
) -> asyncio.AbstractServer:
    return await asyncio.start_server(
        handler,
        host=LOOPBACK_HOST,
        port=0,
        family=socket.AF_INET,
        limit=limit,
    )


def bound_endpoint(server: asyncio.AbstractServer) -> LoopbackEndpoint:
    sockets = server.sockets or ()
    if not sockets:
        raise RuntimeError("loopback server has no bound sockets")
    host, port = sockets[0].getsockname()[:2]
    if host != LOOPBACK_HOST:
        raise RuntimeError("loopback server bound a non-loopback address")
    return LoopbackEndpoint(LOOPBACK_HOST, int(port))


async def open_loopback_connection(
    endpoint: LoopbackEndpoint,
    *,
    limit: int = MAX_FRAME_BYTES + 1,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(
        endpoint.host,
        endpoint.port,
        family=socket.AF_INET,
        limit=limit,
    )


def open_loopback_socket_sync(
    endpoint: LoopbackEndpoint,
    *,
    timeout: float,
) -> socket.socket:
    return socket.create_connection(endpoint.address, timeout=timeout)


def write_endpoint_file(path: Path, endpoint: LoopbackEndpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_private_path(path.parent, 0o700)
    payload = (
        json.dumps(
            {"host": endpoint.host, "port": endpoint.port},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(payload, encoding="utf-8")
    restrict_private_path(temporary, 0o600)
    os.replace(temporary, path)
    restrict_private_path(path, 0o600)


def read_endpoint_file(path: Path) -> LoopbackEndpoint:
    return parse_loopback_endpoint(json.loads(path.read_text(encoding="utf-8")))


async def wait_for_endpoint_file(
    path: Path,
    process: Any,
    *,
    deadline: float,
) -> LoopbackEndpoint:
    """Wait until the child publishes its bound loopback port, or it dies."""
    last_error: Exception | None = None
    while True:
        if getattr(process, "returncode", None) is not None:
            raise RuntimeError(
                f"Workflow Executor exited during startup: {process.returncode}"
            )
        if path.is_file():
            try:
                return read_endpoint_file(path)
            except (OSError, json.JSONDecodeError, ExecutorProtocolError) as exc:
                last_error = exc
        if asyncio.get_running_loop().time() >= deadline:
            detail = f": {last_error}" if last_error is not None else ""
            raise TimeoutError(
                f"Workflow Executor endpoint was not published{detail}"
            )
        await asyncio.sleep(0.05)
