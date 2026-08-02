---
name: workflow-guide
description: '当用户指示任何涉及工作流的操作时务必加载此技能——包括创建/编辑工作流、启动任务、 填充变量、审批节点、查看状态、排查工作流显示或执行问题。工作流是预定义的多节点
  顺序执行流水线，数据存储在 data/workflows/ 目录下。

  '
metadata:
  display_name: workflow-guide
  version: 2.6.0
  author: system
  category: general
  priority: 50
  workflow_only: false
---

# 工作流使用指南

## 使用前提

创建或修改工作流之前，先确认这三件事：

1. **工作流 ID** 格式为 `wf-{8位hex}`，需自行确保全局唯一
2. **agent_type 不能硬编码** — 先调 `list_agent_types` 获取当前可用的子会话类型列表，再从中选择填入。因为 agent 类型可能被用户增删改，写死的值随时失效
3. **每个节点必须有 `position` 字段** — 前端 ReactFlow 画布依赖坐标渲染。缺失时所有节点落到默认坐标 `{x:100, y:100}`，视觉上只能看到最上层那一个

## 数据模型

工作流核心是 `data/workflows/{workflow_id}/definition.json`，引擎启动任务时从此文件加载完整定义。

```
data/workflows/{workflow_id}/
├── definition.json    # 节点、边、变量定义（你维护）
├── script/            # 脚本执行节点的脚本文件（你维护，如 deploy.py）
├── tasks/             # 任务实例（引擎自动生成）
└── runs/              # 执行记录（引擎自动生成）
```

### 节点类型

| node_type | 行为 | 关键字段 |
|-----------|------|---------|
| `"agent"` | LLM 子会话执行 | `agent_type`, `first_message`, `system_prompt_template`, `output_variable`, `auto_flow`, `enable_complete_node_task`, `save_output_to_file`, `output_file_path` |
| `"script"` | subprocess 执行脚本（非 LLM） | `node_params: {script_type, script_name, script_argv, timeout}` |
| `"approval"` | 审批驳回标记（不创建子会话） | 无特殊字段 |
| `"subprocess"` | 嵌套子流程 | `sub_workflow_id`, `sub_scheme_id`, `sub_workflow_params` |

### Agent 节点（node_type: "agent"）

```json
{
  "id": "agent_xxx",
  "label": "节点标签",
  "node_type": "agent",
  "agent_type": "subagent",
  "position": {"x": 300, "y": 100},
  "system_prompt_template": "注入到子 Agent 基础 prompt 之上的指令",
  "first_message": "{{var_key}}",
  "output_variable": "",
  "auto_flow": false,
  "enable_complete_node_task": true,
  "save_output_to_file": false,
  "output_file_path": "",
  "var_bindings": {
    "first_message": {
      "original_value": "写第{{chapter}}章，题材{{genre}}",
      "var_key": "node_xxx_first_message"
    }
  }
}
```

**字段说明：**

- `position` — 画布渲染坐标，必须设置。y 轴按执行顺序递增（100, 200, 300...），并行分支节点 x 坐标错开
- `agent_type` — 子会话类型，先查 `list_agent_types` 再填入
- `first_message` — 首条任务消息，支持 `{{key}}` 占位符
- `system_prompt_template` — 注入到子 Agent 基础 prompt 之上的额外指令
- **`output_variable`** — 输出变量 key（v2 新增）。Agent 完成后，最后一轮回复文本写入此变量，下游节点通过 `{{key}}` 引用。空字符串表示不捕获
- **`auto_flow`** — 自动流转（v2 新增）。设为 true 时，Agent 无需调用 complete_node_task，LLM 输出完成即视为成功
- **`enable_complete_node_task`** — 是否注入 complete_node_task 工具（v2 新增）。默认 true。设为 false 配合 auto_flow=true 使用，Agent 完全不知道自己在工作流中
- **`save_output_to_file`** — 保存输出到文件（v4 新增）。设为 true 时，Agent 最后一轮回复文本保存到 `output_file_path` 指定的路径。**与 `output_variable` 独立，可同时开启**
- **`output_file_path`** — 保存路径（v4 新增）。仅 `save_output_to_file=true` 时生效。支持三种形式：
  - **绝对路径**：仅允许位于当前 workspace 内；推荐优先使用相对路径
  - **相对路径**：如 `result/chapter.md`，基于 workspace 根目录（含 `workspace_override` 覆盖）
  - **`{{key}}` 占位符**：如 `{{output_dir}}/result.txt`，运行时解析
  - 文件已存在时**直接覆盖**并记录警告日志；占位符无法解析或 IO 错误时节点标记为 failed
