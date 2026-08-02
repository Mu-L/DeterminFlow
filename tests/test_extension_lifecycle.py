from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from src.extension_host.lifecycle import (
    ExtensionLifecycle,
    LifecycleCommandError,
    load_extension_lifecycle,
    run_extension_lifecycle,
)
from src.extension_host.manifest import parse_extension_manifest


def _write_manifest(root: Path, lifecycle: str = "") -> Path:
    root.mkdir(parents=True)
    manifest = root / "extension.toml"
    manifest.write_text(
        f"""
[extension]
id = "demo"
name = "Demo"
version = "1.0.0"
{lifecycle}
""",
        encoding="utf-8",
    )
    return manifest


def _run_lifecycle(
    lifecycle: ExtensionLifecycle | None,
    tmp_path: Path,
    *,
    plugin_dir: Path | None = None,
) -> tuple:
    selected_plugin_dir = plugin_dir or tmp_path / "plugin"
    selected_plugin_dir.mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / "config" / "demo.json"
    config_file.parent.mkdir()
    config_file.write_text("{}", encoding="utf-8")
    return asyncio.run(
        run_extension_lifecycle(
            lifecycle,
            owner="demo",
            plugin_dir=selected_plugin_dir,
            config_file=config_file,
            data_dir=tmp_path / "data",
            base_dir=tmp_path,
            plugin_revision="a" * 40,
        )
    )


def test_manifest_parses_optional_forward_only_lifecycle(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "plugin",
        """
[lifecycle]
migrate_command = ["${PYTHON}", "-m", "demo.migrations", "migrate"]
verify_command = ["${PYTHON}", "-m", "demo.migrations", "verify"]
working_directory = "backend"
timeout_seconds = 45
""",
    )

    parsed_manifest = parse_extension_manifest(manifest)
    lifecycle = load_extension_lifecycle(manifest)

    assert parsed_manifest.extension_id == "demo"
    assert lifecycle is not None
    assert lifecycle.migrate_command[-1] == "migrate"
    assert lifecycle.verify_command[-1] == "verify"
    assert lifecycle.working_directory == "backend"
    assert lifecycle.timeout_seconds == 45


def test_manifest_without_lifecycle_loads_none(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "plugin")

    assert load_extension_lifecycle(manifest) is None
    assert parse_extension_manifest(manifest).extension_id == "demo"


@pytest.mark.parametrize(
    ("lifecycle", "message"),
    [
        (
            """
[lifecycle]
migrate_command = "${PYTHON} migrate"
""",
            "migrate_command 必须是非空字符串数组",
        ),
        (
            """
[lifecycle]
migrate_command = []
""",
            "migrate_command 必须是非空字符串数组",
        ),
        (
            """
[lifecycle]
migrate_command = ["${UNKNOWN}", "migrate"]
""",
            "未知占位符",
        ),
        (
            """
[lifecycle]
migrate_command = ["${python}", "migrate"]
""",
            "无效占位符",
        ),
        (
            """
[lifecycle]
migrate_command = ["${PYTHON}", "migrate"]
working_directory = "../outside"
""",
            "相对路径",
        ),
        (
            """
[lifecycle]
migrate_command = ["${PYTHON}", "migrate"]
working_directory = "..\\\\outside"
""",
            "相对路径",
        ),
        (
            """
[lifecycle]
migrate_command = ["${PYTHON}", "migrate"]
working_directory = "/tmp"
""",
            "相对路径",
        ),
        (
            """
[lifecycle]
migrate_command = ["${PYTHON}", "migrate"]
timeout_seconds = false
""",
            "timeout_seconds 必须是正数",
        ),
        (
            """
[lifecycle]
down_command = ["${PYTHON}", "down"]
""",
            "不支持的字段: down_command",
        ),
        (
            """
[lifecycle]
""",
            "至少需要一个",
        ),
    ],
)
def test_manifest_rejects_invalid_lifecycle(
    tmp_path: Path,
    lifecycle: str,
    message: str,
) -> None:
    manifest = _write_manifest(tmp_path / "plugin", lifecycle)

    with pytest.raises(ValueError, match=message):
        parse_extension_manifest(manifest)


