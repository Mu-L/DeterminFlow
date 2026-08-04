"""
LLM 配置工厂 - 创建 ChatOpenAI 实例供 LangGraph 图使用
"""
import asyncio
import logging
import math
from typing import Any, Literal

from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import ensure_config, merge_configs
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models import base as openai_base
from openai import BadRequestError

import os
import warnings

logger = logging.getLogger(__name__)


class ModelCredentialNotConfiguredError(ValueError):
    """The selected model provider has no usable API credential."""


class ModelConfigurationNotAvailableError(ValueError):
    """No configured provider/model is available for an actual LLM call."""


# ============================================================
# Monkey Patch 版本兼容性检查
# ============================================================

_EXPECTED_LANGCHAIN_OPENAI_VERSIONS = {"1.3"}  # 与 requirements.txt 锁定版本一致
try:
    import langchain_openai as _lc_openai_pkg
    _lc_version = getattr(_lc_openai_pkg, "__version__", "unknown")
    _lc_major_minor = ".".join(_lc_version.split(".")[:2]) if _lc_version != "unknown" else "unknown"
    if _lc_major_minor not in _EXPECTED_LANGCHAIN_OPENAI_VERSIONS:
        logger.warning(
            f"langchain_openai {_lc_version} 未在已验证版本 {_EXPECTED_LANGCHAIN_OPENAI_VERSIONS} 中，"
            f"Monkey Patch 可能不兼容。若出现 AttributeError 或行为异常，请降级 langchain-openai。"
        )
    else:
        logger.debug(f"langchain_openai {_lc_version} 版本检查通过")
except ImportError:
    logger.warning("langchain_openai 未安装，Monkey Patch 跳过")


# ============================================================
# Monkey Patch 1: 消息转换 - 将 reasoning_content 传回 API
# ============================================================

_original_convert_message_to_dict = openai_base._convert_message_to_dict


def _patched_convert_message_to_dict(
    message: BaseMessage,
    api: Literal["chat/completions", "responses"] = "chat/completions",
) -> dict:
    """
    修补版本的消息转换函数，支持 DeepSeek V4 的 reasoning_content

    DeepSeek V4 思考模式要求：
    - 在多轮对话中，必须将之前的 reasoning_content 传回 API
    - reasoning_content 应该在 AIMessage 的 additional_kwargs 中
    """
    message_dict = _original_convert_message_to_dict(message, api)

    # 如果是 AIMessage 且包含 reasoning_content，添加到消息字典中
    if isinstance(message, AIMessage) and hasattr(message, "additional_kwargs"):
        reasoning_content = message.additional_kwargs.get("reasoning_content")
        if reasoning_content:
            message_dict["reasoning_content"] = reasoning_content
            logger.debug(f"[PATCH 1] 添加 reasoning_content 到消息字典 (长度: {len(reasoning_content)})")

    return message_dict


openai_base._convert_message_to_dict = _patched_convert_message_to_dict


# ============================================================
# Monkey Patch 2: 响应解析 - 从 API 响应提取 reasoning_content
# ============================================================

_original_convert_delta_to_message_chunk = openai_base._convert_delta_to_message_chunk


def _patched_convert_delta_to_message_chunk(
    _dict: Any,
    default_class: Any,
) -> Any:
    """
    修补版本的 delta 转换函数，支持提取 reasoning_content

    DeepSeek V4 在流式响应中会返回 reasoning_content 字段，
    需要：
    1. 将其作为 chunk 的直接属性（用于流式显示）
    2. 将其添加到 additional_kwargs（用于多轮对话传回）
    """
    chunk = _original_convert_delta_to_message_chunk(_dict, default_class)

    # 如果响应中包含 reasoning_content，进行双重处理
    if "reasoning_content" in _dict:
        reasoning_content = _dict.get("reasoning_content")
        if reasoning_content:
            # 1. 作为 chunk 的直接属性（用于 session.py 中的流式提取）
            # 使用 setattr 防止 frozen/cached_property 对象抛出 AttributeError
            try:
                setattr(chunk, "reasoning_content", reasoning_content)
            except AttributeError:
                pass  # 某些 frozen/cached_property 对象无法设置额外属性

            # 2. 添加到 additional_kwargs（用于多轮对话时传回 API）
            if hasattr(chunk, "additional_kwargs"):
                chunk.additional_kwargs["reasoning_content"] = reasoning_content

    return chunk


