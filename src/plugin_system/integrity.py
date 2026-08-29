"""Content-addressed hashing for plugin checkouts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def plugin_content_sha256(root: Path) -> str:
    """Hash a plugin tree, ignoring `.git` and runtime `__pycache__`."""
    digest = hashlib.sha256()

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.name == ".git":
                continue
            if (
                path.name == "__pycache__"
                and path.is_dir()
                and not path.is_symlink()
            ):
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            if path.is_symlink():
                digest.update(b"L\0" + relative + b"\0")
                digest.update(os.readlink(path).encode("utf-8"))
                digest.update(b"\0")
            elif path.is_dir():
                digest.update(b"D\0" + relative + b"\0")
                visit(path)
            elif path.is_file():
                executable = b"1" if path.stat().st_mode & 0o111 else b"0"
                digest.update(b"F\0" + relative + b"\0" + executable + b"\0")
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")

    visit(root)
    return digest.hexdigest()
