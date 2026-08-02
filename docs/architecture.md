# Core 与 Extension 架构

## 边界

Core 拥有 Agent Runtime、Workflow、Automation、Tool/MCP、Workspace、Prompt 组装机制和 Web Shell。Core 只定义 Extension 可以使用的端口，不包含长期记忆或小说领域实现。

Extension 可以贡献：

- FastAPI Router 与 ASGI Middleware
- Agent Tool Factory 与 Tool Group
- Prompt Context Provider
- Session Lifecycle Hook
- Agent、Prompt、Skill/Rule Bundle、Workflow、Script Library 资源
- Health Check 与 build-time Frontend 页面

Workflow Node 类型由 Core 独占，Extension 只能组合现有 Node 的 Workflow 模板。

## 启动流程

```text
extensions.json / DETERMINFLOW_EXTENSIONS
  -> discover manifests and Python entry points
  -> validate Extension API version
  -> resolve dependencies topologically
  -> build namespaced resource snapshots and validate declarations transactionally
  -> initialize Core services
  -> run forward-only migrate and verify commands
  -> start Extension lifecycle hooks
  -> run Extension health checks
  -> mark Extension running
  -> register Extension tools
  -> reload layered resources from running Extensions
  -> build Agent graphs from active contributions
```

关闭时 Extension 按依赖顺序逆序停止，然后 Core 关闭 Session、Cron 和 MCP 资源。

生命周期状态如下：

| 状态 | 含义 |
|---|---|
| `disabled` | 已发现但未启用 |
| `discovered` / `loaded` | 已进入启用拓扑，声明已通过校验 |
| `starting` | 正在执行启动和健康检查 |
| `running` | 唯一可以激活工具、路由、中间件和资源的状态 |
| `degraded` | 本扩展加载、注册、启动或健康检查失败 |
| `blocked` | 依赖扩展未进入 `running` |

默认的非严格模式会隔离单个 Extension 的错误并让 Core 继续启动；`config/extensions.json` 中的 `strict_startup=true` 用于 CI 或开发期快速失败。注册、迁移、启动或工具安装中断时，Host 会撤销该 Extension 已安装的 Tool，并调用 `stop()` 清理。

## 资源流

输入：Core JSON、启用 Extension 的资源、用户 override。

处理：Host 先用 Plugin Prefix 生成显式资源 ID mapping 和只读运行快照，再由
`LayeredJsonConfig` 按 owner 合并并检测冲突。Skill/Rule Bundle 保留 owner 与只读
来源元数据。

输出：Agent、Prompt、Skill、Rule 和预设短语使用的 resolved config。

修改 Extension 默认资源时，写入 `config/extension-overrides/`。关闭 Extension 后，其默认资源和 override 都不进入 resolved config，但合法 override 会保留并在 Extension 再次运行时恢复；已从 Extension 删除的资源 override 会被清理。

## Workflow 与 Script

Extension Workflow 是不可变模板，启动时 provision 到运行目录。用户修改后的定义不会被 Extension 更新覆盖，运行 Task 继续使用自身 Snapshot。上游已删除且用户未修改的脚本会同步删除；用户修改过的脚本会保留，并在 `.extension.json` 的 `orphaned_files` 中记录。

只有 owner 处于 `running` 的 Workflow 才能创建、编辑或执行。Extension 不可用时仍可读取既有 Task 和 Run 历史，避免故障期间丢失诊断入口。

### 节点失败恢复

失败恢复属于 Core 编排能力，与 Agent、Script、Approval 或 Subprocess 插件实现无关。每个节点可配置首次失败后的 `auto_retry_count`、固定 `auto_retry_interval_seconds` 和重试耗尽后的 `fail_auto_skip`。默认均关闭；自动重试最多 20 次、间隔最多 86,400 秒。重试复用原 Task 的 definition snapshot（定义快照）、参数、workspace（工作空间）和首次冻结的节点输入，已完成节点、并行分支与循环迭代不会重跑。Subprocess 内部状态继续保留在父节点 `child_states`，由父 Subprocess 节点负责恢复调度。

失败节点详情提供原地“重试”和“跳过”。两个 mutation 都要求客户端提交当前 `expected_attempt_count`，用 CAS（比较并交换）拒绝过期或并发操作；对应接口为 `POST /api/workflows/{workflow_id}/tasks/{task_id}/nodes/{node_id}/retry|skip`。自动等待使用持久化 `retry_waiting`，人工操作或进程恢复使用 `resume_pending`，启动恢复器会继续到期重试和中断中的任务。每次尝试保留 trigger、时间、Session 与错误历史，累计 Token 不因重试清零。该能力提供 at-least-once（至少一次）执行保证；有外部副作用的插件仍必须自行保证幂等。

Script Library 使用按 owner 合并的只读 Plugin 目录；重复 `(group, script)` 会在
启动时拒绝。Task 创建时冻结 owner、revision、entrypoint 与文件摘要，执行前再次
核验，避免 Plugin 更新或文件漂移改变已创建 Task 的执行代码。

## 前端

Core Web Shell 不打包官方 Plugin 的产品前端。可安装 Plugin 需要轻量可视化配置时，
使用 manifest `[page]` 声明的静态页面并在 Plugin 管理详情中加载；常规配置优先使用
Core 根据 `settings.schema.json` 生成的通用表单。复杂产品工作台保持独立部署，
通过稳定的 Plugin API 与 Core 集成。

仓库内共同开发的本地 Extension 仍可由 Vite 在构建时发现
`extensions/*/frontend/index.tsx`，但不会预先执行模块。浏览器请求
`/api/extensions` 后只动态加载后端处于 `running` 的页面和 Agent Editor
contribution，并拒绝 Extension ID、页面 ID 或 Core Tab ID 冲突。

Core 的 Extensions 页面展示 manifest、依赖、能力、运行状态和降级原因；第一版不提供运行时启停。

Plugin 静态页面不作为安全沙箱；Plugin Backend 与 Core 同进程、同权限运行。
