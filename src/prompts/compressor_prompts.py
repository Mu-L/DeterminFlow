"""
Compressor Agent 提示词组装逻辑

所有 section 内容从 config/prompts_config.json (agents.compressor.sections) 加载，
此文件仅负责运行时组装：排序、启用过滤等。
"""
from typing import Any

from src.core.utils import estimate_tokens

from .sections import PromptSection, dump_sections


# ============================================================
# 组装函数
# ============================================================

def build_compressor_sections(
    config_sections: list[dict[str, Any]],
    is_workflow_node: bool = False,
) -> list[PromptSection]:
    """从 JSON 配置构建 Compressor Agent sections。

    Args:
        config_sections: 配置中的 sections 列表
        is_workflow_node: 是否为工作流节点环境，用于 workflow_only/chat_only 过滤
    """
    result: list[PromptSection] = []
    enabled_sections = [s for s in config_sections if s.get("enabled", True)]

    for sec in sorted(enabled_sections, key=lambda s: s.get("order", 0)):
        # workflow_only 过滤：非工作流环境跳过 workflow_only=true 的 section
        if sec.get("workflow_only", False) and not is_workflow_node:
            continue
        # chat_only 过滤：工作流环境跳过 chat_only=true 的 section
        if sec.get("chat_only", False) and is_workflow_node:
            continue
        result.append(PromptSection(
            name=sec.get("name", ""),
            content=sec.get("content", ""),
            cache_break=sec.get("cache_break", False),
            cache_break_reason=sec.get("cache_break_reason", ""),
        ))

    return result


def build_compressor_prompt(
    config_sections: list[dict[str, Any]],
    is_workflow_node: bool = False,
) -> str:
    """组装完整的 Compressor Agent system prompt。"""
    sections = build_compressor_sections(
        config_sections=config_sections,
        is_workflow_node=is_workflow_node,
    )
    parts = [s.content for s in sections]
    return "\n\n".join(parts)


def dump_compressor_sections(config_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导出 Compressor Agent sections 详情，供前端编排页管理。"""
    return dump_sections(config_sections)
