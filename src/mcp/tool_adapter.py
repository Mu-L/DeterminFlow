"""
工具适配层 - 将 MCP 工具和本地会话管理工具包装为 LangChain StructuredTool

改造：
- create_mcp_tools() 支持按 session 生成带 workspace 上下文的编码工具
- 编码类工具自动注入 _session_id 和 _workspace_path 隐式参数
"""
from __future__ import annotations

import functools
import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

if TYPE_CHECKING:
    from src.mcp.client import MCPClient
    from src.agent.session_manager import SessionManager

logger = logging.getLogger(__name__)


def _sync_stub(**kwargs) -> str:
    """同步调用占位符 — 会话/MCP 工具仅支持异步调用。"""
    raise NotImplementedError("此工具仅支持异步调用，请使用 coroutine 接口")


# ============================================================
# MCP 工具适配
# ============================================================

def _json_schema_to_pydantic_field(
    name: str, prop: dict, required_fields: list[str]
) -> tuple[type, Any]:
    """将 JSON Schema 属性定义转换为 Pydantic Field 元组。

    支持 string/integer/number/boolean/array/object 六种类型。
    对 array 类型，检查 items 子 schema 推断元素类型（list[T]）；
    对 object 和未知类型，使用 Any 作为兜底以避免数据丢失。
    """
    type_map: dict[str, type] = {
        "string": str, "integer": int, "number": float, "boolean": bool,
        "array": list, "object": dict,
    }
    prop_type = prop.get("type", "string")

    # 推断 array 元素类型
    if prop_type == "array":
        items = prop.get("items", {})
        item_type = type_map.get(items.get("type", "string"), Any)
        field_type = list[item_type] if item_type is not Any else list  # type: ignore[valid-type]
    else:
        field_type = type_map.get(prop_type, Any)

    description = prop.get("description", "")
    default = prop.get("default")

    if name in required_fields:
        return (field_type, Field(description=description))
    else:
        if default is not None:
            return (Optional[field_type], Field(default=default, description=description))
        else:
            # 非必填字段默认 None，让 LLM 区分"未传值"和"主动传空值"
            return (Optional[field_type], Field(default=None, description=description))


@functools.lru_cache(maxsize=64)
def _build_args_model_cached(tool_name: str, schema_key: str, schema_json: str) -> type[BaseModel]:
    """缓存版本：按 (tool_name, schema_key) 复用已创建的 Pydantic model 类。"""
    input_schema = json.loads(schema_json)
    properties = input_schema.get("properties", {})
    required_fields = input_schema.get("required", [])

    if not properties:
        return create_model(f"{tool_name}_Args")

    fields = {}
    for prop_name, prop_schema in properties.items():
        # 过滤掉隐式上下文参数（不暴露给 LLM）
        if prop_name.startswith("ctx_"):
            continue
        fields[prop_name] = _json_schema_to_pydantic_field(prop_name, prop_schema, required_fields)

    return create_model(f"{tool_name}_Args", **fields)


def _build_args_model(tool_name: str, input_schema: dict) -> type[BaseModel]:
    """构建工具参数 Pydantic model，相同 schema 复用已创建的类。"""
    schema_json = json.dumps(input_schema, sort_keys=True)
    return _build_args_model_cached(tool_name, tool_name, schema_json)


def create_mcp_tools(mcp: "MCPClient") -> list[StructuredTool]:
    """从 MCPClient 获取所有工具定义，包装为 LangChain StructuredTool。"""
    tools = []
    for tool_def in mcp.get_tools():
        tool_name = tool_def["name"]
        description = tool_def.get("description", "")
        input_schema = tool_def.get("input_schema", {})
        args_model = _build_args_model(tool_name, input_schema)

        def _make_tool(name: str, desc: str, model: type[BaseModel]):
            async def _arun(**kwargs) -> str:
                return await mcp.call_tool(name, kwargs)

            def _run(**kwargs) -> str:
                raise NotImplementedError("MCP 工具仅支持异步调用")

            return StructuredTool(name=name, description=desc, args_schema=model, func=_run, coroutine=_arun)

        tools.append(_make_tool(tool_name, description, args_model))

    logger.info(f"已适配 {len(tools)} 个 MCP 工具为 LangChain StructuredTool")
    return tools


# ============================================================
# Main Agent 会话管理工具适配
# ============================================================

def _get_available_agent_types_description() -> str:
    """动态生成可用 agent 类型的描述字符串"""
    from src.agent.definition import list_available_sub_session_types
    available_types = list_available_sub_session_types()
    return f"子代理类型: {'/'.join(available_types)}"


_sub_session_args_cache: tuple[str, type[BaseModel]] | None = None
_sub_session_args_lock = threading.Lock()


def _create_sub_session_args_class():
    """动态创建 CreateSubSessionArgs 类，按 agent 类型描述缓存复用（线程安全）。"""
    global _sub_session_args_cache
    description = _get_available_agent_types_description()

    # 快速路径：缓存命中无需拿锁
    cached = _sub_session_args_cache
    if cached is not None and cached[0] == description:
        return cached[1]

    with _sub_session_args_lock:
        # double-check: 拿锁后再次检查
        if _sub_session_args_cache is not None and _sub_session_args_cache[0] == description:
            return _sub_session_args_cache[1]

        class CreateSubSessionArgs(BaseModel):
            task_description: str = Field(description="任务描述")
            custom_prompt: str = Field(default="", description="可选的补充提示词")
            agent_type: str = Field(default="default", description=description)

        _sub_session_args_cache = (description, CreateSubSessionArgs)
        return CreateSubSessionArgs