- `var_bindings` — 记录哪个全局变量填入节点的哪个字段

### 脚本节点（node_type: "script"）

**不创建子会话**，在 workflow-main 所在进程内通过 subprocess 直接执行 shell/python 脚本。完全确定性，零 LLM 幻觉。支持两种脚本来源：**本地直接编辑**和**脚本库引用**。

```json
{
  "id": "script_xxx",
  "label": "脚本节点标签",
  "node_type": "script",
  "position": {"x": 300, "y": 200},
  "node_params": {
    "script_source": "inline",
    "script_type": "python",
    "script_name": "anonymize",
    "script_argv": ["--verbose", "--title", "{{book_title}}"],
    "timeout": "300"
  }
}
```

| 参数 | 说明 |
|------|------|
| `script_source` | `"inline"`（本地编辑，默认）或 `"library"`（脚本库引用） |
| `script_type` | `"shell"` 或 `"python"` |
| `script_name` | 脚本文件名（不含扩展名） |
| `script_group` | 脚本库分组名（仅 `script_source="library"` 时需要） |
| `script_argv` | 推荐。字符串数组，每项作为一个完整 argv 参数传递，支持 `{{key}}` 占位符；含空格、引号或 JSON 的变量不会被 Shell 重新拆分 |
| `script_args` | 仅兼容历史定义的参数字符串；新建和修改工作流不要使用 |
| `timeout` | 超时秒数，默认 300 |

**脚本来源说明：**

- **`inline`（直接编辑）**：脚本文件存储在 `data/workflows/{workflow_id}/script/{name}.{ext}`，在节点配置面板内嵌编辑器直接编写。
- **`library`（脚本库）**：脚本文件存储在 `data/script-library/{group}/{name}/{name}.{ext}`，运行时拉取最新版本执行。脚本库可通过工作流页面「脚本库」页签管理。

**执行环境：**
- 引擎直接以 argv 启动脚本，不经过 Shell；参数必须优先写入 `script_argv`
- 工作目录 = 工作流共享 workspace（`data/workspaces/{workflow_id}/`）
- 环境变量：`WORKFLOW_ID`, `TASK_ID`, `SCRIPT_DIR`, `WORKSPACE_DIR`
- stdout/stderr 截断 50KB

**共享 workspace（重要）：** Script 节点和 Agent 节点统一使用共享 workspace 根目录作为主工作目录，不再为 Agent 节点创建私有 session workspace。Agent 通过 `write_to_file` 写入的文件直接落在共享目录中，下游 Script/Agent 节点可直接读取，无需手动指定路径。

默认共享 workspace 路径为 `data/workspaces/{workflow_id}/`。可通过 `workspace_override` 覆盖为任意目录（绝对路径或相对于项目根目录的相对路径），覆盖后 Agent 的工作目录、file 变量的解析基点、`write_to_file`/`read_file` 的相对路径都切换到新目录。这对多本书/多项目复用同一管线至关重要。

**工作空间覆盖（v4 新增）：** 启动任务时可传入 `workspace_override` 参数覆盖默认路径。支持绝对路径（如 `/home/user/my-project`）或相对路径（相对项目根目录，如 `data/book/hgdafjkh`）。路径不存在时自动创建。覆盖后的实际路径可通过 `{{_system.workspace_path}}` 获取。覆盖值持久化在 `WorkflowTask.workspace_override` 中。

**变量产出（核心机制）：**
- `<WF_VAR>key:value</WF_VAR>` — stdout 中输出此行，自动写入运行时变量池，下游 `{{key}}` 立即可引用
- `<script_out>摘要内容</script_out>` — 提取为节点 summary

**脚本节点不触发审批**，执行完自动流转。

### 变量系统

六种类型，启动时一次性渲染，运行期不可改：