openai_base._convert_delta_to_message_chunk = _patched_convert_delta_to_message_chunk


# ============================================================
# 应用补丁完成
# ============================================================

logger.info("已应用 DeepSeek V4 reasoning_content 支持补丁")


def _resolve_provider_config(
    model_override: str,
    model_manager: Any,
) -> tuple[str, str, str, str, dict]:
    """解析 provider_id:model_name 格式，返回 (provider_id, model_name, api_key, base_url, provider_config)。

    Raises:
        ValueError: provider 不存在或格式不正确
    """
    provider_id, model_name = model_override.split(":", 1)
    provider_config = model_manager.get_provider(provider_id)
    if not provider_config:
        raise ModelConfigurationNotAvailableError(
            f"供应商 {provider_id} 不存在；请在设置页选择可用模型"
        )
    if model_name not in provider_config.get("models", []):
        raise ModelConfigurationNotAvailableError(
            f"模型 {model_override} 未配置；请在设置页选择可用模型"
        )
    api_key = provider_config.get("api_key", "")
    if not api_key:
        raise ModelCredentialNotConfiguredError(
            f"Provider {provider_id} 的 api_key 未配置；"
            "请在模型设置页面填写，或在 models_config.json 中引用环境变量"
        )
    return provider_id, model_name, api_key, provider_config["base_url"], provider_config


def _merge_params(
    model_manager: Any,
    provider_id: str,
    provider_config: dict,
    model_params: dict | None,
    kwargs: dict,
) -> dict:
    """合并模型参数和 provider 超参数到 kwargs（原地修改）。

    合并优先级: agent model_params > defaults；provider hyperparams 作为 fallback。

    参数分类由 ModelManager 的 PROVIDER_CATEGORIES 注册表驱动：
    - chat_openai_params: 直接作为 ChatOpenAI kwargs（temperature / top_p / presence_penalty）
    - build_extra_body:   non-OpenAI 标准参数放入 extra_body（thinking / enable_thinking 等）
    """
    # 合并模型参数：agent model_params > defaults
    defaults = model_manager.get_default_params()
    merged_params = dict(defaults)
    if model_params:
        merged_params.update(
            {
                key: value
                for key, value in model_params.items()
                if value is not None or key == "response_format"
            }
        )

    # 从 category 配置获取应作为 ChatOpenAI kwargs 的参数列表
    cat_config = model_manager.get_category_params_config(provider_id)
    if cat_config:
        for param_name in cat_config.get("chat_openai_params", []):
            if param_name in merged_params:
                kwargs.setdefault(param_name, merged_params[param_name])

    # 构建 extra_body（thinking / enable_thinking / thinking_budget 等）
    extra_body = model_manager.build_extra_body(provider_id, merged_params)

    # 合并 provider 超参数作为 fallback（agent model_params 已通过 setdefault 优先）
    hyperparams = provider_config.get("hyperparameter_values", {})
    if "max_completion_tokens" in hyperparams:
        kwargs.setdefault("max_tokens", hyperparams["max_completion_tokens"])
    if "frequency_penalty" in hyperparams:
        kwargs.setdefault("frequency_penalty", hyperparams["frequency_penalty"])
    if "presence_penalty" in hyperparams:
        kwargs.setdefault("presence_penalty", hyperparams["presence_penalty"])

    # 传递 response_format（如 {"type": "json_object"}），通过 model_kwargs 透传给 ChatOpenAI
    if merged_params.get("response_format"):
        model_kwargs = kwargs.setdefault("model_kwargs", {})
        model_kwargs.setdefault("response_format", merged_params["response_format"])

    # Reasoning-heavy models may legitimately pause between parsed stream
    # chunks. Keep the timeout explicit in the Agent definition so the
    # effective runtime/provenance snapshot records the actual value.
    if "stream_chunk_timeout" in merged_params:
        timeout = merged_params["stream_chunk_timeout"]
        if timeout is not None:
            if isinstance(timeout, bool):
                raise ValueError(
                    "stream_chunk_timeout must be a finite positive number"
                )
            try:
                timeout = float(timeout)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "stream_chunk_timeout must be a finite positive number"
                ) from exc
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError(
                    "stream_chunk_timeout must be a finite positive number"
                )
        kwargs.setdefault("stream_chunk_timeout", timeout)

    kwargs["extra_body"] = extra_body
    return merged_params


