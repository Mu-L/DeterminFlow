"""
PromptSection 数据模型与工厂函数
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.utils import estimate_tokens


@dataclass
class PromptSection:
    """
    单个提示词段落的数据模型。

    Attributes:
        name: section 标识符
        content: section 内容
        cache_break: 是否需要每轮重新计算
        cache_break_reason: 为什么此 section 需要每次重建
    """
    name: str
    content: str
    cache_break: bool = False
    cache_break_reason: str = ""


def static_section(name: str, content: str) -> PromptSection:
    """创建静态（可缓存）section。"""
    return PromptSection(name=name, content=content, cache_break=False)


def DANGEROUS_uncached_section(
    name: str,
    content: str,
    reason: str,
) -> PromptSection:
    """
    创建非缓存 section。

    警告：此类 section 每次 LLM 调用都会重新计算，会破坏 prompt cache。
    强制提供 reason 参数作为心理障碍机制。
    """
    if not reason:
        raise ValueError(
            f"DANGEROUS_uncached_section '{name}' 必须提供 reason 参数。"
            "如果此 section 实际上是静态的，请使用 static_section() 代替。"
        )
    return PromptSection(
        name=name,
        content=content,
        cache_break=True,
        cache_break_reason=reason,
    )


def dump_sections(config_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导出 sections 详情列表，供前端编排页管理。

    通用实现，可被 sub_agent_prompts / compressor_prompts 等模块复用。
    """
    sections = sorted(config_sections, key=lambda s: s.get("order", 0))
    return [
        {
            "name": sec.get("name", ""),
            "content": sec.get("content", ""),
            "token_estimate": estimate_tokens(sec.get("content", "")),
            "cache_break": sec.get("cache_break", False),
            "cache_break_reason": sec.get("cache_break_reason", ""),
            "enabled": sec.get("enabled", True),
            "workflow_only": sec.get("workflow_only", False),
            "order": sec.get("order", i),
        }
        for i, sec in enumerate(sections)
    ]