| 类型 | 用途 | 特殊字段 |
|------|------|---------|
| `text` | 单行自由文本 | — |
| `textarea` | 多行文本段，支持换行 | — |
| `select` | 下拉选择 | `options: [{"name": "展示名", "value": "填充值"}]` |
| `file` | 文件路径，解析为文件内容 | `default`: 默认文件路径 |
| `list` | JSON 数组，供循环网关遍历 | `default`: 合法 JSON 数组，如 `["a","b","c"]` |
| `dict` | JSON 对象，供循环网关遍历 | `default`: 合法 JSON 对象，如 `{"k":"v"}` |

**list/dict 引用语法**（v2.3 新增）：
- `{{list_var[0]}}` — 取列表第一个元素
- `{{list_var[idx]}}` — 取列表指定索引（idx 可以是数字或变量引用）
- `{{dict_var.key}}` — 取字典指定键的值

**运行时类型推断**：节点运行时产出的变量（Agent output_variable、Script `<WF_VAR>`）均为字符串。当通过 `{{var[idx]}}` 或 `{{var.key}}` 引用时，引擎自动尝试 `json.loads()` 解析：
- 解析为 list → 索引访问
- 解析为 dict → 键访问
- 解析失败 → 报错（单行字符串不允许索引访问）

**file 类型变量**填入的文件路径：
- 以 `/` 开头 → 绝对路径
- 否则 → 相对路径，拼接工作流共享 workspace（`data/workspaces/{workflow_id}/`）
- 节点执行时读取文件内容，替代 `{{key}}` 占位符
- `required: true` 时文件不存在会报错，`required: false` 时替换为空字符串

**变量间的嵌套引用**（所有类型通用）：
- `{{key}}` 占位符支持嵌套，如 file 变量的路径可写 `story/{{chapter_num}}/outline.md`
- 引擎先展开所有嵌套引用，再读取 file 变量，最后替换节点字段中的占位符
- 循环引用（A→B→A）会触发错误

**三类变量来源：**

1. **用户全局变量** — 手动定义的业务参数。`source_type: "input"`，启动任务时填入
2. **节点消息变量** — 前端自动生成，key 格式 `{node_id}_first_message`
3. **节点产出变量** — 运行时自动填充，又分两种：
   - `source_type: "output"` + `source_node_id` — 在变量定义中声明，引擎自动从 `node_states[node_id].outputs` 读取
   - Agent 节点 `output_variable` — 直接写入 `parameter_values`，无需预定义变量
   - 脚本节点 `<WF_VAR>` — 直接写入 `parameter_values`

**变量传递链（重要）：**
```
节点A 产出 outputs → 写入 parameter_values → 节点B first_message 中 {{key}} 引用取值
```
这意味着上游节点的产出可以被下游节点**实时引用**，不需要写文件。

未填充的变量保留为字面量 `{{key}}`。

### 系统变量（v4 新增：_system.xxx）

引擎在每个节点执行时自动注入以下系统变量到 `parameter_values`，供节点模板中通过 `{{_system.xxx}}` 引用。用户不可创建同前缀（`_` 开头）的变量。

| 变量 Key | 值示例 | 说明 |
|---------|--------|------|
| `_system.workspace_path` | `/home/user/determinflow/data/workspaces/wf-xxx/` | 实际工作空间路径（含覆盖） |
| `_system.workflow_id` | `wf-8d6b785f` | 工作流 ID |
| `_system.task_id` | `task-5aeece34` | 任务 ID |
| `_system.task_name` | `novel_20260601_113000` | 任务名称 |
| `_system.current_time` | `2026-06-01T11:30:00+08:00` | 当前时间 ISO 8601（**动态**，每节点执行时重新计算） |
| `_system.operator` | `system` | 执行人（硬编码，预留认证扩展） |

**使用示例：**
```json
{
  "first_message": "在 {{_system.workspace_path}} 目录下执行任务，工作流 {{_system.workflow_id}}，当前时间 {{_system.current_time}}"
}
```

### 执行方案（v5 新增）

执行方案保存一种选定节点的快捷方式，可在创建任务时快速复用，也可在子流程节点和 API 执行时传入。方案存储在 `definition.json` 的 `execution_schemes` 数组中。

**数据模型：**

