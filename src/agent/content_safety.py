"""
内容安全诊断模块 - 当 DeepSeek API 返回 Content Exists Risk 时，通过二分排除法定位触发审查的消息
"""
import logging
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from openai import BadRequestError, OpenAIError

logger = logging.getLogger(__name__)

CONTENT_RISK_MSG = "Content Exists Risk"


@dataclass
class DiagnosticResult:
    """诊断结果"""
    triggered_by: str            # "system_prompt" | "user_message" | "injection_content" | "conversation_history" | "unknown"
    identified_message_type: str  # "SystemMessage" | "HumanMessage" 等
    message_preview: str          # 问题消息前 200 字符
    summary: str                  # 人类可读的诊断摘要
    diagnostic_steps: list[dict] = field(default_factory=list)  # [{step, subset_desc, result}]


class ContentSafetyDiagnostic:
    """内容安全诊断器，通过二分排除法定位触发 DeepSeek 安全审查的消息"""

    def __init__(self, llm: ChatOpenAI, messages: list[BaseMessage]):
        """
        Args:
            llm: LLM 客户端（与 session 使用相同配置）
            messages: 触发 Content Exists Risk 时的完整消息快照
        """
        self._llm = llm
        self._messages = messages
        self._steps: list[dict] = []

    async def diagnose(self) -> DiagnosticResult:
        """主入口：按序测试各消息子集，返回诊断结果"""
        msgs = self._messages

        if not msgs:
            return DiagnosticResult(
                triggered_by="unknown",
                identified_message_type="N/A",
                message_preview="",
                summary="消息列表为空，无法诊断",
                diagnostic_steps=self._steps,
            )

        # 分离 system prompt、用户消息、历史消息
        sys_msg = None
        user_msg = None
        history_msgs: list[BaseMessage] = []

        for msg in msgs:
            if isinstance(msg, SystemMessage):
                sys_msg = msg
            elif isinstance(msg, HumanMessage):
                user_msg = msg
            else:
                history_msgs.append(msg)

        #
        # Phase 1: 测试 [sys_msg + user_msg] 是否触发
        #
        phase1_msgs = []
        if sys_msg:
            phase1_msgs.append(sys_msg)
        if user_msg:
            phase1_msgs.append(user_msg)

        phase1_pass = await self._test_subset(phase1_msgs, "系统提示词 + 用户消息（不含历史）")
        self._add_step(1, "系统提示词 + 用户消息", "通过" if phase1_pass else "拦截")

        if phase1_pass:
            # 系统提示词和用户消息都没问题，问题在历史消息
            if not history_msgs:
                return DiagnosticResult(
                    triggered_by="unknown",
                    identified_message_type="N/A",
                    message_preview="",
                    summary="无法定位：基础消息和系统提示词均正常，但无历史消息可排查",
                    diagnostic_steps=self._steps,
                )
            return await self._diagnose_history(sys_msg, user_msg, history_msgs)

        #
        # Phase 2: [sys_msg + user_msg] 触发 → 测试单独 sys_msg
        #
        sys_only = [sys_msg] if sys_msg else []
        sys_pass = await self._test_subset(sys_only, "仅系统提示词")
        self._add_step(2, "仅系统提示词", "通过" if sys_pass else "拦截")

        if not sys_pass and sys_msg:
            # 系统提示词本身触发审查
            preview = str(sys_msg.content)[:200] if hasattr(sys_msg, 'content') else ""
            return DiagnosticResult(
                triggered_by="system_prompt",
                identified_message_type="SystemMessage",
                message_preview=preview,
                summary="系统提示词（System Prompt）触发了 DeepSeek 安全审查拦截，建议检查并精简提示词内容",
                diagnostic_steps=self._steps,
            )

        #
        # Phase 3: 系统提示词通过 → 用户消息有问题
        #
        if not user_msg:
            # 边缘情况：没有用户消息但触发了审查
            return DiagnosticResult(
                triggered_by="unknown",
                identified_message_type="N/A",
                message_preview="",
                summary="无法定位：系统提示词通过排查，但无用户消息可进一步分析",
                diagnostic_steps=self._steps,
            )

        # 测试用户消息是否包含了 SYSTEM_INJECTION
        user_content = str(user_msg.content) if hasattr(user_msg, 'content') else ""
        has_injection = "<SYSTEM_INJECTION>" in user_content

        if has_injection:
            # 分离注入内容，测试纯用户消息
            pure_user_content = user_content
            marker = "<USER_MESSAGE>"
            marker_idx = user_content.find(marker)
            if marker_idx != -1:
                pure_user_content = user_content[marker_idx + len(marker):].strip()
            else:
                # 没有 USER_MESSAGE 标记，去掉 SYSTEM_INJECTION 部分
                inj_end = user_content.find("</SYSTEM_INJECTION>")
                if inj_end == -1:
                    inj_end = user_content.find("<USER_MESSAGE>")
                if inj_end != -1:
                    pure_user_content = user_content[inj_end:].lstrip()

            pure_user_msg = HumanMessage(content=pure_user_content)

            test_msgs = []
            if sys_msg:
                test_msgs.append(sys_msg)
            test_msgs.append(pure_user_msg)

            pure_pass = await self._test_subset(test_msgs, "系统提示词 + 用户消息（去注入）")
            self._add_step(3, "系统提示词 + 用户消息（移除注入内容）", "通过" if pure_pass else "拦截")

            if pure_pass:
                # 注入内容触发的
                inj_preview = user_content[:200]
                return DiagnosticResult(
                    triggered_by="injection_content",
                    identified_message_type="HumanMessage (SYSTEM_INJECTION)",
                    message_preview=inj_preview,
                    summary="用户消息中自动注入的系统上下文内容（规则/技能/工作流元信息等）触发了 DeepSeek 安全审查拦截，建议检查对应的注入配置",
                    diagnostic_steps=self._steps,
                )
            else:
                # 用户消息本身触发的
                return DiagnosticResult(
                    triggered_by="user_message",
                    identified_message_type="HumanMessage",
                    message_preview=pure_user_content[:200],
                    summary="用户输入的消息内容触发了 DeepSeek 安全审查拦截，建议修改消息内容后重试",
                    diagnostic_steps=self._steps,
                )
        else:
            # 用户消息没有注入内容，就是它本身的问题
            return DiagnosticResult(
                triggered_by="user_message",
                identified_message_type="HumanMessage",
                message_preview=user_content[:200],
                summary="用户输入的消息内容（无注入内容）触发了 DeepSeek 安全审查拦截，建议修改消息内容后重试",
                diagnostic_steps=self._steps,
            )

    async def _diagnose_history(
        self,
        sys_msg: BaseMessage | None,
        user_msg: BaseMessage | None,
        history_msgs: list[BaseMessage],
    ) -> DiagnosticResult:
        """对历史消息做二分排除，定位具体是哪条历史消息触发审查"""
        left, right = 0, len(history_msgs) - 1
        step_counter = len(self._steps) + 1

        while left <= right:
            mid = (left + right) // 2
            # 测试包含历史 [left..mid] 的消息集
            test_msgs = []
            if sys_msg:
                test_msgs.append(sys_msg)
            test_msgs.extend(history_msgs[left:mid + 1])
            if user_msg:
                test_msgs.append(user_msg)

            desc = f"二分排除 - 包含历史消息 [{left}..{mid}] (共 {mid - left + 1} 条)"
            blocked = not await self._test_subset(test_msgs, desc)
            self._add_step(step_counter, desc, "通过" if not blocked else "拦截")
            step_counter += 1

            if blocked:
                # 问题在 [left..mid] 范围内
                if left == mid:
                    # 找到精确消息
                    msg = history_msgs[left]
                    msg_type = type(msg).__name__
                    preview = str(msg.content)[:200] if hasattr(msg, 'content') else ""
                    return DiagnosticResult(
                        triggered_by="conversation_history",
                        identified_message_type=msg_type,
                        message_preview=preview,
                        summary=f"对话历史中的第 {left + 1} 条消息（{msg_type}）触发了 DeepSeek 安全审查拦截",
                        diagnostic_steps=self._steps,
                    )
                right = mid
            else:
                # 问题在 [mid+1..right]
                left = mid + 1

        # 所有历史消息都通过了，但全量消息仍触发 → 可能是组合效应
        return DiagnosticResult(
            triggered_by="conversation_history",
            identified_message_type="组合效应",
            message_preview="",
            summary="对话历史中单独排查各消息均未触发，可能是多条消息组合在一起触发了审查，建议尝试清理上下文",
            diagnostic_steps=self._steps,
        )

    async def _test_subset(self, msgs: list[BaseMessage], description: str) -> bool:
        """
        测试消息子集是否触发 Content Exists Risk。

        Returns:
            True = 通过（未触发审查），False = 拦截（触发审查）
        """
        if not msgs:
            return True

        logger.info(f"[ContentSafetyDiagnostic] 测试子集: {description} ({len(msgs)} 条消息)")

        try:
            await self._llm.ainvoke(msgs)
            return True
        except BadRequestError as e:
            error_message = str(e)
            if CONTENT_RISK_MSG in error_message:
                logger.info(f"[ContentSafetyDiagnostic] 子集触发审查: {description}")
                return False
            # 其他 BadRequestError（非安全审查），视为通过但记录警告
            logger.warning(f"[ContentSafetyDiagnostic] 子集返回非审查错误: {description}: {error_message}")
            return True
        except (ConnectionError, ConnectionRefusedError, OSError, TimeoutError) as e:
            # 网络层异常不应视为"通过"，标记为诊断失败
            logger.error(f"[ContentSafetyDiagnostic] 子集测试网络异常（诊断失败）: {description}: {e}")
            return False
        except OpenAIError as e:
            # API 级别错误（RateLimitError、AuthenticationError 等）不应视为"通过"
            logger.error(f"[ContentSafetyDiagnostic] 子集测试 API 错误（诊断失败）: {description}: {type(e).__name__}: {e}")
            return False
        except Exception as e:
            logger.warning(f"[ContentSafetyDiagnostic] 子集测试异常: {description}: {e}")
            return True

    def _add_step(self, step: int, subset_desc: str, result: str) -> None:
        self._steps.append({
            "step": step,
            "subset": subset_desc,
            "result": result,
        })
