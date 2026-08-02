"""提示词动态占位符工具。"""
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.session import AgentSession


def render_prompt_template(content: str, values: dict[str, str]) -> str:
    """替换 {{name}} 形式的轻量占位符。"""
    result = content
    for key, value in values.items():
        result = result.replace(f"{{{{{key}}}}}", value or "")
    return result


def should_skip_section(name: str, values: dict[str, str]) -> bool:
    """动态 section 在对应内容为空时跳过。"""
    required_by_section = {
        "tools_guidance": "tools_section",
        "skills_guidance": "skills_section",
        "rules_guidance": "rules_section",
        "extra_tools": "extra_tools",
        "custom_task_context": "custom_append",
        "upstream_summary": "upstream_summary",
    }
    required_key = required_by_section.get(name)
    return bool(required_key and not values.get(required_key, "").strip())


def build_tools_section(tools: list[Any] | None) -> str:
    """根据 LangChain/自定义工具对象生成工具说明。"""
    if not tools:
        return ""

    parts: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            name = tool.get("name")
        if not name:
            continue

        description = getattr(tool, "description", "")
        if isinstance(tool, dict):
            description = tool.get("description", description)

        parts.append(f"### `{name}`")
        if description:
            parts.append(str(description))

        args_schema = getattr(tool, "args_schema", None)
        schema: dict[str, Any] = {}
        if args_schema is not None:
            try:
                schema = args_schema.model_json_schema()
            except Exception:
                schema = {}
        elif isinstance(tool, dict):
            schema = tool.get("args_schema") or tool.get("schema") or {}

        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        if properties:
            parts.append("**参数：**")
            for param_name, param_schema in properties.items():
                param_type = param_schema.get("type", "any") if isinstance(param_schema, dict) else "any"
                param_desc = param_schema.get("description", "") if isinstance(param_schema, dict) else ""
                required_mark = " *(required)*" if param_name in required else ""
                suffix = f": {param_desc}" if param_desc else ""
                parts.append(f"- `{param_name}` ({param_type}){required_mark}{suffix}")

        parts.append("")

    return "\n".join(parts).strip()


def build_session_meta_text(session: "AgentSession") -> str:
    """从 AgentSession 对象构建会话元信息文本（替换 {{session_meta}} 占位符用）。"""
    import src.config as config

    parent_id_str = session.parent_id or "无"
    workflow_id_str = session.workflow_id or "无"
    agent_ws = session.workspace_path or "未配置"

    # 工作流工作空间（共享根目录）：仅当属于某个 workflow 时推导
    if session.workflow_id:
        workflow_ws = str(config.WORKFLOW_WORKSPACES_DIR / session.workflow_id)
    else:
        workflow_ws = "无"

    return (
        f"- **会话 ID**: {session.session_id}\n"
        f"- **会话类型**: {session.session_type}\n"
        f"- **父会话 ID**: {parent_id_str}\n"
        f"- **Agent 类型**: {session.agent_type}\n"
        f"- **工作流 ID**: {workflow_id_str}\n"
        f"- **创建时间**: {session.created_at}\n"
        f"- **Agent Workspace**: {agent_ws}\n"
        f"- **Workflow Workspace**: {workflow_ws}"
    )