```json
{
  "execution_schemes": [
    {
      "id": "scheme-a1b2c3d4",
      "name": "仅生成核心世界观",
      "selected_node_ids": ["agent_corelaws", "script_save_corelaws"],
      "created_at": "2026-06-01T19:00:00+08:00",
      "updated_at": "2026-06-01T19:00:00+08:00"
    }
  ]
}
```

每个方案只存储**选中执行的节点 ID**。引擎执行时：`disabled_node_ids = 全量节点 - 选中节点`，被禁用的节点在遍历时跳过。没被选中的节点不会执行。

**创建任务时指定执行节点：**

`POST /api/workflows/{id}/tasks` 支持三种入参（按优先级）：

| 入参 | 效果 | 使用场景 |
|------|------|---------|
| `selected_node_ids: [...]` | 直接传选中节点列表 | 前端手动勾选或方案修改后 |
| `scheme_id: "scheme-xxx"` | 使用已有方案 | 选中方案且未手动调整 |
| `disabled_node_ids: [...]` | 直接传禁用节点列表 | 兼容旧行为 |

前端逻辑：选中方案后没动节点 → 传 `scheme_id`；动了节点 → 方案标记"已修改"，传 `selected_node_ids`。

**在 API 直接调用时使用方案：**

```
POST /api/workflows/{id}/tasks
{
  "scheme_id": "scheme-a1b2c3d4",
  "parameter_values": {"genre": "东方玄幻"}
}
```

**子流程节点使用方案：**

子流程节点新增 `sub_scheme_id` 字段，指定目标流程中要使用的执行方案。不填则默认执行子流程全部节点：

```json
{
  "id": "node_sub",
  "node_type": "subprocess",
  "sub_workflow_id": "wf-nvl-build",
  "sub_scheme_id": "scheme-a1b2c3d4",
  "sub_workflow_params": {}
}
```

子流程节点不能手动调整节点 —— 必须选一个方案或者选"全部执行"。

**方案 CRUD API：**

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/workflows/{id}/schemes` | 列出所有方案 |
| `POST` | `/api/workflows/{id}/schemes` | 创建方案 |
| `PUT` | `/api/workflows/{id}/schemes/{sid}` | 更新方案（名称或节点列表） |
| `DELETE` | `/api/workflows/{id}/schemes/{sid}` | 删除方案 |

创建方案时后端会校验选中节点 ID 是否有效，无效节点会返回 400 错误。创建/更新/删除方案都会递增 `version`。

### 自定义变量块（v3 新增：Template Variables）

🔴 **核心认知纠正：section content 支持 `{{}}` 变量渲染——但仅限于已声明的自定义变量块和系统变量。**

如果 Agent 的 prompt template 声明了自定义变量块（`template_variables`），节点配置时自动展示对应的输入框（在编排页面的"提示词 Sections"→"自定义变量块"编辑器）：

```json
{
  "id": "agent_xxx",
  "agent_type": "novel-writer",
  "node_params": {
    "template_values": {
      "planning_section": "第一章要写主角回到老家...",
      "voice_block": "写作姿态：把自己当成亲历者..."
    }
  }
}
```

**动作原理：**
1. `prompts_config.json` 中 prompt template 声明 `template_variables`（如 planning_section）
2. Agent 节点执行时从 `node_params.template_values` 读取
3. 变量块值中的 `{{key}}` 占位符会被 workflow 变量池解析
4. 最终注入到 prompt section content 的 `{{planning_section}}` 位置

**两类占位符区分：**
- **系统变量**（如 `{{session_meta}}`, `{{workflow_overview}}`）— 由系统注册中心 `src/prompts/system_variables.py` 管理，自动解析，用户不可配置
- **自定义变量块**（如 `{{planning_section}}`）— 在 `template_variables` 中声明，节点配置时填写

**编排页面：** 编排页面的"提示词 Sections"标签中，选择自定义模板后可看到"自定义变量块"编辑器，支持增删改声明。

### 并行网关

`gateways` 数组支持两种网关，实现一层并行分支：

| gateway_type | 作用 | 连线规则 |
|-------------|------|---------|
| `parallel` | 分叉点，所有出边分支同时执行 | ≥ 2 条出边，出边不能直接连 `converge` |
| `converge` | 收束点，等所有入边分支完成后继续 | ≥ 2 条入边，恰好 1 条出边 |

- `converge_gateway_id` 后端自动推导，不用手动填
- 分支间并行，分支内串行。仅支持一层
- 任一分支失败 → 任务整体失败
- 汇聚后所有分支 summary 拼接注入下游

### 条件网关 / 循环（v2 新增）

`gateway_type: "condition"` 支持条件分支和循环。

**条件分支**：出边携带 `condition` 字段，运行时按顺序评估，命中第一个 true 的分支。

```json
{
  "id": "gw-cond-1",
  "gateway_type": "condition",
  "label": "条件网关",
  "position": {"x": 300, "y": 200}
}
```

**出边 condition：**
```json
{
  "source": "gw-cond-1",
  "target": "node_high",
  "condition": {
    "expression": "{{score}} > 80",
    "label": "高分",
    "is_default": false
  }
}
```
- `expression` — 支持 `==`, `!=`, `>=`, `<=`, `>`, `<`, `contains`
- `is_default: true` — 默认分支（必须有一条，兜底）
- 条件网关至少 2 条出边，至少一条默认分支

**循环**：条件网关出边指向上游已执行过的节点 → 自动识别为回环。

```
START → node_a → gw-cond ──[continue]──→ node_a（回环）
                          ──[exit]────→ node_b → END
