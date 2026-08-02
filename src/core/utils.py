"""
公共工具函数模块
"""
from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, ToolMessage


def estimate_tokens(text: str) -> int:
    """
    快速估算文本的 token 数量（不引入额外 tokenizer 依赖）。

    规则：
    - 中文字符（Unicode CJK）：每字约 1.5 token
    - 其他字符：每 4 个字符约 1 token
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4) + 1


def _sanitize_tool_pairs(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    清理消息列表中不完整的 tool_calls / tool 消息配对。

    规则：
    1. 如果一条 AIMessage 有 tool_calls，但后续缺少对应的 ToolMessage，
       则移除该 AIMessage 的 tool_calls（保留文本内容）或整条移除。
    2. 如果有 ToolMessage 但找不到对应的 AIMessage tool_call_id，则移除。
    """
    result = list(messages)

    # 收集所有 ToolMessage 的 tool_call_id
    tool_msg_ids = set()
    for msg in result:
        if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
            tool_msg_ids.add(msg.tool_call_id)

    # 修复末尾悬空的 AIMessage（带 tool_calls 但没有后续 ToolMessage 响应）
    sanitized = []
    for i, msg in enumerate(result):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # 检查该 AIMessage 的每个 tool_call 是否都有对应的 ToolMessage
            missing = [tc for tc in msg.tool_calls if tc.get("id") not in tool_msg_ids]
            if missing:
                # 有 tool_call 缺少响应 → 移除 tool_calls，只保留文本内容
                if msg.content:
                    sanitized.append(AIMessage(content=msg.content))
                # 如果既无文本也无完整 tool_calls，则跳过这条消息
                continue
        sanitized.append(msg)

    # 再次清理：移除没有对应 AIMessage tool_call 的孤立 ToolMessage
    ai_tool_call_ids = set()
    for msg in sanitized:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                ai_tool_call_ids.add(tc.get("id"))

    final = []
    for msg in sanitized:
        if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
            if msg.tool_call_id not in ai_tool_call_ids:
                continue
        final.append(msg)

    return final


