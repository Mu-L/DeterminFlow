# Extension 开发指南

## Manifest

```toml
[extension]
id = "example-tools"                 # 扩展唯一标识
name = "Example Tools"               # 展示名称
version = "1.0.0"                    # 扩展版本
api_version = "1"                    # Extension API 版本
backend = "example.backend:create"   # 可选 Python entrypoint
frontend = "example-tools"           # 可选前端模块 ID
dependencies = []                     # 必需 Extension ID 完整列表
capabilities = ["agent.tools"]       # 能力声明，仅用于审计

[resource_namespace]
prefix = "example"                   # 开发者默认 Prefix，安装时可高级覆盖

[resources]
agents = "resources/agents.json"
prompts = "resources/prompts.json"
skill_bundles = "resources/skill-bundles"
rule_bundles = "resources/rule-bundles"
workflows = "resources/workflows"
script_libraries = "resources/script-library"
```

`dependencies` 当前只接受 Extension ID，Host 会自动启用传递依赖并检测环。

第三方 Python 包使用 `determinflow.extensions` Entry Point 时，名称必须与
`manifest.extension_id` 相同；未启用的 Entry Point 不会被 import。历史包使用的
`ai_company.extensions` 仍受支持，但新项目不应继续采用旧名称。

## Backend

```python
class ExampleExtension:
    def register(self, registrar):
        registrar.add_router(router)
        registrar.add_tool_contributor(register_tools)
        registrar.add_health_check(check_dependency)

    async def start(self, runtime):
        self.workflow = runtime.workflow_runtime

    async def stop(self):
        pass


def create():
    return ExampleExtension()
```

`register()` 只能声明贡献，不应连接数据库、启动任务或访问网络。I/O 初始化放在 `start()`，资源释放放在可重复调用的 `stop()`；即使启动只完成了一部分，Host 也可能调用 `stop()` 做回滚。

Manifest 只接受 Core 已声明的资源类型，资源路径必须位于当前 Extension 目录内。Tool contributor 获得的是 owner-scoped registry（所有者受限注册表），即使省略 `owner` 也会自动归属当前 Extension，停止或降级时可完整回滚；尝试冒充其他 owner 会失败。

Extension 不应 import `src.web_server`、`src.agent.session_manager` 或 `src.workflow.manager` 等内部实现。需要的新能力应先以 Protocol/Facade 添加到 `src.extension_api`。

`runtime.workflow_runtime` 只暴露工作流查询、创建任务、运行、停止、任务快照和 Token 汇总；工作流编辑和 Manager 内部状态不属于 Extension API。健康检查返回 `HealthCheckResult`，失败时扩展进入 `degraded`，不会拖垮非严格模式下的 Core。降级扩展的 Prompt Context 与 Session Hook 不会执行。

Plugin 源文件使用本地资源 ID。Host 会按 Manifest 默认 Prefix 或安装时覆盖值构建
显式映射和运行快照；不要在代码中自行拼接、裁剪 Prefix。跨资源引用使用：

```python
runtime.resolve_resource("prompt", "writer")
runtime.resolve_resource(
    "workflow",
    "build",
    plugin_id="workflow-provider",
)
```

跨 Plugin owner 必须先列入当前 Manifest 的 `dependencies`，否则解析失败关闭。
Prompt 正文、脚本正文、Workflow node ID、公开 API 与数据库标识不参与自动改写。
Prefix 只在最终资源 ID 和 Plugin 详情中弱展示。

只有完成可选 lifecycle、`start()` 和全部健康检查、进入 `running` 后，Host 才注册该 Extension 的 Tool，并开放路由、中间件和资源层。Workflow Node 属于 Core，不是 Plugin Extension API。依赖失败时下游进入 `blocked`；注册阶段的 JSON 语法、section 形状、资源 ID 或 Workflow 冲突在非严格模式下只降级责任 Extension。

Skill/Rule Bundle 与 Script Library 是 Plugin 只读资源。Script Task 会冻结并在执行前
核验 Plugin revision 和文件摘要。需要修改时发布新的 Plugin commit，不要从运行时
写入 Plugin checkout。数据库升级使用 Manifest `[lifecycle]` 的幂等
`migrate_command` 与 `verify_command`，不在 `register()` 或模块 import 时执行。

## Prompt 与 Session Hooks

Prompt Context Provider 输入 `PromptContextRequest`，输出 `PromptContribution`。它适合长期记忆、项目上下文或检索增强。

Session Lifecycle Hook 的 `on_session_end(session)` 用于异步归档或 retain。Hook 失败不会阻断 Core shutdown。

## Frontend

`frontend/index.tsx` 默认导出 `FrontendExtension`：

```tsx
const extension = {
  id: "example-tools",
  pages: [{ id: "example", label: "Example", icon: Wrench, component: ExamplePage }],
};

export default extension;
```

Frontend 是 build-time module。新增或删除前端 Extension 后必须重新构建 `web`。

`extension.id` 必须等于 manifest 的 `frontend`，页面 ID 不得与 Core Tab 或其他 Extension 页面重复。模块仅在后端状态为 `running` 时动态加载；加载或契约校验失败会显示在 Extensions 诊断页，不影响 Core 页面。

## 验证

每个 Extension 至少验证：

1. Extension 关闭时 Core 能启动，且不出现扩展路由和工具。
2. Extension 启用时 manifest、资源和依赖能解析。
3. Extension 的数据库、网络或外部服务不可用时状态为 `degraded`，Core 仍可运行。
4. 所有资源 ID 无冲突。
5. Extension 关闭或降级时，历史 Workflow 可读但所有写入和执行入口返回不可用。
6. `python -m pytest -q`、`npm run lint`、`npm run test:extensions` 与 `npm run build` 通过。
