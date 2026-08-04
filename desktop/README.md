# DeterminFlow Windows Desktop

本目录只服务于桌面发行构建。服务版仍从仓库根目录执行 `python run.py`，无需加载这里的 Tauri、PyInstaller 或 NSIS 配置。

## 架构

| 部分 | 实现 | 运行职责 |
|---|---|---|
| 桌面壳 | Tauri 2 + Windows WebView2 | 创建原生窗口、启动和关闭本地后端 |
| 后端 | PyInstaller `onedir` | 冻结现有 Python/FastAPI 服务，不要求用户安装 Python |
| 前端 | 现有 `web/dist` | 由本地 FastAPI 服务提供，入口和服务版一致 |
| 安装包 | NSIS `currentUser` | 安装到当前用户目录，不申请管理员权限 |
| 构建 | GitHub Actions `windows-2025` | 在真实 x64 Windows Runner 上生成、安装、启动并卸载验证安装包 |

桌面进程每次选择一个空闲的 `127.0.0.1` 端口。Windows Release 使用 GUI Subsystem，只显示主界面，不额外打开 CMD 窗口；应用、安装器和卸载器统一使用 `web/public/brand/determinflow-mark.svg` 对应的正式图标。窗口在 `/api/system/status` 返回成功后才进入现有 Web UI；重复打开只会唤起已有窗口；窗口退出时会终止内置后端及其子进程。

## 数据边界

运行数据位于 `%LOCALAPPDATA%\\io.determinflow.desktop`：

```text
io.determinflow.desktop/
├── config/  # 用户配置；升级时不覆盖
├── data/    # 会话、工作流、Workspace、Skills、Rules、Plugins
└── logs/    # 服务日志与 backend-console.log
```

构建只读取 Git `HEAD` 中的白名单配置。模型配置由 `models_config.example.json` 生成；MCP Server 和 Extension 默认关闭；Plugin Source 固定为公开仓库。忽略的 `config/models_config.json`、工作区数据、本地 Plugin 状态和凭据不会进入安装包。

## 本地验证

macOS 可以完成平台无关测试、Web 构建、PyInstaller 后端冒烟测试和 Rust 编译检查；NSIS 安装、WebView2 和卸载行为必须由 Windows CI 验证。

```bash
python -m pytest tests/test_desktop_packaging.py -q
python desktop/scripts/stage_defaults.py
(cd web && npm ci && npm run build)
python -m pip install pyinstaller==6.21.0
python desktop/scripts/build_backend.py
python desktop/scripts/smoke_backend.py
python desktop/scripts/verify_bundle.py
(cd desktop && npm ci)
(cd desktop/src-tauri && cargo test)
```

GitHub 临时分支 `codex/desktop-tauri-poc` 会运行 `.github/workflows/desktop-windows.yml`，但只上传 14 天有效的 unsigned（未签名）构建 Artifact；不会创建 Tag、Release 或修改 `main`。

## 首版限制

- 安装包尚未做 Authenticode（Windows 代码签名），因此不同 Windows 设备上的 SmartScreen 表现可能不同。
- 自动更新尚未启用。下一阶段可接 Tauri Updater，以 GitHub Release 或自有加速地址提供签名后的更新清单和构建文件。
- 不内置 Node.js、npm、Git 或 Git Bash。`execute_command` 使用 Windows `cmd.exe`；Python Workflow 由冻结后端兼容执行；Shell Workflow 需要用户另行安装 Git Bash。
- `downloadBootstrapper` 保持安装包较小。Windows 10/11 通常已有 WebView2；缺失时安装器需要联网下载。
