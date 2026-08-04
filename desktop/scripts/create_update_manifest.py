"""Create the static Tauri updater manifest uploaded with a desktop Release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote


def create_manifest(
    *,
    version: str,
    installer: Path,
    signature: Path,
    base_url: str,
    notes: str = "",
    pub_date: str | None = None,
) -> dict[str, object]:
    signature_value = signature.read_text(encoding="utf-8").strip()
    if not signature_value:
        raise ValueError("更新签名不能为空")
    platform = {
        "signature": signature_value,
        "url": f"{base_url.rstrip('/')}/{quote(installer.name)}",
    }
    manifest: dict[str, object] = {
        "version": version,
        "notes": notes,
        "platforms": {"windows-x86_64": platform},
    }
    if pub_date:
        manifest["pub_date"] = pub_date
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--pub-date")
    parser.add_argument("--output", type=Path, default=Path("latest.json"))
    options = parser.parse_args()

    notes = (
        options.notes_file.read_text(encoding="utf-8")
        if options.notes_file
        else ""
    )
    manifest = create_manifest(
        version=options.version,
        installer=options.installer,
        signature=options.signature,
        base_url=options.base_url,
        notes=notes,
        pub_date=options.pub_date,
    )
    options.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