```

### 循环网关（v2.3 新增）

`gateway_type: "loop"` 提供直观的 for-each 循环，循环语义通过**出边条件表达式**定义（点击边打开编辑器设置）。

**网关定义（无额外字段，和并行/汇聚网关一样）：**
```json
{
  "id": "gw-loop-1",
  "gateway_type": "loop",
  "label": "循环网关",
  "position": {"x": 300, "y": 200}
}
```

**三种循环表达式语法**（写在 continue 出边的 `condition.expression` 字段中）：

| 表达式 | 模式 | 迭代变量 | 循环体内引用 |
|--------|------|---------|------------|
| `for item in chapters` | 列表 | `item` | `{{item}}` → 当前元素 |
| `for key, value in config` | 字典 | `key`, `value` | `{{key}}`, `{{value}}` |
| `for i in range(5)` | 次数(0..4) | `i` | `{{i}}` → 0,1,2,3,4 |
| `for i in range(1, 5)` | 次数(1..4) | `i` | `{{i}}` → 1,2,3,4 |

**出边规则：**
- 恰好 2 条出边：
  - **非默认边（continue）**：循环体边，expression 写循环表达式。执行完循环体后回到网关
  - **默认边（exit）**：退出边，`is_default: true`。循环结束后走此分支
- 循环体内支持任意编排（多节点、并行网关、条件网关），但不嵌套循环网关

**连线拓扑：**
```
START → node_a → gw-loop ──[for item in list]──→ body_node ──→ gw-loop（回环）
                           ──[默认/exit]───────→ next_node → END
```

**出边 condition 示例：**
```json
{
  "source": "gw-loop-1",
  "target": "body_node",
  "condition": {
    "expression": "for item in chapters",
    "label": "循环体",
    "is_default": false
  }
}
```

**迭代变量生命周期**：每轮迭代引擎自动将当前值写入 `parameter_values`，循环体内所有节点可直接引用。迭代结束走 exit 分支后迭代变量仍保留最后一轮的值。

**变量值要求**：列表/字典变量必须是合法 JSON：
- ✅ `["章节一","章节二","章节三"]` — 双引号 JSON 字符串数组
- ✅ `[1,2,3]` — JSON 数字数组
- ❌ `['a','b','c']` — 单引号不是合法 JSON
- ❌ `[a,b,c]` — 无引号不是合法 JSON
- ✅ `{"name":"Alice","age":30}` — JSON 对象

**脚本节点产出 list/dict**：通过 `<WF_VAR>` 标签输出时，值必须是合法 JSON：
```
<WF_VAR>chapters:["章节一","章节二"]</WF_VAR>
<WF_VAR>config:{"name":"Alice","age":30}</WF_VAR>
```
注意 `<WF_VAR>` 必须**大写**，且值是**双引号** JSON。

## 创建与编辑

没有专用创建 API，直接写入文件：

```
write_to_file(
  path="data/workflows/wf-myid/definition.json",
  content="完整的 JSON"
)
```

- 写完用 `list_workflows` 验证可见
- 编辑已有工作流直接覆盖文件，递增 `version`。task 创建时 snapshot 当前 definition，之后改 definition **不影响已创建的 task**，需重新 `create_and_attach_task`
- tasks/ 和 runs/ 目录由引擎自动创建，不用手动建
- 脚本节点：脚本文件放入 `data/workflows/{workflow_id}/script/`

### 🔴 每次直接编辑 definition.json 后必须运行校验脚本

当你（main agent）通过 `write_to_file` / `apply_diff` / `replace_in_file` 直接修改了 `definition.json` 后，**必须立即运行**：

```bash
python data/skills/workflow-guide/scripts/validate_definition.py <定义文件或目录>
```

**示例：**
```bash
# 校验单个文件
python data/skills/workflow-guide/scripts/validate_definition.py data/workflows/wf-nvl-build/definition.json

