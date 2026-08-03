"""
Workflow Prompt Injector — 将 WorkflowDef 转为 LLM 友好的系统提示词注入文本。

通过 prompts_config.json 的 3 个 workflow_only section 注入：
1. workflow_overview — 任务概览及职责
2. workflow_structure — 结构化工作流信息（节点、变量等）
3. workflow_definition_json — 原始 definition.json
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .definition import WorkflowDef


def build_workflow_overview(defi: "WorkflowDef") -> str:
    """构建工作流概览 + 职责说明。"""
    parts: list[str] = []

    parts.append(f"- 名称: {defi.name}")
    parts.append(f"- 工作流 ID: {defi.workflow_id}")
    parts.append(f"- 节点数: {len(defi.nodes)}")
    parts.append(f"- 全局变量数: {len(defi.variables)}")
    parts.append("")

    parts.append("### 你的职责")
    parts.append(
        "你是此工作流的主控制者（Main Agent）。你的职责包括：\n"
        "1. 对任务执行修改、启动、审批和恢复时始终携带明确的 workflow_id 与 task_id\n"
        "2. 帮助用户理解和填写全局变量，使用 `set_workflow_variable` 设置任务变量\n"
        "3. 用户确认后，使用 `start_workflow_task` 在后台启动任务\n"
        "4. 节点完成后会发送包含 TaskRef 与 attempt_count 的审批请求；原样携带这些字段调用 `approve_node`\n"
        "5. 如果拒绝节点产出，该节点会携带你的反馈回滚重试（最多 3 次）"
    )

    return "\n".join(parts)


def build_workflow_structure(defi: "WorkflowDef") -> str:
    """构建节点执行顺序 + 全局变量定义。"""
    parts: list[str] = []

    # ── 节点执行顺序 ──
    parts.append("### 节点执行顺序")
    execution_order = _get_execution_order(defi)
    for i, node_id in enumerate(execution_order, 1):
        node = defi.get_node(node_id)
        if node is None:
            continue
        first_msg = node.first_message or "(无)"
        sp = node.system_prompt_template or "(无)"
        parts.append(f"{i}. [{node.id}] {node.label}")
        parts.append(f"   - 节点类型: {node.node_type}")
        if node.node_type == "agent":
            parts.append(f"   - Agent 类型: {node.agent_type}")
            parts.append(f"   - 任务消息: {first_msg}")
            parts.append(f"   - System Prompt 补充: {sp}")
        elif node.node_type == "approval":
            file_paths = node.node_params.get("file_paths", "")
            parts.append(f"   - 需审查文件: {file_paths or '(无)'}")
    parts.append("")

    # ── 全局变量 ──
    if defi.variables:
        parts.append("### 全局变量")
        parts.append("以下是工作流的全局变量定义。你可以通过 `set_workflow_variable` 工具修改这些变量的值，修改后用户左侧表单会实时更新。")
        parts.append("")
        for v in defi.variables:
            type_map = {"text": "文本", "select": "下拉选择", "file": "文件"}
            type_tag = type_map.get(v.type, v.type)
            req_tag = "必填" if v.required else "非必填"
            parts.append(f"- **{v.key}** ({type_tag}, {req_tag}): {v.name}")
            if v.description:
                parts.append(f"  说明: {v.description}")
            if v.default:
                parts.append(f"  默认值: \"{v.default}\"")
            if v.type == "select" and v.options:
                opts = ", ".join(f"{o['name']}({o['value']})" for o in v.options)
                parts.append(f"  可选值: {opts}")
        parts.append("")
    else:
        parts.append("### 全局变量")
        parts.append("此工作流没有定义全局变量。")
        parts.append("")

    return "\n".join(parts)


def build_workflow_definition_json(defi: "WorkflowDef") -> str:
    """构建原始 definition.json。"""
    parts: list[str] = []

    parts.append("以下是完整的 workflow definition.json，仅供参考：")
    parts.append("```json")
    parts.append(json.dumps(defi.to_dict(), ensure_ascii=False, indent=2))
    parts.append("```")

    return "\n".join(parts)


def build_workflow_summary_for_approval(
    defi: "WorkflowDef",
    task_id: str,
    node_id: str,
    summary: str,
    attempt_count: int,
) -> str:
    """构建发送给 main 的审批请求消息。"""
    node = defi.get_node(node_id)
    node_label = node.label if node else node_id
    next_id = defi.get_next_node_id(node_id)
    next_node = defi.get_node(next_id) if next_id else None
    next_label = next_node.label if next_node else "END"

    return (
        f"## 节点审批请求\n\n"
        f"节点 **{node_label}** (`{node_id}`) 已完成任务，请审批其产出。\n\n"
        f"**TaskRef：** `workflow_id={defi.workflow_id}`, `task_id={task_id}`\n"
        f"**执行尝试：** `attempt_count={attempt_count}`\n\n"
        f"**产出摘要：**\n{summary}\n\n"
        f"**下一个节点：** {next_label} (`{next_id or '无'}`)\n\n"
        f"请调用 `approve_node` 工具做出决定：\n"
        f"- 通过：`approve_node(workflow_id=\"{defi.workflow_id}\", task_id=\"{task_id}\", node_id=\"{node_id}\", expected_attempt_count={attempt_count}, approved=true)`\n"
        f"- 拒绝并反馈：`approve_node(workflow_id=\"{defi.workflow_id}\", task_id=\"{task_id}\", node_id=\"{node_id}\", expected_attempt_count={attempt_count}, approved=false, feedback=\"你的意见\")`"
    )


def _get_execution_order(defi: "WorkflowDef") -> list[str]:
    """计算节点的串行执行顺序（排除 __start__ / __end__）。"""
    if not defi.nodes:
        return []
    agent_nodes = [n for n in defi.nodes
                   if not (n.id.startswith("__") and n.id.endswith("__"))]
    if not agent_nodes:
        return []
    if not defi.edges:
        return [n.id for n in agent_nodes]

    # 找起始节点
    all_sources = {e.source for e in defi.edges}
    all_targets = {e.target for e in defi.edges}
    start_nodes = all_sources - all_targets

    order: list[str] = []
    if start_nodes:
        current = next(iter(start_nodes))
    else:
        current = agent_nodes[0].id

    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        if current in {n.id for n in agent_nodes}:
            order.append(current)
        current = defi.get_next_node_id(current)
    return order
