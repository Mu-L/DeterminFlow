"""
圆桌会议 Moderator 提示词模块

将原 roundtable_runner.py 中的三个裸字符串常量迁移至独立模块，
并应用 Claude Code 提示词工程最佳实践进行强化。

改进点：
1. 首尾包夹约束（法则 7）：JSON 输出 prompt 在头部声明格式要求，尾部重申
2. 数字化长度锚点（法则 8）：用"≤200字"替代模糊的"简洁"
3. 反例约束（法则 9）：明确列出错误的输出格式示例
4. 禁止性指令密度（法则 9）：NEVER/DO NOT 显式声明不允许的行为
5. 独立模块（法则 4）：与执行引擎解耦，可独立演进和版本控制

注意：这三个 prompt 使用 Python format() 字符串，而非 PromptSection，
因为它们是运行时动态生成的（包含 {topic}、{current_round} 等变量），
应在调用时通过 safe_format() 填充变量（缺失 key 时替换为空字符串并 warning）。
"""
import logging
import re

logger = logging.getLogger(__name__)


def safe_format(template: str, **kwargs: str) -> str:
    """安全的字符串格式化：缺失变量时替换为空字符串并记录 warning。

    Args:
        template: 包含 {key} 占位符的模板字符串
        **kwargs: 要填充的变量

    Returns:
        格式化后的字符串
    """
    # 找出模板中所有占位符，排除 {{}} 转义的大括号
    # 先将 {{ 和 }} 替换掉，再匹配 {key}
    stripped = template.replace("{{", "").replace("}}", "")
    placeholders = set(re.findall(r'\{(\w+)\}', stripped))
    missing = placeholders - set(kwargs.keys())
    if missing:
        logger.warning(f"roundtable_prompts.safe_format: 缺失变量 {missing}，将替换为空字符串")
        kwargs = {**kwargs}
        for key in missing:
            kwargs[key] = ""
    return template.format(**kwargs)


# ============================================================
# Moderator 决策 Prompt（首尾包夹模式）
# ============================================================

# 头部：明确格式要求 + 反例约束
_MODERATOR_DECISION_PREAMBLE = """IMPORTANT: 你必须只返回一个 JSON 对象，**不要**包含任何其他内容。
不要添加解释、markdown 代码块（```json...```）、或任何前缀/后缀文字。

错误示例（NEVER do this）：
  "我认为应该选择小明发言，理由是..."
  "```json\\n{{...}}\\n```"

正确示例（ONLY this）：
  {{"action": "select_speaker", "speaker_id": "seat-1", "reason": "..."}}

"""

# 尾部：REMINDER 重申
_MODERATOR_DECISION_REMINDER = """
---
REMINDER: 只返回 JSON 对象，不要包含其他内容。
有效的 action 值：select_speaker | new_round | conclude | summarize
如果 action 是 select_speaker，必须提供有效的 speaker_id。"""

MODERATOR_DECISION_PROMPT = (
    _MODERATOR_DECISION_PREAMBLE
    + """你是一场圆桌会议的主持人 AI。

当前讨论主题：{topic}
当前轮次：第 {current_round} 轮（共 {max_rounds} 轮）
总发言数：{transcript_count}

参与者列表：
{participants}

最近讨论摘要：
{recent_summary}

请决定下一步行动。返回 JSON 对象，格式如下：

{{
  "action": "select_speaker" | "new_round" | "conclude" | "summarize",
  "speaker_id": "seat-X",
  "reason": "你的决策理由（≤50字）"
}}

行动说明：
- "select_speaker": 选择下一位发言者（**必须**提供有效的 speaker_id）
- "new_round": 开始新一轮讨论（可选提供首位发言者的 speaker_id）
- "conclude": 讨论充分，结束会议
- "summarize": 需要先做阶段摘要再继续

决策原则：
- 确保每位参与者都有发言机会
- 如果发现讨论陷入重复，考虑结束或转入新话题
- 关注讨论的深度和广度平衡
- 如果即将达到最大轮次，提前准备结论"""
    + _MODERATOR_DECISION_REMINDER
)


# ============================================================
# Moderator 摘要 Prompt（数字化长度锚点）
# ============================================================

MODERATOR_SUMMARY_PROMPT = """你是一场圆桌会议的主持人 AI，请为最近的讨论生成一份简洁的阶段摘要。

讨论主题：{topic}
当前轮次：第 {current_round} 轮

最近的讨论记录：
{recent_transcript}

请生成一份**不超过 200 字**的摘要，涵盖：
1. 主要观点和论点
2. 已达成的共识
3. 尚存的分歧

输出要求：
- **只输出摘要内容**，不要添加标题、编号或额外说明
- 使用简洁的段落形式，而非列表
- 如果内容超过 200 字，进行压缩而非截断

REMINDER: 只输出摘要内容，不超过 200 字。"""


# ============================================================
# Moderator 结论 Prompt（首尾包夹 + 字段验证约束）
# ============================================================

_MODERATOR_CONCLUSION_PREAMBLE = """IMPORTANT: 你必须只返回一个 JSON 对象，**不要**包含任何其他内容。
不要添加解释、markdown 代码块（```json...```）、或任何前缀/后缀文字。

"""

_MODERATOR_CONCLUSION_REMINDER = """
---
REMINDER: 只返回 JSON 对象。
- summary 字段：**必填**，≤300 字
- consensus、disagreements、pending_verification、action_items 字段：**必填**，可以是空列表 []
- **不要**省略任何字段，即使值为空列表"""

MODERATOR_CONCLUSION_PROMPT = (
    _MODERATOR_CONCLUSION_PREAMBLE
    + """你是一场圆桌会议的主持人 AI，请为整场讨论生成结构化最终结论。

讨论主题：{topic}
共进行了 {total_rounds} 轮讨论，{transcript_count} 条发言

{shared_memory_context}

完整讨论记录：
{full_transcript}

返回 JSON 对象，格式如下：

{{
  "summary": "≤300 字的会议总结",
  "consensus": ["共识点1", "共识点2"],
  "disagreements": ["分歧点1", "分歧点2"],
  "pending_verification": ["待验证事项1"],
  "action_items": ["行动项1: 负责人 - 截止日期"]
}}

字段要求：
1. **summary**（必填）：简明扼要总结整场讨论，**不超过 300 字**
2. **consensus**（必填）：列出所有参与者达成一致的核心观点，无共识时填 `[]`
3. **disagreements**（必填）：列出仍然存在分歧的主要问题，无分歧时填 `[]`
4. **pending_verification**（必填）：列出讨论中提出但未验证的假设或数据，无则填 `[]`
5. **action_items**（必填）：列出具体的后续行动建议，无则填 `[]`"""
    + _MODERATOR_CONCLUSION_REMINDER
)