# 传入目录也行
python data/skills/workflow-guide/scripts/validate_definition.py data/workflows/wf-nvl-build/

# 校验全部工作流
python data/skills/workflow-guide/scripts/validate_definition.py --all
```

**校验脚本覆盖的规则（与后端 `WorkflowDef.validate()` 一致）：**

| 类别 | 规则 | 级别 |
|------|------|------|
| 结构 | JSON 语法、顶层字段完整性 | ERROR |
| 变量 | key 不能以 `_` 开头（系统变量保留） | ERROR |
| ID 唯一性 | 节点/网关 ID 不能重复 | ERROR |
| ID 冲突 | 网关 ID 不能与节点 ID 重叠 | ERROR |
| 边引用 | source/target 必须指向存在的节点或网关 | ERROR |
| 子流程 | subprocess 节点必须指定 sub_workflow_id | ERROR |
| 拓扑-起点 | START 必须有出边 | ERROR |
| 拓扑-终点 | 必须有边连接到 END | ERROR |
| 拓扑-路径 | START→END 完整可达，无断链 | ERROR |
| 拓扑-孤立 | 所有节点必须在连线上 | ERROR |
| 拓扑-多出边 | 普通节点不能有 >1 条出边（需并行网关） | ERROR |
| 并行网关 | ≥2 出边；出边不能直接连汇聚网关 | ERROR |
| 汇聚网关 | ≥2 入边；恰好 1 出边 | ERROR |
| 并行配对 | 每个并行网关必须有对应汇聚网关 | ERROR |
| 嵌套并行 | 不支持并行网关内再嵌套并行网关 | ERROR |
| 条件网关 | ≥1 入边；≥2 出边；必须有默认分支 | ERROR |
| 条件表达式 | 非默认分支的 expression 不能为空 | ERROR/WARNING |
| 循环网关 | ≥1 入边；恰好 2 出边；必须有默认(退出)分支 | ERROR |
| 嵌套循环 | 循环体内不能包含另一个循环网关 | ERROR |
| 循环表达式 | 格式检查 (for item in list / range(N) 等) | WARNING |
| 变量引用 | 引用但未定义的变量 | WARNING |
| 变量引用 | 定义但未被引用的 input 变量 | WARNING |
| 位置字段 | 节点/网关缺少 position | WARNING |
| 标签 | 节点缺少 label | WARNING |

**退出码：** 0 = 通过（含 warning 也返回 0），1 = 有 ERROR 需要修复。

**重要：** 不要仅依赖前端的保存校验——直接用编辑器改 JSON 文件绕过了前端校验，必须手动跑脚本。后端虽然也有 validate，但那是 API 层面的；直接写文件时后端感知不到。

## 执行流程

```
create_and_attach_task → set_workflow_variable (可多次) → start_workflow_task
         ↓                                                      ↓
   绑定到当前 main session                                引擎按 edges 依次执行节点
                                                                   ↓
                                                            每节点完成 → 审批 → 下一节点
