"""
Agent 定义系统 - 统一的 Agent 类型注册与工具过滤机制

参照 Claude Code 工程哲学指南（4.1 Agent 定义系统 + 4.4 工具过滤层次）：
- AgentDefinition 统一描述 Agent 的身份、工具权限、模型配置
- ALL_AGENT_DISALLOWED_TOOLS 实现全局禁用（防止递归和权限提升）
- resolve_agent_tools() 实现多层工具过滤流水线
- Agent 类型从配置文件加载，不再硬编码

工具过滤流水线:
  ALL_AGENT_DISALLOWED_TOOLS (全局禁止)
    → AgentDefinition.tools (白名单，None=仅通信工具, ["*"]=全部)
    → AgentDefinition.disallowed_tools (黑名单剔除)
    → 合并 Sub Agent 通信工具
    → 最终工具列表
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from src.agent.config_manager import AgentConfigManager

logger = logging.getLogger(__name__)


# ============================================================
# 全局配置管理器
# ============================================================

# 线程安全约定：此变量仅在应用启动阶段（主线程）通过
# set_agent_config_manager() 设置一次，之后只读访问。
# 若未来需要多线程写入，应添加 threading.Lock 保护。
_agent_config_manager: "AgentConfigManager | None" = None


def set_agent_config_manager(manager: "AgentConfigManager"):
    """设置全局 Agent 配置管理器（仅在启动阶段调用一次）"""
    global _agent_config_manager
    _agent_config_manager = manager
    logger.info("Agent 配置管理器已设置")


# ============================================================
# AgentDefinition 数据模型
# ============================================================

@dataclass
class AgentDefinition:
    """
    统一的 Agent 定义模型。

    参照 Claude Code 的 AgentDefinition 设计（指南 4.1），
    每个 Agent 类型通过此结构描述身份、工具权限和行为配置。

    Attributes:
        agent_type: 唯一标识符（如 "default", "researcher", "reviewer"）
        description: 描述何时使用此 Agent（暴露给主 Agent 决策）
        tools: 允许的工具名列表
            - None: 仅通信工具（send_message）
            - ["*"]: 全部可用工具（经全局禁用过滤后）
            - ["search_memory", "add_memory", ...]: 指定工具白名单
        disallowed_tools: 拒绝的工具名列表（在白名单基础上进一步剔除）
        model: 模型覆盖，None 表示继承父 Agent 的模型
        max_turns: 最大工具调用轮次
        system_prompt_template: 可选的额外 prompt 模板（追加到 Sub Agent 基础 prompt 之后）
        include_skills: 是否把运行时 Skill 内容注入 system prompt
        include_rules: 是否把运行时 Rule 内容注入 system prompt
        copy_main_workspace: 创建子会话时是否复制 Main Workspace 内容
            - None: 继承全局配置 CODING_WORKSPACE_COPY_ON_CREATE
            - True: 强制复制
            - False: 强制不复制（空白 workspace）
        available_for_sub_session: 是否可被创建为子会话（默认 True）
            - True: 可在 create_sub_session 工具中使用
            - False: 不可被用户直接创建（如 compressor）
    """
    agent_type: str
    description: str
    prompt_template: str = "subagent"
    """提示词模板名，指向 prompts_config.json 中的模板（如 "main", "subagent", "compressor"）"""
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int = 10
    system_prompt_template: str = ""
    include_skills: bool = True
    include_rules: bool = True
    copy_main_workspace: bool | None = None
    visible_skill_group_ids: list[str] | None = None
    visible_rule_group_ids: list[str] | None = None
    extension_options: dict[str, dict] | None = None
    available_for_sub_session: bool = True
    model_params: dict | None = None
    """
    模型参数覆盖（可选）。
    结构：{"thinking_enabled": bool, "reasoning_effort": "high"|"max",
           "temperature": float, "top_p": float,
           "stream_chunk_timeout": 正数秒值或 None}
    未设置的字段从 models_config.json 的 default_params 继承。
    None 表示全部使用 defaults。
    """


# ============================================================
# 全局禁用工具集（Fail-closed 安全默认）
# ============================================================

# 所有 Sub Agent 禁止使用的工具（防止递归创建子会话和权限提升）
# 参照指南 4.4: ALL_AGENT_DISALLOWED_TOOLS
ALL_AGENT_DISALLOWED_TOOLS: set[str] = {
    "create_sub_session",       # 防止 Sub Agent 递归创建子会话
    "check_sub_progress",       # Sub Agent 不应查看其他子会话
    "delete_session",           # Sub Agent 不应删除会话
}


# ============================================================
# Agent 定义查找（从配置文件加载）
# ============================================================

def get_agent_definition(agent_type: str) -> AgentDefinition | None:
    """
    从配置文件查找 Agent 定义。

    Args:
        agent_type: Agent 类型标识符

    Returns:
        匹配的 AgentDefinition，未找到返回 None
    """
    if not _agent_config_manager:
        logger.error("Agent 配置管理器未初始化")
        return None

    config = _agent_config_manager.get_agent_config(agent_type)
    if config:
        return _config_to_definition(agent_type, config)

    return None


def get_default_agent_definition() -> AgentDefinition | None:
    """获取默认 Agent 定义"""
    return get_agent_definition("default")


def _config_to_definition(agent_type: str, config: dict) -> AgentDefinition:
    """从配置字典构建 AgentDefinition 对象（DRY 公共方法）"""
    return AgentDefinition(
        agent_type=agent_type,
        description=config.get("description", ""),
        prompt_template=config.get("prompt_template", "subagent"),
        tools=config.get("tools"),
        disallowed_tools=config.get("disallowed_tools"),
        model=config.get("model"),
        max_turns=config.get("max_turns", 10),
        system_prompt_template=config.get("system_prompt_template", ""),
        include_skills=config.get("include_skills", True),
        include_rules=config.get("include_rules", True),
        copy_main_workspace=config.get("copy_main_workspace"),
        visible_skill_group_ids=config.get("visible_skill_group_ids"),
        visible_rule_group_ids=config.get("visible_rule_group_ids"),
        extension_options=config.get("extension_options"),
        available_for_sub_session=config.get("available_for_sub_session", True),
        model_params=config.get("model_params"),
    )


def list_agent_definitions() -> list[AgentDefinition]:
    """列出所有 Agent 定义（从配置文件加载）"""
    if not _agent_config_manager:
        logger.error("Agent 配置管理器未初始化")
        return []

    result = []
    for agent_type, config in _agent_config_manager.get_all_agents().items():
        result.append(_config_to_definition(agent_type, config))

    return result


def list_agent_types() -> list[dict]:
    """列出所有 Agent 类型的摘要信息（用于 API 和 prompt 注入）

    注意：此函数直接从配置构建 dict，避免构造完整 AgentDefinition 对象。
    """
    if not _agent_config_manager:
        logger.error("Agent 配置管理器未初始化")
        return []

    _FIELDS = [
        "description", "prompt_template", "tools", "disallowed_tools",
        "model", "max_turns", "system_prompt_template", "include_skills",
        "include_rules", "copy_main_workspace",
        "visible_skill_group_ids", "visible_rule_group_ids", "extension_options",
        "available_for_sub_session", "model_params",
    ]
    _DEFAULTS = {
        "description": "", "prompt_template": "subagent", "max_turns": 10,
        "system_prompt_template": "", "include_skills": True,
        "include_rules": True, "available_for_sub_session": True,
    }

    result = []
    for agent_type, cfg in _agent_config_manager.get_all_agents().items():
        entry: dict = {"agent_type": agent_type}
        for key in _FIELDS:
            if key in cfg:
                entry[key] = cfg[key]
            elif key in _DEFAULTS:
                entry[key] = _DEFAULTS[key]
        result.append(entry)
    return result


def list_available_sub_session_types() -> list[str]:
    """列出所有可被创建为子会话的 Agent 类型标识符。

    Returns:
        可用的 agent_type 列表（如 ["default", "researcher", "coder"]）
    """
    all_definitions = list_agent_definitions()
    return [d.agent_type for d in all_definitions if d.available_for_sub_session]


# ============================================================
# 多层工具过滤函数
# ============================================================

def resolve_agent_tools(
    definition: AgentDefinition,
    available_mcp_tools: list["BaseTool"],
    sub_communication_tools: list["BaseTool"],
) -> list["BaseTool"]:
    """
    根据 AgentDefinition 解析 Sub Agent 的最终工具列表。

    实现多层过滤流水线（参照指南 4.4 工具过滤层次）：
    1. 从 available_mcp_tools 中剔除 ALL_AGENT_DISALLOWED_TOOLS
    2. 按 definition.tools 白名单过滤
    3. 按 definition.disallowed_tools 黑名单剔除
    4. 合并 sub_communication_tools（通信工具始终包含）

    Args:
        definition: Agent 定义
        available_mcp_tools: 可用的 MCP 工具列表（主进程已适配的 StructuredTool）
        sub_communication_tools: Sub Agent 通信工具（send_message）

    Returns:
        最终的工具列表
    """
    # Step 1: 全局禁用过滤
    filtered = [
        t for t in available_mcp_tools
        if t.name not in ALL_AGENT_DISALLOWED_TOOLS
    ]

    # Step 2: 白名单过滤
    if definition.tools is None:
        # None = 不给 MCP 工具，仅通信工具
        filtered = []
    elif definition.tools == ["*"]:
        # ["*"] = 全部可用（已经过 Step 1 过滤）
        pass
    else:
        # 指定工具名列表
        allowed_set = set(definition.tools)
        filtered = [t for t in filtered if t.name in allowed_set]

    # Step 3: 黑名单剔除
    if definition.disallowed_tools:
        disallowed_set = set(definition.disallowed_tools)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    # Step 4: 合并通信工具（始终包含）
    final_tools = filtered + list(sub_communication_tools)

    logger.info(
        f"Agent [{definition.agent_type}] 工具解析完成: "
        f"{len(filtered)} 个 MCP 工具 + {len(sub_communication_tools)} 个通信工具 = "
        f"{len(final_tools)} 个总工具"
    )

    return final_tools