def test_runner_executes_migrate_then_verify_with_supported_placeholders(
    tmp_path: Path,
) -> None:
    writer = (
        "from pathlib import Path; import sys; "
        "path=Path(sys.argv[1]); "
        "path.write_text(path.read_text() + sys.argv[2] + '\\n') "
        "if path.exists() else path.write_text(sys.argv[2] + '\\n')"
    )
    verifier = (
        "from pathlib import Path; import sys; "
        "path=Path(sys.argv[1]); "
        "assert path.read_text().startswith('migrate:'); "
        "path.write_text(path.read_text() + 'verify:' + '|'.join(sys.argv[2:]))"
    )
    lifecycle = ExtensionLifecycle(
        migrate_command=(
            "${PYTHON}",
            "-c",
            writer,
            "${DATA_DIR}/order.txt",
            "migrate:${PLUGIN_REVISION}",
        ),
        verify_command=(
            "${PYTHON}",
            "-c",
            verifier,
            "${DATA_DIR}/order.txt",
            "${PLUGIN_DIR}",
            "${CONFIG_FILE}",
            "${BASE_DIR}",
        ),
    )

    results = _run_lifecycle(lifecycle, tmp_path)

    assert [result.stage for result in results] == ["migrate", "verify"]
    content = (tmp_path / "data" / "order.txt").read_text(encoding="utf-8")
    assert content.startswith(f"migrate:{'a' * 40}\nverify:")
    assert str(tmp_path / "plugin") in content
    assert str(tmp_path / "config" / "demo.json") in content


def test_runner_reports_exit_failure_without_output_or_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-expose-this-value"  # pragma: allowlist secret
    monkeypatch.setenv("PLUGIN_LIFECYCLE_SECRET", secret)
    lifecycle = ExtensionLifecycle(
        migrate_command=(
            "${PYTHON}",
            "-c",
            (
                "import os,sys; "
                "sys.stderr.write(os.environ.get('PLUGIN_LIFECYCLE_SECRET', "
                f"'{secret}')); "
                "raise SystemExit(23)"
            ),
        ),
        verify_command=(
            "${PYTHON}",
            "-c",
            "from pathlib import Path; Path('verify-ran').touch()",
        ),
    )

    with pytest.raises(LifecycleCommandError) as captured:
        _run_lifecycle(lifecycle, tmp_path)

    assert captured.value.stage == "migrate"
    assert captured.value.returncode == 23
    assert "退出码为 23" in str(captured.value)
    assert secret not in str(captured.value)
    assert not (tmp_path / "plugin" / "verify-ran").exists()


def test_runner_times_out_and_terminates_command(tmp_path: Path) -> None:
    lifecycle = ExtensionLifecycle(
        migrate_command=(
            "${PYTHON}",
            "-c",
            "import time; time.sleep(10)",
        ),
        timeout_seconds=0.05,
    )
    started_at = time.monotonic()

    with pytest.raises(LifecycleCommandError, match="超时") as captured:
        _run_lifecycle(lifecycle, tmp_path)

    assert captured.value.stage == "migrate"
    assert time.monotonic() - started_at < 2


def test_runner_passes_shell_metacharacters_as_literal_argument(
    tmp_path: Path,
) -> None:
    injected = tmp_path / "injected"
    literal = f"safe; touch {injected}"
    lifecycle = ExtensionLifecycle(
        verify_command=(
            "${PYTHON}",
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text(sys.argv[2])"
            ),
            "${DATA_DIR}/literal.txt",
            literal,
        ),
    )

    _run_lifecycle(lifecycle, tmp_path)

    assert (tmp_path / "data" / "literal.txt").read_text() == literal
    assert not injected.exists()


def test_runner_receives_only_explicit_plugin_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SECRET", "must-not-leak")
    lifecycle = ExtensionLifecycle(
        verify_command=(
            "${PYTHON}",
            "-c",
            (
                "from pathlib import Path; import os; "
                "Path(os.environ['OUTPUT_FILE']).write_text("
                "os.environ['DB_HOST'] + '|' + "
                "str('HOST_SECRET' in os.environ))"
            ),
        ),
    )
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")

    asyncio.run(
        run_extension_lifecycle(
            lifecycle,
            owner="demo",
            plugin_dir=plugin_dir,
            config_file=config_file,
            data_dir=tmp_path / "data",
            base_dir=tmp_path,
            plugin_revision="revision",
            environment={
                "DB_HOST": "database.internal",
                "OUTPUT_FILE": str(tmp_path / "environment.txt"),
            },
        )
    )

    assert (tmp_path / "environment.txt").read_text() == (
        "database.internal|False"
    )


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="symlink is not supported",
)
def test_runner_rejects_working_directory_symlink_escape(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (plugin_dir / "escaped").symlink_to(outside, target_is_directory=True)
    lifecycle = ExtensionLifecycle(
        migrate_command=(sys.executable, "-c", "print('not-run')"),
        working_directory="escaped",
    )

    with pytest.raises(LifecycleCommandError, match="逃逸"):
        _run_lifecycle(lifecycle, tmp_path, plugin_dir=plugin_dir)