class CheckSubProgressArgs(BaseModel):
    session_id: str = Field(default="", description="子会话 ID，留空查看所有")
    wait_for: Literal["none", "change", "terminal_or_attention"] = Field(
        default="none",
        description=(
            "等待条件：none=立即返回，change=变化或超时返回，"
            "terminal_or_attention=终态或需要 Main 介入时返回"
        ),
    )
    timeout_seconds: float | None = Field(
        default=0,
        ge=0,
        le=86_400,
        description="最长等待秒数；null 表示无截止时间，仍可取消",
    )


class CheckMainProgressArgs(BaseModel):
    session_id: str = Field(default="", description="主会话 ID，留空查看所有")


class SendToSubArgs(BaseModel):
    session_id: str = Field(description="目标子会话 ID")
    message: str = Field(description="消息内容")


class DeleteSessionArgs(BaseModel):
    session_ids: list[str] = Field(description="要删除的会话 ID 列表，支持传入一个或多个")


def create_session_tools(session_manager: "SessionManager", llm_client=None) -> list[StructuredTool]:
    """为 main agent 创建会话管理工具。

    create_sub_session 的 parent_id 和 send_message 的 from_session_id
    通过 get_session_context() 运行时获取。
    """
    from src.tools.communication_tools import create_send_message_tool
    from src.session.context import get_session_context

    # 动态创建 CreateSubSessionArgs 类
    CreateSubSessionArgs = _create_sub_session_args_class()

    async def _create_sub_session(task_description: str, custom_prompt: str = "", agent_type: str = "default") -> str:
        ctx = get_session_context()
        parent_id = ctx.get("session_id") or session_manager.main_session_id
        result = await session_manager.create_sub_session(
            task_description=task_description, custom_prompt=custom_prompt, llm_client=llm_client, agent_type=agent_type,
            parent_id=parent_id)
        return json.dumps(result, ensure_ascii=False)

    async def _check_sub_progress(
        session_id: str = "",
        wait_for: str = "none",
        timeout_seconds: float | None = 0,
    ) -> str:
        result = await session_manager.check_sub_progress(
            session_id=session_id,
            wait_for=wait_for,
            timeout_seconds=timeout_seconds,
        )
        return json.dumps(result, ensure_ascii=False)

    async def _check_main_progress(session_id: str = "") -> str:
        result = await session_manager.check_main_progress(session_id=session_id)
        return json.dumps(result, ensure_ascii=False)

    async def _delete_session(session_ids: list[str]) -> str:
        results = await session_manager.delete_sessions(session_ids=session_ids)
        return json.dumps(results, ensure_ascii=False)

    send_message_tool = create_send_message_tool(session_manager)

    tools = [
        StructuredTool(name="create_sub_session", description="创建子会话异步执行任务", args_schema=CreateSubSessionArgs, func=_sync_stub, coroutine=_create_sub_session),
        StructuredTool(
            name="check_sub_progress",
            description=(
                "查看或事件驱动等待子会话进度。等待时必须提供 session_id；"
                "终态返回会包含有上限的 final_output。"
            ),
            args_schema=CheckSubProgressArgs,
            func=_sync_stub,
            coroutine=_check_sub_progress,
        ),
        StructuredTool(name="check_main_progress", description="查看主会话（main session）的状态和进度信息", args_schema=CheckMainProgressArgs, func=_sync_stub, coroutine=_check_main_progress),
        send_message_tool,
        StructuredTool(name="delete_session", description="批量删除已完成的会话，支持传入多个会话ID", args_schema=DeleteSessionArgs, func=_sync_stub, coroutine=_delete_session),
    ]

    logger.info(f"已创建 {len(tools)} 个 main agent 会话管理工具")
    return tools


# ============================================================
# Sub Agent 工具适配
# ============================================================

def create_sub_agent_tools(session_manager: "SessionManager",
                           available_mcp_tools: list[StructuredTool] | None = None, agent_definition=None,
                           is_workflow_node: bool = False,
                           enable_complete_node_task: bool = True,
                           enable_reject_upstream: bool = False) -> list[StructuredTool]:
    """为指定 sub agent 创建工具列表。

    session_id 通过 get_session_context() 运行时获取。
    on_node_complete 通过 contextvars 获取（仅在 is_workflow_node 时使用）。

    Args:
        session_manager: 会话管理器实例
        available_mcp_tools: 可用的 MCP 工具列表
        agent_definition: Agent 定义
        is_workflow_node: 是否为工作流节点
        enable_complete_node_task: 是否注入 complete_task 工具（默认 True）
        enable_reject_upstream: 是否注入 reject_upstream 工具（默认 False）
    """
    from src.tools.communication_tools import create_complete_task_tool, create_reject_upstream_tool
    from src.session.context import get_session_context

    ctx = get_session_context()
    session_id = ctx.get("session_id", "")

    # 根据配置决定是否注入 complete_task 工具
    if enable_complete_node_task:
        communication_tools = [create_complete_task_tool(session_manager)]
    else:
        communication_tools = []

    # 根据配置决定是否注入 reject_upstream 工具
    if enable_reject_upstream and is_workflow_node:
        communication_tools.append(create_reject_upstream_tool(session_manager))

    if agent_definition is not None and available_mcp_tools:
        from src.agent.definition import resolve_agent_tools
        final_tools = resolve_agent_tools(definition=agent_definition, available_mcp_tools=available_mcp_tools, sub_communication_tools=communication_tools)
        logger.info(f"Sub agent [{session_id}] 按 AgentDefinition [{agent_definition.agent_type}] 组装了 {len(final_tools)} 个工具 (workflow={is_workflow_node})")
        return final_tools

    logger.info(f"已创建 sub agent 工具 (session: {session_id}, workflow={is_workflow_node})")
    return communication_tools