def _resolve_model_override(model_override: str | None, model_manager) -> str:
    """
    将 model_override 解析为标准 "provider_id:model_name" 格式。

    支持三种输入：
    1. 新格式 "provider_id:model_name" → 直接返回
    2. 纯模型名 → 在所有 provider 的 models 列表中查找匹配，返回 "pid:model_name"
    3. None → 使用默认模型

    Returns:
        "provider_id:model_name" 格式的字符串

    Raises:
        ValueError: 无法解析时抛出
    """
    # 新格式：直接返回
    if model_override and ":" in model_override:
        return model_override

    # 旧格式：纯模型名，按名称查找供应商
    if model_override:
        for pid, pconfig in model_manager.get_all_providers().items():
            if model_override in pconfig.get("models", []):
                return f"{pid}:{model_override}"

    # 使用默认模型（从 agents_config.json main agent 读取）
    default_model = model_manager.get_default_model()
    if default_model and ":" in default_model:
        return default_model

    raise ModelConfigurationNotAvailableError(
        "尚未配置可用模型；请先在设置页添加供应商和模型"
    )


def _wrap_llm_with_retry(llm: ChatOpenAI, retry_config: dict) -> ChatOpenAI:
    """
    为 ChatOpenAI 实例的 ainvoke 和 astream 方法注入自动重试机制。

    当 LLM 调用失败（服务端错误、限流、网络不可达等），自动按配置的重试次数
    和间隔时间重试，最后一次失败则抛出原始异常。

    Args:
        llm: ChatOpenAI 实例
        retry_config: {"max_retries": 3, "delays": [5, 15, 30]}

    Returns:
        包装后的 ChatOpenAI 实例（就地修改）
    """
    max_retries = retry_config.get("max_retries", 3)
    delays = retry_config.get("delays", [5, 15, 30])

    if max_retries <= 0:
        return llm

    # 确保 delays 长度足够（不足时用最后一个值填充）
    delays = list(delays)
    while len(delays) < max_retries:
        delays.append(delays[-1] if delays else 30)

    # ---- 包装 ainvoke ----
    original_ainvoke = llm.ainvoke

    class _StreamingCallbackTracker(BaseCallbackHandler):
        """Observe chunks emitted by ``ainvoke`` through LangChain callbacks."""

        def __init__(self):
            self.chunk_count = 0

        def on_llm_new_token(self, _token, **_kwargs):
            self.chunk_count += 1

    def _tracked_ainvoke_arguments(args, kwargs, tracker):
        tracked_args = list(args)
        tracked_kwargs = dict(kwargs)
        tracker_config = {"callbacks": [tracker]}
        if tracked_args:
            tracked_args[0] = merge_configs(
                ensure_config(tracked_args[0]),
                tracker_config,
            )
        else:
            tracked_kwargs["config"] = merge_configs(
                ensure_config(tracked_kwargs.get("config")),
                tracker_config,
            )
        return tuple(tracked_args), tracked_kwargs

    def _mark_failed_provider_usage(error, *, partial_stream_emitted):
        # Failed provider calls frequently have no final usage payload. Keep
        # this explicit on the propagated error in addition to the audit log.
        for attribute, value in (
            ("llm_partial_stream_emitted", partial_stream_emitted),
            ("llm_provider_usage_status", "unavailable_on_failed_attempt"),
            ("llm_retry_suppressed", partial_stream_emitted),
        ):
            try:
                setattr(error, attribute, value)
            except (AttributeError, TypeError):
                pass

    async def ainvoke_with_retry(input, *args, **kwargs):
        last_error = None
        for attempt in range(max_retries + 1):
            tracker = _StreamingCallbackTracker()
            tracked_args, tracked_kwargs = _tracked_ainvoke_arguments(
                args,
                kwargs,
                tracker,
            )
            try:
                return await original_ainvoke(
                    input,
                    *tracked_args,
                    **tracked_kwargs,
                )
            except BadRequestError as error:
                # 400 错误是永久性错误（输入格式非法），重试无意义，直接抛出
                _mark_failed_provider_usage(
                    error,
                    partial_stream_emitted=tracker.chunk_count > 0,
                )
                logger.error(
                    "LLM ainvoke BadRequestError (400)，不重试 "
                    "(provider_usage_status=unavailable_on_failed_attempt)"
                )
                raise
            except Exception as e:
                last_error = e
                partial_stream_emitted = tracker.chunk_count > 0
                _mark_failed_provider_usage(
                    e,
                    partial_stream_emitted=partial_stream_emitted,
                )
                if partial_stream_emitted:
                    logger.error(
                        "LLM ainvoke 在 streaming callback 输出开始后中断；"
                        "拒绝原地重试以避免拼接重复响应 "
                        "(retry_suppressed=partial_stream, "
                        "provider_usage_status=unavailable_on_failed_attempt, "
                        f"chunks={tracker.chunk_count}, "
                        f"error={type(e).__name__}: {e!s})"
                    )
                    raise
                if attempt < max_retries:
                    delay = delays[attempt]
                    logger.warning(
                        f"LLM ainvoke 失败，将重试 "
                        f"(attempt {attempt + 1}/{max_retries}, "
                        f"delay={delay}s, error={type(e).__name__}: {e!s})"
                        "; provider_usage_status=unavailable_on_failed_attempt"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"LLM ainvoke 失败，已耗尽所有重试 "
                        f"(max_retries={max_retries}, "
                        f"error={type(last_error).__name__}: {last_error!s})"
                        "; provider_usage_status=unavailable_on_failed_attempt"
                    )
                    raise

    object.__setattr__(llm, 'ainvoke', ainvoke_with_retry)

    # ---- 包装 astream ----
    original_astream = llm.astream

    async def astream_with_retry(input, *args, **kwargs):
        last_error = None
        for attempt in range(max_retries + 1):
            yielded_chunk = False
            try:
                async for chunk in original_astream(input, *args, **kwargs):
                    yielded_chunk = True
                    yield chunk
                return  # 流正常完成
            except BadRequestError:
                # 400 错误是永久性错误（输入格式非法），重试无意义，直接抛出
                logger.error(f"LLM astream BadRequestError (400)，不重试")
                raise
            except Exception as e:
                last_error = e
                # Once a chunk reached the consumer, restarting this request
                # would append a second response to the first partial one.
                # Let the enclosing Workflow attempt restart from a clean
                # output boundary instead.
                if yielded_chunk:
                    logger.error(
                        "LLM astream 在输出开始后中断；拒绝原地重试以避免拼接重复响应 "
                        f"(error={type(e).__name__}: {e!s})"
                    )
                    raise
                if attempt < max_retries:
                    delay = delays[attempt]
                    logger.warning(
                        f"LLM astream 失败，将重试 "
                        f"(attempt {attempt + 1}/{max_retries}, "
                        f"delay={delay}s, error={type(e).__name__}: {e!s})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"LLM astream 失败，已耗尽所有重试 "
                        f"(max_retries={max_retries}, "
                        f"error={type(last_error).__name__}: {last_error!s})"
                    )
                    raise

    object.__setattr__(llm, 'astream', astream_with_retry)

    logger.info(f"LLM 重试机制已注入: max_retries={max_retries}, delays={delays[:max_retries]}")
    return llm


