"""
Rules 系统 - 必须遵守的规则模块

Rules 是必须严格遵守的规则，会自动注入到 Agent 上下文中。
与 Skills 的区别：
- Skills：可选的知识和最佳实践
- Rules：必须遵守的强制规则
"""
from .manager import RuleManager
from .models import Rule
from .loader import RuleLoader
from .config_manager import RuleConfigManager

__all__ = ["RuleManager", "Rule", "RuleLoader", "RuleConfigManager"]
