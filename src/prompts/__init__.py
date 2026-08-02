"""
提示词系统 - 分段组装、优先级覆盖、可观测性

核心组件：
- PromptSection: 提示词片段数据模型
- PromptManager: 统一配置管理器（config/prompts_config.json）
- PromptOrchestrator: 5 级优先级覆盖链的运行时编排器
- sub_agent_prompts: Sub Agent 的 prompt 组装逻辑（数据来自 prompts_config.json）
- compressor_prompts: Compressor Agent 的 prompt 组装逻辑（数据来自 prompts_config.json）
- roundtable_prompts: 圆桌会议的 prompt 构建函数
"""
from pathlib import Path
from typing import TYPE_CHECKING

from .manager import PromptManager
from .orchestrator import PromptOrchestrator
from .sections import PromptSection
from . import system_variables

if TYPE_CHECKING:
    from src.skills.manager import SkillManager
    from src.rules.manager import RuleManager


def create_orchestrator(
    prompt_manager: PromptManager,
    skill_manager: "SkillManager | None" = None,
    rule_manager: "RuleManager | None" = None,
) -> PromptOrchestrator:
    """工厂函数：创建 PromptOrchestrator 实例"""
    return PromptOrchestrator(
        prompt_manager=prompt_manager,
        skill_manager=skill_manager,
        rule_manager=rule_manager,
    )


__all__ = [
    "PromptManager",
    "PromptOrchestrator",
    "PromptSection",
    "create_orchestrator",
    "system_variables",
]
