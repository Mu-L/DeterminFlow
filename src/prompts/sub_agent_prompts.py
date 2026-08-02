"""
Sub Agent 提示词组装逻辑

所有 section 内容从 config/prompts_config.json (agents.subagent.sections) 加载，
此文件仅负责运行时组装：模板变量替换、条件跳过、排序等。
"""
import re
import warnings
from typing import Any

from .sections import PromptSection, dump_sections
from .system_variables import is_system_variable
from .placeholders import should_skip_section, render_prompt_template
from src.core.utils import estimate_tokens

# 占位符正则：匹配 {{key}} 格式
_PLACEHOLDER_RE = re.compile(r"\{\{([\w-]+)\}\}")


# ============================================================
# 组装函数
# ============================================================

def build_sub_agent_sections(
    config_sections: list[dict[str, Any]],
    custom_append: str = "",
    extra_tool_names: list[str] | None = None,
    skills_section: str = "",
    rules_section: str = "",
    tools_section: str = "",
    is_workflow_node: bool = False,
    upstream_summary: str = "",
    template_vars: dict[str, str] | None = None,
) -> list[PromptSection]:
    """从 JSON 配置构建 Sub Agent sections，替换动态占位符。

    Args:
        config_sections: 配置中的 sections 列表
        custom_append: 自定义追加内容
        extra_tool_names: 额外工具名列表
        skills_section: 技能列表文本
        rules_section: 规则列表文本
        tools_section: 工具列表文本
        is_workflow_node: 是否为工作流节点
        upstream_summary: 上游节点摘要
        template_vars: 自定义变量块的值 {key: value}
    """
    extra_tools_text = "\n".join(f"- **{name}**" for name in extra_tool_names) if extra_tool_names else ""
    dynamic_values = {
        "extra_tools": extra_tools_text,
        "tools_section": tools_section.strip() if tools_section else extra_tools_text,
        "custom_append": custom_append.strip() if custom_append else "",
        "skills_section": skills_section.strip() if skills_section else "",
        "rules_section": rules_section.strip() if rules_section else "",
        "upstream_summary": upstream_summary.strip() if upstream_summary else "",
    }

    # 合并自定义变量块值
    template_vars = template_vars or {}

    result: list[PromptSection] = []
    enabled_sections = [s for s in config_sections if s.get("enabled", True)]
    for sec in sorted(enabled_sections, key=lambda s: s.get("order", 0)):
        name = sec.get("name", "")
        content = sec.get("content", "")
        # workflow_only 过滤：非工作流环境跳过 workflow_only=true 的 section
        if sec.get("workflow_only", False) and not is_workflow_node:
            continue
        # chat_only 过滤：工作流环境跳过 chat_only=true 的 section
        if sec.get("chat_only", False) and is_workflow_node:
            continue
        # 条件跳过：占位符内容为空时跳过对应 section
        if should_skip_section(name, dynamic_values):
            continue

        # 模板变量替换（系统变量）— 复用 placeholders.render_prompt_template
        content = render_prompt_template(content, dynamic_values)

        # 自定义变量块替换（非系统变量的 {{key}}）
        if template_vars:
            content = _replace_template_vars(content, template_vars)

        result.append(PromptSection(
            name=name,
            content=content,
            cache_break=sec.get("cache_break", False),
            cache_break_reason=sec.get("cache_break_reason", ""),
        ))
    return result


def _replace_template_vars(content: str, template_vars: dict[str, str]) -> str:
    """替换内容中的自定义变量块占位符。

    只替换非系统变量的 {{key}} 占位符。

    Args:
        content: 包含占位符的文本
        template_vars: 自定义变量块的值 {key: value}

    Returns:
        替换后的文本
    """
    if not template_vars:
        return content

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        # 系统变量跳过，留给系统处理
        if is_system_variable(key):
            return match.group(0)
        # 自定义变量块：从 template_vars 读取值
        if key in template_vars:
            return template_vars[key]
        # 未声明的变量：保留原样
        return match.group(0)

    return _PLACEHOLDER_RE.sub(replacer, content)


def dump_sub_agent_sections(config_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导出 Sub Agent sections 详情，供前端编排页管理。"""
    return dump_sections(config_sections)


def build_sub_agent_prompt(
    config_sections: list[dict[str, Any]],
    custom_append: str = "",
    extra_tool_names: list[str] | None = None,
    skills_section: str = "",
    rules_section: str = "",
    tools_section: str = "",
    is_workflow_node: bool = False,
    upstream_summary: str = "",
    template_vars: dict[str, str] | None = None,
) -> str:
    """组装完整的 Sub Agent system prompt。

    .. deprecated::
        请改用 ``PromptOrchestrator.build_sub_agent_prompt()``，以获得统一的
        skills/rules 注入路径和一致的 dynamic_values 结构。

    Args:
        config_sections: 配置中的 sections 列表
        custom_append: 自定义追加内容
        extra_tool_names: 额外工具名列表
        skills_section: 技能列表文本
        rules_section: 规则列表文本
        tools_section: 工具列表文本
        is_workflow_node: 是否为工作流节点
        upstream_summary: 上游节点摘要
        template_vars: 自定义变量块的值 {key: value}
    """
    warnings.warn(
        "build_sub_agent_prompt() is deprecated, use PromptOrchestrator.build_sub_agent_prompt() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    sections = build_sub_agent_sections(
        config_sections=config_sections,
        custom_append=custom_append,
        extra_tool_names=extra_tool_names,
        skills_section=skills_section,
        rules_section=rules_section,
        tools_section=tools_section,
        is_workflow_node=is_workflow_node,
        upstream_summary=upstream_summary,
        template_vars=template_vars,
    )
    parts = [s.content for s in sections]
    return "\n\n".join(parts)
