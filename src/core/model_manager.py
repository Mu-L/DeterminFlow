"""
多供应商模型管理器 - 管理 DeepSeek、MiMo 等供应商的配置

配置存储在 config/models_config.json，API Key 优先从环境变量解析。
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, List, Any

from dotenv import load_dotenv, dotenv_values
from src.environment import get_determinflow_env

logger = logging.getLogger(__name__)

# get_default_model() 的 mtime 缓存：避免每次调用都读磁盘
_agents_config_cache: dict = {"mtime": 0.0, "model": None}

# 默认最大上下文 token 数，当模型/供应商未配置 maxContextTokens 时使用
# 现代模型通常支持 128K+，但 8000 作为保守默认值避免意外消耗过多资源
DEFAULT_MAX_CONTEXT_TOKENS = 8000

# ============================================================
# Provider Category 注册表 — 不同供应商的参数格式差异在此集中管理
# ============================================================
# 每个 category 定义了：
#   - build_extra_body: 根据合并后的 model_params 构建 extra_body 的函数
#   - chat_openai_params: model_params 中哪些字段直接作为 ChatOpenAI kwargs
# 新增 provider 只需在此注册一个 category 即可，无需修改 llm_client.py

def _build_ds_extra_body(params: dict) -> dict:
    """DeepSeek / MiMo 思考参数：thinking.type（extra_body） + reasoning_effort（顶层参数）

    根据 DeepSeek 官方 API 文档，reasoning_effort 必须是顶层参数，
    而非嵌套在 thinking.effort 内。否则 API 会静默忽略。
    """
    extra = {}
    thinking_enabled = params.get("thinking_enabled", True)
    extra["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
    if thinking_enabled:
        extra["reasoning_effort"] = params.get("reasoning_effort", "high")
    return extra


def _build_qwen_extra_body(params: dict) -> dict:
    """Qwen 思考参数：enable_thinking + thinking_budget（None 时不传）"""
    extra = {}
    thinking_enabled = params.get("thinking_enabled")
    if thinking_enabled is not None:
        extra["enable_thinking"] = thinking_enabled
    thinking_budget = params.get("thinking_budget")
    if thinking_budget is not None:
        extra["thinking_budget"] = thinking_budget
    return extra


def _build_gpt_extra_body(params: dict) -> dict:
    """GPT 推理参数：reasoning_effort 作为顶层参数（low/medium/high/xhigh）

    GPT-5 系列始终启用推理，无需 thinking_enabled 开关。
    reasoning_effort 默认 medium。
    """
    extra = {}
    extra["reasoning_effort"] = params.get("reasoning_effort", "medium")
    return extra


PROVIDER_CATEGORIES: dict[str, dict] = {
    "ds": {
        "display_name": "DeepSeek",
        "build_extra_body": _build_ds_extra_body,
        "chat_openai_params": ["temperature", "top_p", "presence_penalty"],
    },
    "mimo": {
        "display_name": "MiMo",
        "build_extra_body": _build_ds_extra_body,
        "chat_openai_params": ["temperature", "top_p", "presence_penalty"],
    },
    "qwen": {
        "display_name": "Qwen",
        "build_extra_body": _build_qwen_extra_body,
        "chat_openai_params": ["temperature", "top_p", "presence_penalty"],
    },
    "gpt": {
        "display_name": "GPT",
        "build_extra_body": _build_gpt_extra_body,
        "chat_openai_params": ["temperature", "top_p", "presence_penalty"],
    },
}


# 供应商超参数 Schema 定义（硬编码）
# thinking/reasoning_effort/temperature/top_p/presence_penalty/thinking_budget 已提升到 Agent 级别（model_params），
# Provider 仅保留与模型能力/行为相关的参数（max_completion_tokens / frequency_penalty）
PROVIDER_SCHEMAS = {
    "deepseek": {
        "display_name": "DeepSeek",
        "default_base_url": "https://api.deepseek.com",
        "hyperparams": {
            "max_completion_tokens": {
                "type": "number",
                "min": 1,
                "max": 131072,
                "default": 32768,
                "label": "最大输出Token"
            }
        }
    },
    "mimo": {
        "display_name": "Xiaomi MiMo",
        "default_base_url": "https://api.xiaomimimo.com/v1",
        "hyperparams": {
            "max_completion_tokens": {
                "type": "number",
                "min": 1,
                "max": 131072,
                "default": 32768,
                "label": "最大输出Token"
            },
            "frequency_penalty": {
                "type": "number",
                "min": -2.0,
                "max": 2.0,
                "default": 0,
                "label": "频率惩罚"
            }
        }
    },
    "alibaba": {
        "display_name": "阿里百炼",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "hyperparams": {
            "max_completion_tokens": {
                "type": "number",
                "min": 1,
                "max": 131072,
                "default": 32768,
                "label": "最大输出Token"
            },
            "thinking_budget": {
                "type": "number",
                "min": 1,
                "max": 131072,
                "default": None,
                "label": "思考Token上限"
            }
        }
    }
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_ROOT = Path(
    get_determinflow_env("CONFIG_DIR", str(_PROJECT_ROOT / "config"))
).expanduser().resolve()
_DEFAULT_CONFIG_PATH = str(_CONFIG_ROOT / "models_config.json")
_AGENTS_CONFIG_PATH = _CONFIG_ROOT / "agents_config.json"


class ModelManager:
    """多供应商模型管理器"""

    def __init__(self, config_path: str = _DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """加载配置文件，如无则尝试从 .env 迁移或创建默认配置"""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # 尝试从 .env 迁移
        env_path = ".env"
        if os.path.exists(env_path):
            self.migrate_from_env(env_path)
            return self._load_config()

        # 创建默认配置
        return self._create_default_config()

    def _create_default_config(self) -> Dict:
        """创建默认配置"""
        config = {
            "default_params": {
                "thinking_enabled": True,
                "reasoning_effort": "high",
                "temperature": 0.7,
                "top_p": 1.0,
                "presence_penalty": 0.0,
                "thinking_budget": None,
            },
            "providers": {
                "deepseek": {
                    "category": "ds",
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "",
                    "api_key_env": "DEEPSEEK_API_KEY",  # pragma: allowlist secret
                    "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                    "hyperparameter_values": {
                        "max_completion_tokens": 32768
                    }
                }
            }
        }
        self.config = config
        self.save()
        return config

    def migrate_from_env(self, env_path: str) -> bool:
        """从 .env 迁移非敏感配置；API Key 仍保留在环境文件中。"""
        load_dotenv(env_path)

        # 使用 python-dotenv 解析 .env（正确处理引号、行内注释等）
        env_vars = dotenv_values(env_path)

        # 构建配置
        api_key_env = (
            "OPENAI_API_KEY"
            if env_vars.get("OPENAI_API_KEY")
            else "DEEPSEEK_API_KEY"
        )
        provider = {
            "category": "ds",
            "name": "DeepSeek",
            "base_url": env_vars.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
            "api_key": "",
            "api_key_env": api_key_env,
            "models": [env_vars.get("MODEL_NAME", "deepseek-v4-flash")],
            "hyperparameter_values": {
                "max_completion_tokens": 32768
            }
        }

        config = {
            "default_params": {
                "thinking_enabled": True,
                "reasoning_effort": "high",
                "temperature": 0.7,
                "top_p": 1.0,
                "presence_penalty": 0.0,
                "thinking_budget": None,
            },
            "providers": {"deepseek": provider}
        }
        self.config = config
        self.save()

        logger.info(f"已从 .env 迁移配置到 {self.config_path}")
        return True

    def save(self):
        """保存配置到 JSON 文件（原子写入：tmp + os.replace）"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix('.json.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            os.replace(str(tmp_path), str(self.config_path))
        except (IOError, OSError) as e:
            logger.error(f"保存模型配置失败: {self.config_path} | {e}")
            # 清理残留临时文件
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get_provider(self, provider_id: str) -> Optional[Dict]:
        """获取供应商配置"""
        provider = self.config.get("providers", {}).get(provider_id)
        if provider is None:
            return None
        resolved = dict(provider)
        env_name = resolved.get("api_key_env") or f"{provider_id.upper()}_API_KEY"
        env_value = os.getenv(env_name, "")
        if env_value:
            resolved["api_key"] = env_value
        return resolved

    def get_all_providers(self) -> Dict:
        """获取所有供应商"""
        return self.config.get("providers", {})

    # 允许通过 update_provider 修改的字段白名单
    _PROVIDER_UPDATE_KEYS = frozenset({
        "name", "display_name", "base_url", "api_key", "api_key_env", "models", "models_config",
        "hyperparameter_values",
    })

    def update_provider(self, provider_id: str, updates: Dict):
        """更新供应商配置（仅允许白名单字段）"""
        if provider_id not in self.config["providers"]:
            raise ValueError(f"Provider {provider_id} not found")
        filtered = {k: v for k, v in updates.items() if k in self._PROVIDER_UPDATE_KEYS}
        ignored = set(updates) - self._PROVIDER_UPDATE_KEYS
        if ignored:
            logger.warning(f"update_provider 忽略非白名单字段: {ignored} | provider={provider_id}")
        self.config["providers"][provider_id].update(filtered)
        self.save()

    def add_provider(self, provider_id: str, config: Dict):
        """添加新供应商"""
        if provider_id in self.config["providers"]:
            raise ValueError(f"Provider {provider_id} already exists")
        self.config["providers"][provider_id] = config
        self.save()

    def delete_provider(self, provider_id: str):
        """删除供应商"""
        if provider_id in self.config["providers"]:
            del self.config["providers"][provider_id]
            self.save()

    def get_all_models(self) -> List[str]:
        """返回所有模型标识 provider_id:model_name"""
        models = []
        for pid, pconfig in self.config.get("providers", {}).items():
            for model in pconfig.get("models", []):
                models.append(f"{pid}:{model}")
        return models

    def get_models_for_provider(self, provider_id: str) -> List[str]:
        """返回指定供应商的模型列表"""
        provider = self.get_provider(provider_id)
        if provider:
            return provider.get("models", [])
        return []

    def get_default_model(self) -> str:
        """返回默认模型标识（从 agents_config.json 的 main agent 定义读取，带 mtime 缓存）"""
        agents_config_path = _AGENTS_CONFIG_PATH
        try:
            if agents_config_path.exists():
                st = agents_config_path.stat()
                if st.st_mtime != _agents_config_cache["mtime"]:
                    with open(agents_config_path, 'r', encoding='utf-8') as f:
                        agents_config = json.load(f)
                    main_agent = agents_config.get("agents", {}).get("main", {})
                    _agents_config_cache["mtime"] = st.st_mtime
                    _agents_config_cache["model"] = main_agent.get("model")
                cached = _agents_config_cache["model"]
                if cached:
                    return cached
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"读取 agents_config.json 获取默认模型失败: {e}")

        # Fallback: 返回第一个可用的模型
        models = self.get_all_models()
        if models:
            logger.warning(f"未找到 agents_config.json main agent 的 model 配置，回退到: {models[0]}")
            return models[0]

        logger.error("没有任何模型可用，返回硬编码默认值")
        return "deepseek:deepseek-v4-flash"

    def get_default_params(self) -> Dict:
        """获取全局默认模型参数"""
        defaults = self.config.get("default_params", {})
        return {
            "thinking_enabled": defaults.get("thinking_enabled", True),
            "reasoning_effort": defaults.get("reasoning_effort", "high"),
            "temperature": defaults.get("temperature", 0.7),
            "top_p": defaults.get("top_p", 1.0),
            "presence_penalty": defaults.get("presence_penalty", 0.0),
            "thinking_budget": defaults.get("thinking_budget"),  # None = 不限制
            "response_format": defaults.get("response_format"),  # {"type": "json_object"} or null
        }

    def get_retry_config(self) -> dict:
        """获取 LLM 调用重试配置"""
        retry = self.config.get("retry", {})
        return {
            "max_retries": retry.get("max_retries", 3),
            "delays": retry.get("delays", [5, 15, 30]),
        }

    def get_provider_category(self, provider_id: str) -> str | None:
        """获取供应商的 category（ds/qwen/mimo），用于区分参数格式。

        category 从 provider 配置的 `category` 字段读取，
        不再依赖 provider_id 字符串推断。
        """
        provider = self.get_provider(provider_id)
        if provider:
            return provider.get("category")
        return None

    def get_category_params_config(self, provider_id: str) -> dict | None:
        """获取 category 的参数配置，包含 build_extra_body 函数和 chat_openai_params 列表。

        供 llm_client 查询：哪些 model_params 应作为 ChatOpenAI kwargs 直接透传。
        """
        category = self.get_provider_category(provider_id)
        if category:
            return PROVIDER_CATEGORIES.get(category)
        return None

    def build_extra_body(self, provider_id: str, model_params: Dict | None = None) -> Dict:
        """根据供应商 category 和 model_params 构建 extra_body。

        不同 category 的思考参数格式差异由 PROVIDER_CATEGORIES 注册表驱动：
        - ds / mimo → {"thinking": {"type": "enabled"/"disabled", "effort": "high"/"max"}}
        - qwen       → {"enable_thinking": true/false, "thinking_budget": N}

        Args:
            provider_id: 供应商 ID（如 "deepseek"、"mimo"、"alibaba"）
            model_params: 合并后的模型参数字典
        """
        params = model_params or self.get_default_params()
        category = self.get_provider_category(provider_id)
        if category and category in PROVIDER_CATEGORIES:
            return PROVIDER_CATEGORIES[category]["build_extra_body"](params)
        # 未注册的 category：不传思考参数，由模型自行决定
        return {}

    def get_provider_schema(self, provider_id: str) -> Optional[Dict]:
        """获取供应商的超参数 Schema"""
        return PROVIDER_SCHEMAS.get(provider_id)

    def get_all_schemas(self) -> Dict:
        """获取所有供应商的超参数 Schema"""
        return PROVIDER_SCHEMAS

    def get_max_context_tokens(self, model_override: str | None = None) -> int:
        """获取模型的最大上下文token数

        Args:
            model_override: 模型覆盖，格式 "provider_id:model_name"

        Returns:
            模型的最大上下文token数，默认返回8000
        """
        if model_override and ":" in model_override:
            provider_id, model_name = model_override.split(":", 1)
            provider = self.get_provider(provider_id)
            if provider:
                # 检查模型级别的maxContextTokens
                models_config = provider.get("models_config", {})
                if model_name in models_config:
                    return models_config[model_name].get("maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS)
                # 检查供应商级别的maxContextTokens
                if "maxContextTokens" in provider:
                    return provider["maxContextTokens"]

        # 使用默认模型的供应商配置
        default_model = self.get_default_model()
        if ":" in default_model:
            default_provider_id = default_model.split(":", 1)[0]
            default_provider = self.get_provider(default_provider_id)
            if default_provider and "maxContextTokens" in default_provider:
                return default_provider["maxContextTokens"]

        # 返回默认值
        return DEFAULT_MAX_CONTEXT_TOKENS

    def get_model_info(self, model_override: str | None = None) -> Dict:
        """获取模型的完整信息

        Args:
            model_override: 模型覆盖，格式 "provider_id:model_name"

        Returns:
            包含模型信息的字典
        """
        if model_override and ":" in model_override:
            provider_id, model_name = model_override.split(":", 1)
            provider = self.get_provider(provider_id)
            if provider:
                models_config = provider.get("models_config", {})
                model_config = models_config.get(model_name, {})
                return {
                    "provider_id": provider_id,
                    "model_name": model_name,
                    "maxContextTokens": model_config.get("maxContextTokens",
                                                        provider.get("maxContextTokens", DEFAULT_MAX_CONTEXT_TOKENS)),
                    "provider_name": provider.get("name", ""),
                }

        # 使用默认模型
        default_model = self.get_default_model()
        if ":" in default_model:
            return self.get_model_info(default_model)

        return {
            "provider_id": "deepseek",
            "model_name": "deepseek-v4-flash",
            "maxContextTokens": DEFAULT_MAX_CONTEXT_TOKENS,
            "provider_name": "DeepSeek",
        }


# 全局实例
_model_manager: Optional[ModelManager] = None


def get_model_manager() -> ModelManager:
    """获取全局 ModelManager 实例"""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager
