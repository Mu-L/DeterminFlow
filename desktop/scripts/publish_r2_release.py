"""Publish signed desktop release assets to an S3-compatible R2 bucket."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from .create_update_manifest import create_manifest
except ImportError:  # Direct workflow invocation: python desktop/scripts/...
    from create_update_manifest import create_manifest


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
LATEST_CACHE_CONTROL = "no-cache, no-store, must-revalidate"
SEMVER_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_object_url(public_base_url: str, key: str) -> str:
    if not public_base_url.startswith("https://"):
        raise ValueError("R2 public base URL must use HTTPS")
    encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
    return f"{public_base_url.rstrip('/')}/{encoded_key}"


class R2Publisher:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        public_base_url: str,
        aws_binary: str = "aws",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        fetcher: Callable[..., object] = urlopen,
    ) -> None:
        if not endpoint_url.startswith("https://"):
            raise ValueError("R2 API endpoint must use HTTPS")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.public_base_url = public_base_url
        self.aws_binary = aws_binary
        self.runner = runner
        self.fetcher = fetcher

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.runner(
            [
                self.aws_binary,
                "--endpoint-url",
                self.endpoint_url,
                "s3api",
                *arguments,
            ],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _head(self, key: str) -> dict[str, object] | None:
        result = self._run(
            "head-object",
            "--bucket",
            self.bucket,
            "--key",
            key,
            check=False,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        missing_markers = ("404", "Not Found", "NoSuchKey")
        if any(marker in result.stderr for marker in missing_markers):
            return None
        raise RuntimeError(f"R2 head-object failed for {key}: {result.stderr.strip()}")

    def _put(self, path: Path, key: str, cache_control: str) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._run(
            "put-object",
            "--bucket",
            self.bucket,
            "--key",
            key,
            "--body",
            str(path),
            "--content-type",
            content_type,
            "--cache-control",
            cache_control,
            "--metadata",
            f"sha256={sha256_file(path)}",
        )

    def _verify_public(self, path: Path, key: str) -> None:
        expected_hash = sha256_file(path)
        url = f"{public_object_url(self.public_base_url, key)}?sha256={expected_hash}"
        request = Request(url, headers={"Cache-Control": "no-cache"})
        with self.fetcher(request, timeout=30) as response:
            published = response.read()
        if hashlib.sha256(published).hexdigest() != expected_hash:
            raise RuntimeError(f"R2 public object checksum mismatch: {key}")

    def publish_immutable(self, path: Path, key: str) -> None:
        expected_hash = sha256_file(path)
        existing = self._head(key)
        if existing is not None:
            metadata = existing.get("Metadata") or {}
            existing_hash = metadata.get("sha256") if isinstance(metadata, dict) else None
            if existing_hash != expected_hash or existing.get("ContentLength") != path.stat().st_size:
                raise RuntimeError(f"immutable R2 object already exists with different content: {key}")
        else:
            self._put(path, key, IMMUTABLE_CACHE_CONTROL)
        self._verify_public(path, key)

    def publish_latest(self, path: Path, key: str) -> None:
        self._put(path, key, LATEST_CACHE_CONTROL)
        self._verify_public(path, key)


def publish_release(
    *,
    assets_dir: Path,
    version: str,
    notes_file: Path,
    pub_date: str,
    publisher: R2Publisher,
) -> None:
    if not SEMVER_PATTERN.fullmatch(version):
        raise ValueError(f"invalid release version: {version}")
    installer = assets_dir / f"DeterminFlow_{version}_x64-setup.exe"
    signature = installer.with_suffix(installer.suffix + ".sig")
    if not installer.is_file() or not signature.is_file():
        raise FileNotFoundError("Core installer or updater signature is missing")

    version_prefix = f"desktop/releases/v{version}"
    assets = sorted(
        path
        for path in assets_dir.iterdir()
        if path.is_file() and path.name != "latest.json"
    )
    if any(path.is_symlink() for path in assets):
        raise ValueError("release assets must not contain symbolic links")
    if not assets:
        raise ValueError("release asset directory is empty")
    for asset in assets:
        publisher.publish_immutable(asset, f"{version_prefix}/{asset.name}")

    with tempfile.TemporaryDirectory(prefix="determinflow-r2-release-") as temporary:
        manifest_path = Path(temporary) / "latest.json"
        manifest = create_manifest(
            version=version,
            installer=installer,
            signature=signature,
            base_url=public_object_url(publisher.public_base_url, version_prefix),
            notes=notes_file.read_text(encoding="utf-8"),
            pub_date=pub_date,
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        publisher.publish_immutable(manifest_path, f"{version_prefix}/latest.json")
        publisher.publish_latest(manifest_path, "desktop/stable/latest.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--pub-date", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--aws-binary", default="aws")
    options = parser.parse_args()

    publish_release(
        assets_dir=options.assets_dir.resolve(),
        version=options.version,
        notes_file=options.notes_file.resolve(),
        pub_date=options.pub_date,
        publisher=R2Publisher(
            bucket=options.bucket,
            endpoint_url=options.endpoint_url,
            public_base_url=options.public_base_url,
            aws_binary=options.aws_binary,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
