from __future__ import annotations

import json

from scripts.sync_gitee_release import (
    _rewrite_gitee_manifest,
    _same_attachment,
)


def test_rewrites_updater_download_to_gitee_release(tmp_path) -> None:
    manifest = tmp_path / "latest.json"
    manifest.write_text(
        json.dumps({
            "version": "1.0.4",
            "platforms": {
                "windows-x86_64": {
                    "url": (
                        "https://github.com/alikon-art/DeterminFlow/releases/"
                        "download/v1.0.4/DeterminFlow_1.0.4_x64-setup.exe"
                    ),
                    "signature": "signed",
                }
            },
        }),
        encoding="utf-8",
    )

    _rewrite_gitee_manifest(
        manifest,
        owner="alikon",
        repo="DeterminFlow",
        tag="v1.0.4",
    )

    rewritten = json.loads(manifest.read_text(encoding="utf-8"))
    platform = rewritten["platforms"]["windows-x86_64"]
    assert platform["url"] == (
        "https://gitee.com/alikon/DeterminFlow/releases/download/v1.0.4/"
        "DeterminFlow_1.0.4_x64-setup.exe"
    )
    assert platform["signature"] == "signed"


def test_attachment_size_supports_numeric_api_values(tmp_path) -> None:
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"release")

    assert _same_attachment({"size": asset.stat().st_size}, asset) is True
    assert _same_attachment({"size": str(asset.stat().st_size)}, asset) is True
