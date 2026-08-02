"""
Skills 系统 - 可复用的知识和行为模块

Skills 是可以动态注入到 Agent 上下文中的知识片段和行为指南。
每个 skill 包含：
- 名称和描述
- 适用的 agent 类型
- 优先级
- 内容（markdown 格式）
- 元数据（标签、版本等）
"""
from .manager import SkillManager
from .models import Skill, SkillCategory
from .loader import SkillLoader
from .config_manager import SkillConfigManager

__all__ = ["SkillManager", "Skill", "SkillCategory", "SkillLoader", "SkillConfigManager"]
