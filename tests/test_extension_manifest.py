from pathlib import Path

import pytest

from src.extension_host.manifest import parse_extension_manifest


def _write_manifest(root: Path, content: str) -> Path:
    root.mkdir(parents=True)
    manifest = root / "extension.toml"
    manifest.write_text(content, encoding="utf-8")
    return manifest


def test_plugin_manifest_parses_settings_page_and_process(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path / "demo",
        """
[extension]
id = "demo"
name = "Demo"
version = "1.2.0"
api_version = "1"
backend = "demo.extension:create_extension"
capabilities = ["api.routes"]

[resource_namespace]
prefix = "demo-resources"

[installation]
requirements = "requirements.txt"

[settings]
schema = "settings.schema.json"

[page]
label = "Demo 配置"
static_dir = "ui"
entrypoint = "index.html"

[[processes]]
id = "api"
command = ["${PYTHON}", "-m", "demo.api"]
working_directory = "."
environment = { DEMO_CONFIG = "${CONFIG_FILE}" }
healthcheck_url = "http://127.0.0.1:8090/health"
start_timeout_seconds = 5
stop_timeout_seconds = 2
""",
    )

    parsed = parse_extension_manifest(manifest)

    assert parsed.extension_id == "demo"
    assert parsed.resource_prefix == "demo-resources"
    assert parsed.requirements == "requirements.txt"
    assert parsed.settings_schema == "settings.schema.json"
    assert parsed.page is not None
    assert parsed.page.static_dir == "ui"
    assert parsed.processes[0].process_id == "api"
    assert parsed.processes[0].command[0] == "${PYTHON}"
    assert parsed.processes[0].start_timeout_seconds == 5


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            """
[extension]
id = "demo"
[settings]
schema = "../settings.json"
""",
            "settings.schema 必须是",
        ),
        (
            """
[extension]
id = "demo"
[[processes]]
id = "api"
command = []
""",
            "command 必须是非空字符串数组",
        ),
        (
            """
[extension]
id = "demo"
[page]
static_dir = "/tmp/ui"
""",
            "page.static_dir 必须是",
        ),
    ],
)
def test_plugin_manifest_rejects_unsafe_or_invalid_declarations(
    tmp_path: Path,
    body: str,
    message: str,
):
    manifest = _write_manifest(tmp_path / "demo", body)

    with pytest.raises(ValueError, match=message):
        parse_extension_manifest(manifest)


@pytest.mark.parametrize("extension_id", ["demo_plugin", "demo.plugin", "Demo"])
def test_manifest_rejects_non_kebab_case_plugin_id(
    tmp_path: Path,
    extension_id: str,
):
    manifest = _write_manifest(
        tmp_path / extension_id,
        f"""
[extension]
id = "{extension_id}"
name = "Demo"
version = "1.0.0"
api_version = "1"
""",
    )

    with pytest.raises(ValueError, match="kebab-case"):
        parse_extension_manifest(manifest)


@pytest.mark.parametrize("resource_prefix", ["Demo", "demo_prefix", "demo.prefix"])
def test_manifest_rejects_non_kebab_case_resource_prefix(
    tmp_path: Path,
    resource_prefix: str,
):
    manifest = _write_manifest(
        tmp_path / resource_prefix,
        f"""
[extension]
id = "demo"

[resource_namespace]
prefix = "{resource_prefix}"
""",
    )

    with pytest.raises(ValueError, match="resource_namespace.prefix.*kebab-case"):
        parse_extension_manifest(manifest)


def test_manifest_without_resource_prefix_keeps_legacy_empty_prefix(
    tmp_path: Path,
):
    manifest = _write_manifest(
        tmp_path / "demo",
        """
[extension]
id = "demo"
""",
    )

    assert parse_extension_manifest(manifest).resource_prefix == ""


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        ('["Bad_ID"]', "kebab-case"),
        ('["base-plugin", "base-plugin"]', "不能重复"),
    ],
)
def test_manifest_rejects_invalid_dependencies(
    tmp_path: Path,
    dependencies: str,
    message: str,
):
    manifest = _write_manifest(
        tmp_path / "demo",
        f"""
[extension]
id = "demo"
dependencies = {dependencies}
""",
    )

    with pytest.raises(ValueError, match=message):
        parse_extension_manifest(manifest)