```

### 审批

每个 Agent 节点完成后 main Agent 收到审批通知，通过 `approve_node` 回复。上限 3 次拒绝。

**脚本节点不触发审批** — 不创建子会话，执行完自动流转。

## 节点执行细节

1. 引擎按 edges 遍历生成执行计划（含网关调度）
2. **Agent 节点**：创建子会话 → 注入 workflow-only sections（`sub_execution_workflow` + `upstream_summary`）→ 渲染 first_message → 发给子 Agent → 调 complete_task → 触发审批
3. **脚本节点**：直接 subprocess 执行 → 解析 `<WF_VAR>` → 产出变量写入池 → 自动流转
4. **变量传递**：节点完成后 outputs 写入 `parameter_values`，下游 `{{key}}` 立即可引用

**上游信息传递：**
- `upstream_summary` 自动携带上一节点摘要
- **变量池是节点间数据传递的核心机制** — 比文件传递更可靠，不需要担心路径问题
- 文件产出物不自动传递，下游需被告知路径

## 查看与管理

| 工具 | 用途 |
|------|------|
| `list_workflows` | 列出所有工作流 |
| `get_workflow(id)` | 查看完整 definition |
| `get_task_status` | 查看任务和节点进度 |
| `list_tasks` | 任务历史 |
| `stop_task` | 停止运行中的任务 |

## 常见陷阱

**空 first_message：** 模板变量全部为空时子 Agent 收到空白任务。确保每个节点要么有写死的 `original_value`，要么变量被正确填充。

**变量不渲染：** `parameter_values` 中缺少模板引用的 key 时，`{{key}}` 保留为字面量。

**definition 与 task 不同步：** task 创建时 snapshot 当前 definition，之后改 definition 不影响已创建的 task。改完后需重新 `create_and_attach_task`。

**节点位置缺失：** 所有节点重叠时，检查 `position` 字段。

**并行分支节点位置重叠：** x 坐标错开（如 200 和 400）。

**file 变量文件不存在：** 上游节点可能还未产出文件时，下游 file 变量应设为 `required: false`。

**节点间文件传递：** Agent 和 Script 节点统一使用共享 workspace 根目录（默认为 `data/workspaces/{workflow_id}/`，可通过 `workspace_override` 覆盖），Agent 写入的文件可直接被下游节点读取。如需传递文本内容，也可以用 `output_variable` 或 `<WF_VAR>` 自动捕获为变量，绕过文件路径。

**提示词中的绝对路径：** Agent 的提示词 section（如 `file_structure`）中应避免写死绝对路径。应使用相对路径（如 `story/{N}/chapter.md`），因为 Agent 的工作目录就是共享 workspace。如果写了绝对路径，`workspace_override` 切换后 Agent 仍会读写旧位置。

**section 中 `{{}}` 什么时候渲染：** section content 中的 `{{key}}` 占位符，只有两类会被替换执行——① 系统变量（`{{session_meta}}` 等，由 `system_variables.py` 注册）；② 在 prompt template 的 `template_variables` 中声明的自定义变量块，其值通过节点 `node_params.template_values` 传入。未声明的自定义 `{{key}}` 会保留为字面量，不会报错。

**脚本节点是确定性执行的：** 需要"非 LLM"处理的步骤（如字符串替换、格式转换）优先用脚本节点，零幻觉。

**系统变量 key 不可手动创建：** 变量 key 不能以下划线 `_` 开头，此项在后端保存时校验拒绝。系统变量 `_system.xxx` 由引擎自动注入，无需手动定义。

**工作空间覆盖路径选择：** 多个工作流的 `workspace_override` 指向同一目录时会共享文件，这是预期行为。如需隔离，可在覆盖路径下使用不同的子目录。

**循环网关找不到变量：** 循环表达式 `for item in x` 中的变量名 x 必须存在于 `parameter_values` 或已完成的节点 outputs 中。检查变量 key 是否拼写正确，前驱节点是否已产出该变量。

**循环网关 JSON 格式错误：** list/dict 变量值必须是**双引号** JSON。单引号 `['a']`、无引号 `[a]` 都会导致 `json.loads()` 失败，任务直接 failed。脚本节点检查 `<WF_VAR>` 的大小写——必须大写。

**循环网关出边数量：** 恰好 2 条出边（一条循环体，一条退出），多或少都会保存校验失败。退出边必须标记 `is_default: true`。

**嵌套循环不支持：** 循环网关的循环体内不能再放置另一个循环网关。如需嵌套，用子流程（subprocess）节点实现。

**循环网关 vs 条件网关回环：** 循环网关适合"遍历数据源"（for-each），条件网关回环适合"条件不满足就重试"（while）。不要混用。
