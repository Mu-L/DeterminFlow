"""Forward-only, one-shot lifecycle commands for trusted Plugin packages."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


DEFAULT_LIFECYCLE_TIMEOUT_SECONDS = 300.0
LIFECYCLE_PLACEHOLDERS = frozenset({
    "PYTHON",
    "PLUGIN_DIR",
    "CONFIG_FILE",
    "DATA_DIR",
    "BASE_DIR",
    "PLUGIN_REVISION",
})

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_LIFECYCLE_FIELDS = frozenset({
    "migrate_command",
    "verify_command",
    "working_directory",
    "timeout_seconds",
})
_INHERITED_ENVIRONMENT_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
)


class LifecycleCommandError(RuntimeError):
    """A lifecycle command could not start, timed out, or exited unsuccessfully."""

    def __init__(
        self,
        *,
        owner: str,
        stage: str,
        reason: str,
        returncode: int | None = None,
    ) -> None:
        self.owner = owner
        self.stage = stage
        self.reason = reason
        self.returncode = returncode
        super().__init__(f"Plugin {owner} lifecycle {stage} 失败: {reason}")


@dataclass(frozen=True)
class ExtensionLifecycle:
    """Optional, forward-only commands declared by a Plugin package."""

    migrate_command: tuple[str, ...] = ()
    verify_command: tuple[str, ...] = ()
    working_directory: str = "."
    timeout_seconds: float = DEFAULT_LIFECYCLE_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        _validate_command(self.migrate_command, field_name="lifecycle.migrate_command")
        _validate_command(self.verify_command, field_name="lifecycle.verify_command")
        if not self.migrate_command and not self.verify_command:
            raise ValueError("[lifecycle] 至少需要一个 migrate_command 或 verify_command")
        normalized_cwd = _validate_working_directory(self.working_directory)
        object.__setattr__(self, "working_directory", normalized_cwd)
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("lifecycle.timeout_seconds 必须是正数")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True)
class LifecycleCommandResult:
    """Non-sensitive execution metadata for one lifecycle stage."""

    stage: str
    returncode: int
    elapsed_seconds: float


def parse_extension_lifecycle(value: Any) -> ExtensionLifecycle | None:
    """Parse and validate one optional ``[lifecycle]`` TOML table."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("[lifecycle] 必须是 TOML table")
    unknown_fields = sorted(set(value) - _LIFECYCLE_FIELDS)
    if unknown_fields:
        raise ValueError(
            "lifecycle 包含不支持的字段: " + ", ".join(unknown_fields)
        )

    return ExtensionLifecycle(
        migrate_command=_parse_command(value, "migrate_command"),
        verify_command=_parse_command(value, "verify_command"),
        working_directory=_parse_working_directory(value),
        timeout_seconds=_parse_timeout(value),
    )


def load_extension_lifecycle(manifest_path: Path) -> ExtensionLifecycle | None:
    """Load only the lifecycle declaration from an ``extension.toml`` file."""
    with Path(manifest_path).resolve().open("rb") as handle:
        data = tomllib.load(handle)
    return parse_extension_lifecycle(data.get("lifecycle"))


