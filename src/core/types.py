"""
共享类型定义 - 跨模块复用的 dataclass / TypedDict / Enum
"""
from dataclasses import dataclass


@dataclass
class GuardResult:
    """鉴权/校验结果

    用于 WorkspaceGuard、ToolGuard 等安全检查模块。
    统一定义避免重复声明导致字段不一致。
    """
    allowed: bool
    reason: str = ""
    needs_approval: bool = False