def _sanitize_tool_pairs_strict(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    严格清理 tool_calls / tool 消息配对（比 _sanitize_tool_pairs 更严格）。

    OpenAI API 要求：带有 tool_calls 的 assistant 消息之后，必须紧跟对应的 tool 消息，
    中间不能插入其他 assistant/user 消息。仅通过全局 ID 匹配（_sanitize_tool_pairs）
    无法检测以下场景：
        [AIMessage(tool_calls=[tc1]), AIMessage("中间文本"), ToolMessage(tc1)]
        → tc1 的 ToolMessage 存在，但没有紧跟在 AIMessage 之后，API 会拒绝。

    规则：
    1. 对每条 AIMessage(tool_calls=[...])，向后扫描其「tool block」
       （连续的 ToolMessage），检查是否所有 tool_call_id 都在该 block 中得到响应。
    2. 不完整的 → 剥离该 AIMessage 的 tool_calls，移除其孤儿 ToolMessage。
    3. 不属于任何有效 tool block 的 ToolMessage 全部移除。
    """
    if not messages:
        return messages

    result = list(messages)
    # 标记哪些索引的 ToolMessage 是合法的（属于某个完整的 tool block）
    valid_tool_indices: set[int] = set()
    # 标记哪些索引的 AIMessage(tool_calls) 需要通过「剥离 tool_calls」来修复
    strip_tool_calls_at: set[int] = set()

    i = 0
    while i < len(result):
        msg = result[i]
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tc_ids_needed = {tc.get("id") for tc in msg.tool_calls}
            tc_ids_found: set[str] = set()
            tool_indices: list[int] = []

            # 向后扫描连续的 ToolMessage
            j = i + 1
            while j < len(result):
                nxt = result[j]
                if isinstance(nxt, ToolMessage):
                    tcid = getattr(nxt, "tool_call_id", None)
                    if tcid and tcid in tc_ids_needed:
                        tc_ids_found.add(tcid)
                        tool_indices.append(j)
                    j += 1
                else:
                    # 遇到非 ToolMessage → tool block 结束
                    break

            if tc_ids_found == tc_ids_needed:
                # 完整配对 → 标记这些 ToolMessage 为合法
                valid_tool_indices.update(tool_indices)
            else:
                # 不完整配对 → 需要剥离 tool_calls
                strip_tool_calls_at.add(i)
                # 这些 ToolMessage 是孤儿，不标记为合法

            i = j  # 跳过已扫描的 tool block
        else:
            i += 1

    # 重建消息列表
    rebuilt = []
    for idx, msg in enumerate(result):
        if isinstance(msg, ToolMessage):
            if idx not in valid_tool_indices:
                continue  # 移除孤儿 ToolMessage
        if idx in strip_tool_calls_at:
            # 剥离 tool_calls，保留文本内容
            if msg.content:
                rebuilt.append(AIMessage(content=msg.content))
            # 无文本则直接丢弃
            continue
        rebuilt.append(msg)

    return rebuilt


def _diff_sanitize(
    before: list[BaseMessage], after: list[BaseMessage]
) -> str:
    """诊断工具：对比 sanitize 前后差异，返回被移除消息的简要描述。"""
    after_set = set(id(m) for m in after)
    removed = []
    for m in before:
        if id(m) not in after_set:
            if isinstance(m, ToolMessage):
                removed.append(
                    f"ToolMessage(tool_call_id={getattr(m, 'tool_call_id', '?')[:20]}, "
                    f"content_len={len(str(m.content)) if m.content else 0})"
                )
            elif isinstance(m, AIMessage):
                tc_ids = [tc.get("id", "?")[:12] for tc in (m.tool_calls or [])]
                removed.append(
                    f"AIMessage(content_len={len(str(m.content)) if m.content else 0}, "
                    f"tool_calls={tc_ids})"
                )
            elif isinstance(m, SystemMessage):
                removed.append(f"SystemMessage(...)")
            elif hasattr(m, "content"):
                removed.append(f"{type(m).__name__}(...)")
            else:
                removed.append(f"{type(m).__name__}")
    return " | ".join(removed) if removed else ""


def is_visible_to_frontend(msg: dict) -> bool:
    """
    判断消息是否应对前端可见。

    排除 system_prompt 类型的消息（仅 LLM 上下文使用），
    其余所有类型（user/assistant/tool/compression_divider 等）均对前端可见。
    兼容旧格式：role="system" 且无 display 的消息也隐藏。
    """
    msg_type = msg.get("type", msg.get("role", ""))
    if msg_type == "system_prompt":
        return False
    # 旧格式兼容：纯 role="system" 无 display 字段的消息为 LLM 专用
    if not msg.get("type") and msg.get("role") == "system" and not msg.get("display"):
        return False
    return True


def trim_langchain_messages(messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    """
    当消息过长时，截断早期消息（保留 system 消息和最近的对话）。
    截断后会修复不完整的 tool_calls/tool 消息配对，避免 API 400 错误。

    Args:
        messages: LangChain 消息列表
        max_tokens: 最大 token 数

    Returns:
        截断后的消息列表
    """
    if not messages:
        return messages

    total = sum(estimate_tokens(str(m.content or "")) for m in messages)
    if total <= max_tokens:
        return _sanitize_tool_pairs(list(messages))

    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]

    kept = []
    current_tokens = sum(estimate_tokens(str(m.content or "")) for m in system_msgs)
    for msg in reversed(non_system):
        msg_tokens = estimate_tokens(str(msg.content or ""))
        if current_tokens + msg_tokens > max_tokens:
            break
        kept.append(msg)
        current_tokens += msg_tokens
    kept.reverse()  # append+reverse 比 insert(0) 性能更优（O(n) vs O(n²)）

    result = system_msgs + kept

    # 截断后修复不完整的 tool_calls / tool 消息配对
    result = _sanitize_tool_pairs(result)

    return result
