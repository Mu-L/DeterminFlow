from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_FILES = ("requirements.lock", "requirements-dev.lock")


def test_uvloop_is_excluded_on_windows() -> None:
    for lock_file in LOCK_FILES:
        lines = (REPO_ROOT / lock_file).read_text(encoding="utf-8").splitlines()
        uvloop_lines = [line for line in lines if line.startswith("uvloop==")]

        assert len(uvloop_lines) == 1
        marker = Requirement(uvloop_lines[0]).marker
        assert marker is not None

        windows = default_environment()
        windows.update(
            sys_platform="win32",
            platform_python_implementation="CPython",
        )
        assert not marker.evaluate(windows)

        linux = default_environment()
        linux.update(
            sys_platform="linux",
            platform_python_implementation="CPython",
        )
        assert marker.evaluate(linux)
