"""
系统变量注册中心

集中管理所有系统内置的提示词变量。
用于区分系统变量（自动处理）和用户自定义变量块（需要节点配置填写）。
"""
import re

# 系统变量注册表
# key: 变量名（不包含花括号）
# value: 变量描述（用于前端展示）
_SYSTEM_VARIABLES: dict[str, str] = {
    # PromptBuilder 层面处理的变量
    "session_meta": "会话元信息（会话ID、类型、创建时间等）",
    "workflow_overview": "工作流概览（节点列表、执行顺序）",
    "workflow_structure": "工作流结构（DAG 拓扑、全局变量）",
    "workflow_definition_json": "工作流定义 JSON（原始 definition.json）",
    "tools_section": "工具列表说明",
    "skills_section": "技能列表说明",
    "rules_section": "规则列表说明",
    "rules_reminder": "规则提醒（重复注入用于长上下文）",

    # sub_agent_prompts 层面处理的变量
    "extra_tools": "额外工具列表",
    "custom_append": "自定义追加内容（system_prompt_template）",
    "upstream_summary": "上游节点产出摘要",
}


def is_system_variable(key: str) -> bool:
    """判断一个变量名是否为系统内置变量。

    Args:
        key: 变量名（不包含花括号），如 "session_meta"

    Returns:
        True 如果是系统变量，False 否则
    """
    return key in _SYSTEM_VARIABLES


def get_system_variable_description(key: str) -> str:
    """获取系统变量的描述。

    Args:
        key: 变量名（不包含花括号）

    Returns:
        变量描述，如果不是系统变量返回空字符串
    """
    return _SYSTEM_VARIABLES.get(key, "")


def get_all_system_variables() -> dict[str, str]:
    """获取所有系统变量及其描述。

    Returns:
        {变量名: 描述} 字典
    """
    return dict(_SYSTEM_VARIABLES)


def register_system_variable(key: str, description: str) -> None:
    """注册一个新的系统变量。

    用于扩展时动态注册新的系统变量。

    Args:
        key: 变量名（不包含花括号）
        description: 变量描述
    """
    _SYSTEM_VARIABLES[key] = description


# 模块级预编译正则，避免每次调用 scan_placeholders 时重复编译
_PLACEHOLDER_RE = re.compile(r"\{\{([\w-]+)\}\}")


def scan_placeholders(content: str) -> list[str]:
    """扫描内容中的所有 {{key}} 占位符。

    Args:
        content: 包含占位符的文本

    Returns:
        所有发现的变量名列表（不包含花括号）
    """
    return list(set(match.group(1) for match in _PLACEHOLDER_RE.finditer(content)))


def classify_placeholders(content: str) -> dict[str, list[str]]:
    """分类内容中的占位符为系统变量和自定义变量。

    Args:
        content: 包含占位符的文本

    Returns:
        {
            "system": ["session_meta", ...],
            "custom": ["planning_section", ...]
        }
    """
    all_keys = scan_placeholders(content)
    system_keys = [k for k in all_keys if is_system_variable(k)]
    custom_keys = [k for k in all_keys if not is_system_variable(k)]
    return {
        "system": sorted(system_keys),
        "custom": sorted(custom_keys),
    }
