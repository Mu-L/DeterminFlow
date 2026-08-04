# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH).parents[1]
generated_config = project_root / "desktop" / "generated" / "default-config"
web_dist = project_root / "web" / "dist"
core_defaults = project_root / "src" / "core" / "defaults"
bundled_plugins = project_root / "desktop" / "generated" / "bundled-plugins"

for required_path in (generated_config, web_dist, core_defaults):
    if not required_path.exists():
        raise RuntimeError(f"Desktop build input is missing: {required_path}")

datas = [
    (str(generated_config), "config"),
    (str(web_dist), "web/dist"),
    (str(core_defaults), "src/core/defaults"),
]
if bundled_plugins.is_dir():
    datas.append((str(bundled_plugins), "bundled-plugins"))
for distribution in (
    "fastapi",
    "langchain-core",
    "langchain-openai",
    "langgraph",
    "mcp",
    "openai",
    "uvicorn",
):
    datas += copy_metadata(distribution)

hiddenimports = []
hiddenimports += collect_submodules("langchain_core")
hiddenimports += collect_submodules(
    "langchain_openai",
    filter=lambda name: not name.startswith("langchain_openai.middleware"),
)
hiddenimports += collect_submodules("langgraph")
hiddenimports += collect_submodules(
    "mcp",
    filter=lambda name: not name.startswith("mcp.cli"),
)
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("watchdog.observers")

a = Analysis(
    [str(project_root / "desktop" / "python" / "entrypoint.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gunicorn", "pytest", "tkinter", "uvloop"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="determinflow-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="backend",
)