async def run_extension_lifecycle(
    lifecycle: ExtensionLifecycle | None,
    *,
    owner: str,
    plugin_dir: Path,
    config_file: Path,
    data_dir: Path,
    base_dir: Path,
    plugin_revision: str,
    python_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[LifecycleCommandResult, ...]:
    """Run migrate then verify without a shell or inherited secret environment."""
    if lifecycle is None:
        return ()

    resolved_plugin_dir = Path(plugin_dir).expanduser().resolve()
    if not resolved_plugin_dir.is_dir():
        raise LifecycleCommandError(
            owner=owner,
            stage="prepare",
            reason="PLUGIN_DIR 不存在或不是目录",
        )
    resolved_data_dir = Path(data_dir).expanduser().resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    resolved_working_directory = (
        resolved_plugin_dir / lifecycle.working_directory
    ).resolve()
    if not resolved_working_directory.is_dir():
        raise LifecycleCommandError(
            owner=owner,
            stage="prepare",
            reason="working_directory 不存在或不是目录",
        )
    try:
        resolved_working_directory.relative_to(resolved_plugin_dir)
    except ValueError as exc:
        raise LifecycleCommandError(
            owner=owner,
            stage="prepare",
            reason="working_directory 逃逸 Plugin 根目录",
        ) from exc

    placeholders = {
        "PYTHON": python_executable or sys.executable,
        "PLUGIN_DIR": str(resolved_plugin_dir),
        "CONFIG_FILE": str(Path(config_file).expanduser().resolve()),
        "DATA_DIR": str(resolved_data_dir),
        "BASE_DIR": str(Path(base_dir).expanduser().resolve()),
        "PLUGIN_REVISION": str(plugin_revision),
    }
    results: list[LifecycleCommandResult] = []
    for stage, command in (
        ("migrate", lifecycle.migrate_command),
        ("verify", lifecycle.verify_command),
    ):
        if not command:
            continue
        results.append(
            await _run_command(
                owner=owner,
                stage=stage,
                command=command,
                cwd=resolved_working_directory,
                placeholders=placeholders,
                timeout_seconds=lifecycle.timeout_seconds,
                environment=environment,
            )
        )
    return tuple(results)


def _parse_command(table: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    if field_name not in table:
        return ()
    value = table[field_name]
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"lifecycle.{field_name} 必须是非空字符串数组")
    command = tuple(value)
    _validate_command(command, field_name=f"lifecycle.{field_name}")
    return command


def _parse_working_directory(table: Mapping[str, Any]) -> str:
    value = table.get("working_directory", ".")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("lifecycle.working_directory 必须是非空字符串")
    return _validate_working_directory(value.strip())


def _parse_timeout(table: Mapping[str, Any]) -> float:
    value = table.get("timeout_seconds", DEFAULT_LIFECYCLE_TIMEOUT_SECONDS)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError("lifecycle.timeout_seconds 必须是正数")
    return float(value)


def _validate_command(command: tuple[str, ...], *, field_name: str) -> None:
    if not isinstance(command, tuple):
        raise ValueError(f"{field_name} 必须是字符串 tuple")
    if any(not isinstance(item, str) or not item for item in command):
        raise ValueError(f"{field_name} 必须包含非空字符串")
    for item in command:
        if "\x00" in item:
            raise ValueError(f"{field_name} 不能包含 NUL 字符")
        _validate_placeholders(item, field_name=field_name)


def _validate_placeholders(value: str, *, field_name: str) -> None:
    unknown = sorted(
        {
            match.group(1)
            for match in _PLACEHOLDER_RE.finditer(value)
            if match.group(1) not in LIFECYCLE_PLACEHOLDERS
        }
    )
    if unknown:
        raise ValueError(
            f"{field_name} 包含未知占位符: "
            + ", ".join(f"${{{name}}}" for name in unknown)
        )
    remainder = _PLACEHOLDER_RE.sub("", value)
    if "${" in remainder:
        raise ValueError(f"{field_name} 包含无效占位符")


def _validate_working_directory(value: str) -> str:
    normalized = value.replace("\\", "/")
    if "${" in normalized:
        raise ValueError("lifecycle.working_directory 不支持占位符")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or _WINDOWS_DRIVE_RE.match(normalized)
        or any(part in {"", ".."} for part in path.parts)
    ):
        raise ValueError(
            "lifecycle.working_directory 必须是 Plugin 根目录内的相对路径"
        )
    return path.as_posix()


def _replace_placeholders(
    value: str,
    placeholders: Mapping[str, str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        return placeholders[match.group(1)]

    return _PLACEHOLDER_RE.sub(replace, value)


def _controlled_environment(
    configured: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in _INHERITED_ENVIRONMENT_KEYS
        if key in os.environ
    }
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    })
    for key, value in (configured or {}).items():
        if not _ENVIRONMENT_NAME_RE.fullmatch(key):
            raise ValueError(f"invalid lifecycle environment name: {key}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(
                f"invalid lifecycle environment value: {key}"
            )
        environment[key] = value
    return environment


async def _run_command(
    *,
    owner: str,
    stage: str,
    command: tuple[str, ...],
    cwd: Path,
    placeholders: Mapping[str, str],
    timeout_seconds: float,
    environment: Mapping[str, str] | None,
) -> LifecycleCommandResult:
    argv = tuple(_replace_placeholders(item, placeholders) for item in command)
    started_at = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=_controlled_environment(environment),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
    except (OSError, ValueError) as exc:
        raise LifecycleCommandError(
            owner=owner,
            stage=stage,
            reason=f"命令无法启动（{type(exc).__name__}）",
        ) from exc

    try:
        returncode = await asyncio.wait_for(
            process.wait(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        await _terminate_process(process)
        raise LifecycleCommandError(
            owner=owner,
            stage=stage,
            reason=f"命令超过 {timeout_seconds:g} 秒超时",
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise

    if returncode != 0:
        raise LifecycleCommandError(
            owner=owner,
            stage=stage,
            reason=f"命令退出码为 {returncode}",
            returncode=returncode,
        )
    return LifecycleCommandResult(
        stage=stage,
        returncode=returncode,
        elapsed_seconds=time.monotonic() - started_at,
    )


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, 9)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    await process.wait()