class DeferredChatModel:
    """Delay model construction until a request needs the provider credential."""

    def __init__(
        self,
        factory_options: dict[str, Any],
        *,
        tools: list[Any] | None = None,
        bind_options: dict[str, Any] | None = None,
    ) -> None:
        self._factory_options = dict(factory_options)
        self._tools = list(tools) if tools is not None else None
        self._bind_options = dict(bind_options or {})
        self._delegate: Any = None

    def bind_tools(self, tools: list[Any], **kwargs) -> "DeferredChatModel":
        return DeferredChatModel(
            self._factory_options,
            tools=tools,
            bind_options=kwargs,
        )

    def _resolve(self) -> Any:
        if self._delegate is None:
            delegate = create_llm(**self._factory_options)
            if self._tools is not None:
                delegate = delegate.bind_tools(self._tools, **self._bind_options)
            self._delegate = delegate
        return self._delegate

    def invoke(self, *args, **kwargs):
        return self._resolve().invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self._resolve().ainvoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        yield from self._resolve().stream(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        async for chunk in self._resolve().astream(*args, **kwargs):
            yield chunk


def create_llm(
    model_override: str | None = None,
    model: str | None = None,
    streaming: bool = True,
    model_params: dict | None = None,
    **kwargs,
) -> ChatOpenAI:
    """
    创建 ChatOpenAI 实例。

    Args:
        model_override: 模型覆盖，格式 "provider_id:model_name"（新格式）或纯模型名（旧格式）
        model: 模型名称（旧格式，向后兼容）
        streaming: 是否支持流式输出，默认 True
        model_params: 模型参数字典，包含 thinking_enabled / reasoning_effort / temperature / top_p
                     未设置时从 models_config.json 的 default_params 继承
        **kwargs: 传递给 ChatOpenAI 的额外参数

    Returns:
        配置好的 ChatOpenAI 实例
    """
    from src.core.model_manager import get_model_manager

    model_manager = get_model_manager()

    # 第一阶段：解析为标准 "provider_id:model_name" 格式（无递归）
    resolved = _resolve_model_override(model_override, model_manager)

    # 第二阶段：构造 ChatOpenAI 实例
    provider_id, model_name, api_key, base_url, provider_config = _resolve_provider_config(
        resolved, model_manager
    )
    merged_params = _merge_params(model_manager, provider_id, provider_config, model_params, kwargs)

    logger.info(
        f"使用 ModelManager 配置: provider={provider_id}, model={model_name}, "
        f"base_url={base_url}, thinking={merged_params.get('thinking_enabled')}, "
        f"temperature={merged_params.get('temperature')}"
    )

    # 流式输出时，请求 API 返回 token usage（OpenAI/DeepSeek 兼容）
    if streaming:
        model_kwargs = kwargs.get("model_kwargs", {})
        if "stream_options" not in model_kwargs:
            model_kwargs["stream_options"] = {"include_usage": True}
            kwargs["model_kwargs"] = model_kwargs

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        streaming=streaming,
        **kwargs,
    )

    # 注入重试机制
    retry_config = model_manager.get_retry_config()
    _wrap_llm_with_retry(llm, retry_config)

    logger.info(
        f"LLM 实例已创建: model={model_name}, temperature={kwargs.get('temperature', 'default')}, "
        f"streaming={streaming}"
    )
    return llm


def create_startup_llm(
    model_override: str | None = None,
    model: str | None = None,
    streaming: bool = True,
    model_params: dict | None = None,
    **kwargs,
) -> ChatOpenAI | DeferredChatModel:
    """Create the startup model, deferring only missing-credential failures."""
    options = {
        "model_override": model_override,
        "model": model,
        "streaming": streaming,
        "model_params": model_params,
        **kwargs,
    }
    try:
        return create_llm(**options)
    except (ModelCredentialNotConfiguredError, ModelConfigurationNotAvailableError):
        logger.warning(
            "模型或凭据尚未配置，服务将继续启动；可在模型设置页面完成配置"
        )
        return DeferredChatModel(options)
