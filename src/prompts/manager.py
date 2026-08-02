"""
系统提示词管理器 - 统一管理所有 Agent 类型的 sections 配置和缓存

数据来源：
1. config/prompts_config.json（唯一配置源，包含 main/subagent/compressor）
2. data/system_prompt.json（main agent 缓存）
3. data/prompt_history.json（main agent 历史记录）

注意：所有提示词内容必须从 JSON 配置文件加载，代码中不包含任何硬编码默认值。
如果配置文件不存在或格式错误，将直接抛出异常。
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.config import SYSTEM_PROMPT_FILE, PROMPT_HISTORY_FILE, PROMPTS_CONFIG_FILE

logger = logging.getLogger(__name__)


def _sanitize_json(raw: str) -> str:
    """清洗 JSON 字符串中的常见格式错误，使其能被标准解析器接受。

    目前处理：
    - 尾部逗号（对象 / 数组最后一个元素后的逗号）
    """
    # 移除尾部逗号：逗号 + 可选空白 + } 或 ]
    sanitized = re.sub(r",(\s*[}\]])", r"\1", raw)
    return sanitized


_AGENT_DESCRIPTIONS = {
    "main": "主 Agent 提示词配置 - 统一管理 sections 和引导语",
    "subagent": "子代理提示词配置 - 管理 Sub Agent system prompt sections",
    "compressor": "压缩 Agent 提示词配置 - 管理 Compressor Agent system prompt sections",
}


class PromptManager:
    """
    系统提示词管理器（统一配置源）

    配置源：config/prompts_config.json (agents.main/subagent/compressor)
    缓存：data/system_prompt.json (main agent 组装后的完整 prompt)
    历史：data/prompt_history.json (main agent 变更记录)
    """

    def __init__(
        self,
        config_file: Path = PROMPTS_CONFIG_FILE,
        cache_file: Path = SYSTEM_PROMPT_FILE,
        history_file: Path = PROMPT_HISTORY_FILE,
        config_store=None,
    ):
        self.config_file = config_file
        self.cache_file = cache_file
        self.history_file = history_file
        self._config_store = config_store
        self._ensure_initialized()

    def _ensure_initialized(self):
        """确保配置文件和缓存文件存在"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        # 配置文件必须存在，不存在则报错
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"提示词配置文件不存在: {self.config_file}\n"
                f"请确保 config/prompts_config.json 存在于项目目录中。"
            )

        # 读取并验证配置格式
        config = self._read_config()
        self._validate_config_format(config)

        # 初始化缓存文件
        if not self.cache_file.exists():
            self._rebuild_cache("main")

        # 初始化历史文件
        if not self.history_file.exists():
            self._write_history_file([])

    def _validate_config_format(self, config: dict):
        """验证配置文件格式是否正确。不再强制要求特定 agent 类型存在，由启动时交叉校验。"""
        if "agents" not in config:
            raise ValueError(
                "提示词配置文件格式错误: 缺少顶层 'agents' 字段。"
                "请确认 config/prompts_config.json 格式正确。"
            )
        agents = config["agents"]
        if not agents:
            logger.warning("提示词配置中 agents 为空，system prompt 将不包含任何内容。")
        for agent_type, agent_config in agents.items():
            sections = agent_config.get("sections", [])
            if not sections:
                logger.warning(
                    f"提示词配置中 '{agent_type}' 的 sections 为空，"
                    f"该 agent 类型的 system prompt 将不包含任何内容。"
                )

    def _read_config(self) -> dict:
        """读取配置文件，失败时尝试清洗 trailing comma 后重试，仍失败则报错"""
        if self._config_store is not None:
            return self._config_store.load()
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                raw = f.read()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # 尝试清洗常见格式问题（如 trailing comma）后重试
            sanitized = _sanitize_json(raw)
            try:
                data = json.loads(sanitized)
                logger.warning(
                    "提示词配置文件存在格式问题（如尾部逗号），已自动清洗修复。"
                    "建议通过 API 重新保存以规范化格式。"
                )
                # 自动回写清洗后的合法 JSON，防止问题累积
                try:
                    self._write_raw_config(sanitized)
                except Exception:
                    logger.debug("自动回写清洗后配置失败（不影响读取）", exc_info=True)
                return data
            except json.JSONDecodeError:
                raise ValueError(
                    f"提示词配置文件 JSON 解析失败: {self.config_file}\n"
                    f"原始错误: {e}\n"
                    f"请手动检查并修复 JSON 格式。"
                ) from e
        except IOError as e:
            raise IOError(f"无法读取提示词配置文件: {self.config_file}") from e

    def _write_raw_config(self, raw_json_string: str):
        """将已清洗的 JSON 字符串原子写入配置文件（不修改内容，仅重新写入）"""
        tmp_path = str(self.config_file) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(raw_json_string)
        os.replace(tmp_path, self.config_file)
        logger.info("配置已自动修复并保存")

    def _write_config(self, config: dict):
        """写入配置文件（原子写入），失败时抛出异常让调用方感知"""
        try:
            # 创建副本避免修改调用方持有的 dict 引用
            config = {**config, "last_updated": datetime.now(timezone.utc).isoformat()}
            if self._config_store is not None:
                self._config_store.save(config)
                logger.info("Prompt 分层配置已保存")
                return
            tmp_path = str(self.config_file) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_file)
            logger.info("配置已保存")
        except IOError as e:
            logger.error(f"写入配置失败: {e}")
            raise

    def _read_cache(self) -> dict:
        """读取缓存文件"""
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"读取缓存失败: {e}")
            return {"prompt": "", "version": 1, "last_modified": ""}

    def _write_cache(self, prompt: str, version: int = 1):
        """写入缓存文件（原子写入），失败时抛出异常让调用方感知"""
        try:
            cache_data = {
                "prompt": prompt,
                "version": version,
                "last_modified": datetime.now(timezone.utc).isoformat(),
            }
            tmp_path = str(self.cache_file) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.cache_file)
            logger.info("缓存已更新")
        except IOError as e:
            logger.error(f"写入缓存失败: {e}")
            raise

    def _read_history_file(self) -> list[dict]:
        """读取历史文件"""
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def _write_history_file(self, data: list[dict]):
        """写入历史文件（原子写入），失败时抛出异常让调用方感知"""
        try:
            tmp_path = str(self.history_file) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.history_file)
        except IOError as e:
            logger.error(f"写入历史失败: {e}")
            raise

    def _get_agent_config(self, config: dict, agent_type: str) -> dict:
        """获取指定 agent_type 的配置，不存在则报错"""
        agents = config.get("agents", {})
        if agent_type not in agents:
            raise ValueError(
                f"提示词配置中不存在 agent 类型: '{agent_type}'。"
                f"有效的类型: {', '.join(sorted(agents.keys()))}"
            )
        return agents[agent_type]

    # ============ Sections 管理 ============

    def get_sections(self, agent_type: str = "main") -> list[dict]:
        """获取所有 sections（按 order 排序）"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        sections = agent_config.get("sections", [])
        return sorted(sections, key=lambda s: s.get("order", 0))

    def get_section(self, name: str, agent_type: str = "main") -> dict | None:
        """获取单个 section"""
        sections = self.get_sections(agent_type)
        for section in sections:
            if section.get("name") == name:
                return section
        return None

    # 允许通过 update_section 更新的字段白名单，防止外部覆写 name 等结构字段
    _SECTION_MUTABLE_KEYS = frozenset({
        "content", "enabled", "order", "cache_break", "cache_break_reason",
        "workflow_only", "chat_only",
    })

    def update_section(self, name: str, updates: dict, reason: str = "", agent_type: str = "main") -> bool:
        """更新单个 section"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        sections = agent_config.get("sections", [])

        # 过滤掉不在白名单中的字段（如 "name"），防止破坏 section 结构
        safe_updates = {k: v for k, v in updates.items() if k in self._SECTION_MUTABLE_KEYS}
        skipped = set(updates) - self._SECTION_MUTABLE_KEYS
        if skipped:
            logger.warning(f"update_section 跳过非白名单字段: {skipped}")

        found = False
        old_content = None
        for section in sections:
            if section.get("name") == name:
                old_content = section.get("content", "")
                section.update(safe_updates)
                found = True
                break

        if not found:
            # section 不存在时自动创建
            new_section = {
                "name": name,
                "enabled": True,
                "order": len(sections),
                "cache_break": False,
                "cache_break_reason": "",
            }
            new_section.update(safe_updates)
            sections.append(new_section)
            old_content = None  # 标记为创建而非更新
            logger.info(f"Section '{name}' 不存在于 {agent_type}，已自动创建")

        agent_config["sections"] = sections
        config["agents"][agent_type] = agent_config
        self._write_config(config)

        # 记录历史（仅 main 有独立历史）
        if agent_type == "main":
            if old_content is not None:
                # 更新已有 section
                if "content" in updates:
                    self._add_history_entry(
                        action="update_section",
                        section_name=name,
                        old_content=old_content,
                        new_content=updates["content"],
                        reason=reason if reason else f"更新 section {name}",
                    )
            else:
                # 新创建 section
                self._add_history_entry(
                    action="create_section",
                    section_name=name,
                    old_content="",
                    new_content=updates.get("content", ""),
                    reason=reason if reason else f"自动创建 section {name}",
                )

        if agent_type == "main":
            self._rebuild_cache("main")
        return True

    def update_sections(self, sections: list[dict], reason: str = "", agent_type: str = "main") -> bool:
        """批量更新 sections"""
        # 记录历史（仅 main）
        if agent_type == "main":
            old_sections = self.get_sections("main")
            self._add_history_entry(
                action="update_sections",
                section_name="all",
                old_content=f"{len(old_sections)} sections",
                new_content=f"{len(sections)} sections",
                reason=reason if reason else "批量更新 sections",
            )

        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        agent_config["sections"] = sections
        config["agents"][agent_type] = agent_config
        self._write_config(config)

        if agent_type == "main":
            self._rebuild_cache("main")
        return True

    def add_section(self, section: dict, agent_type: str = "main") -> bool:
        """添加新 section"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        sections = agent_config.get("sections", [])

        # 检查是否已存在
        if any(s.get("name") == section.get("name") for s in sections):
            logger.error(f"Section {section.get('name')} 已存在于 {agent_type}")
            return False

        # 设置默认值
        section.setdefault("enabled", True)
        section.setdefault("order", len(sections))
        section.setdefault("cache_break", False)
        section.setdefault("cache_break_reason", "")

        sections.append(section)
        agent_config["sections"] = sections
        config["agents"][agent_type] = agent_config
        self._write_config(config)

        if agent_type == "main":
            self._rebuild_cache("main")
        return True

    def delete_section(self, name: str, agent_type: str = "main") -> bool:
        """删除 section"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        sections = agent_config.get("sections", [])
        original_len = len(sections)

        # 找到要删除的 section 内容（用于历史记录）
        deleted_content = ""
        for s in sections:
            if s.get("name") == name:
                deleted_content = s.get("content", "")
                break

        sections = [s for s in sections if s.get("name") != name]

        if len(sections) == original_len:
            logger.warning(f"Section {name} 不存在于 {agent_type}")
            return False

        # 记录历史（仅 main）
        if deleted_content and agent_type == "main":
            self._add_history_entry(
                action="delete_section",
                section_name=name,
                old_content=deleted_content,
                new_content="",
                reason=f"删除 section {name}",
            )

        agent_config["sections"] = sections
        config["agents"][agent_type] = agent_config
        self._write_config(config)

        if agent_type == "main":
            self._rebuild_cache("main")
        return True

    def rename_section(self, old_name: str, new_name: str, agent_type: str = "main") -> bool:
        """重命名 section"""
        if not new_name.strip():
            logger.error("新名称不能为空")
            return False

        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        sections = agent_config.get("sections", [])

        # 检查新名称是否已存在
        if any(s.get("name") == new_name for s in sections):
            logger.error(f"Section {new_name} 已存在于 {agent_type}")
            return False

        # 查找并重命名
        found = False
        for section in sections:
            if section.get("name") == old_name:
                section["name"] = new_name
                found = True
                break

        if not found:
            logger.warning(f"Section {old_name} 不存在于 {agent_type}")
            return False

        # 记录历史（仅 main）
        if agent_type == "main":
            self._add_history_entry(
                action="rename_section",
                section_name=old_name,
                old_content=old_name,
                new_content=new_name,
                reason=f"重命名 section {old_name} → {new_name}",
            )

        agent_config["sections"] = sections
        config["agents"][agent_type] = agent_config
        self._write_config(config)

        if agent_type == "main":
            self._rebuild_cache("main")
        return True

    # ============ Preambles 管理 ============

    def get_preambles(self, agent_type: str = "main") -> dict:
        """获取引导语配置"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        return agent_config.get("preambles", {})

    def update_preambles(self, preambles: dict, agent_type: str = "main") -> bool:
        """更新引导语配置"""
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        agent_config["preambles"] = preambles
        config["agents"][agent_type] = agent_config
        self._write_config(config)
        return True

    # ============ 缓存管理 ============

    def _rebuild_cache(self, agent_type: str = "main"):
        """重建缓存（从 sections 组装）并增加版本号"""
        if agent_type != "main":
            return  # 仅 main 有缓存

        sections = self.get_sections("main")
        enabled_sections = [s for s in sections if s.get("enabled", True)]

        # 组装完整 prompt
        parts = [s.get("content", "") for s in enabled_sections]
        prompt = "\n\n".join(parts)

        # 读取旧版本号并递增
        cache = self._read_cache()
        old_version = cache.get("version", 1)
        new_version = old_version + 1

        # 写入缓存（版本号+1）
        self._write_cache(prompt, new_version)

        logger.info(f"缓存已重建，{len(enabled_sections)} 个 sections，版本 v{old_version} → v{new_version}")

    def _add_history_entry(self, action: str, section_name: str, old_content: str, new_content: str, reason: str):
        """添加历史记录"""
        try:
            history = self._read_history_file()

            # 获取当前版本号
            cache = self._read_cache()
            version = cache.get("version", 1)

            history.append({
                "version": version + 1,  # 即将更新到的版本
                "action": action,
                "section_name": section_name,
                "old_content": old_content[:200] + "..." if len(old_content) > 200 else old_content,
                "new_content": new_content[:200] + "..." if len(new_content) > 200 else new_content,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            self._write_history_file(history)
            logger.info(f"历史记录已添加: {action} - {section_name}")
        except Exception:
            logger.error("添加历史记录失败", exc_info=True)

    def get_prompt(self, agent_type: str = "main") -> dict:
        """获取缓存的 prompt（向后兼容，仅 main 有缓存）"""
        if agent_type == "main":
            return self._read_cache()
        # 其他 agent_type 现场组装
        sections = self.get_sections(agent_type)
        enabled = [s for s in sections if s.get("enabled", True)]
        prompt = "\n\n".join(s.get("content", "") for s in enabled)
        return {"prompt": prompt, "version": 1, "last_modified": ""}

    def get_prompt_text(self, agent_type: str = "main") -> str:
        """获取缓存的 prompt 文本"""
        if agent_type == "main":
            cache = self._read_cache()
            return cache.get("prompt", "")
        sections = self.get_sections(agent_type)
        enabled = [s for s in sections if s.get("enabled", True)]
        return "\n\n".join(s.get("content", "") for s in enabled)

    def get_version(self, agent_type: str = "main") -> int:
        """获取缓存版本号"""
        if agent_type == "main":
            cache = self._read_cache()
            return cache.get("version", 1)
        return 1

    def is_customized(self, agent_type: str = "main") -> bool:
        """判断是否自定义（始终返回 False，因为现在使用 sections）"""
        return False

    def get_history(self) -> list[dict]:
        """获取历史记录"""
        return self._read_history_file()

    # ============ 配置管理 ============

    def get_config(self, agent_type: str | None = None) -> dict:
        """获取完整配置"""
        config = self._read_config()
        if agent_type:
            return self._get_agent_config(config, agent_type)
        return config

    def list_agent_types(self) -> list[str]:
        """列出 prompts_config.json 中所有可用的提示词 agent 类型。

        从配置文件动态读取，未来在 config 中新增类型后自动生效。
        """
        config = self._read_config()
        agents = config.get("agents", {})
        return sorted(agents.keys())

    def delete_agent_type(self, agent_type: str) -> bool:
        """删除指定 agent 类型的提示词模板（从 prompts_config.json 中移除）。"""
        if agent_type in ("main", "subagent", "compressor"):
            raise ValueError(f"不能删除内置模板: {agent_type}")
        config = self._read_config()
        agents = config.get("agents", {})
        if agent_type not in agents:
            logger.warning(f"Agent 类型 '{agent_type}' 不存在")
            return False
        del agents[agent_type]
        config["agents"] = agents
        self._write_config(config)
        logger.info(f"已删除 agent 类型: {agent_type}")
        return True

    def reload(self):
        """验证并重新确认配置文件可解析（本类不缓存配置，每次读取磁盘）。

        主要用途：外部调用方在修改配置文件后，调用此方法验证格式正确性。
        """
        config = self._read_config()
        self._validate_config_format(config)
        logger.info("提示词配置文件格式验证通过")

    # ============ Template Variables 管理 ============

    def get_template_variables(self, agent_type: str) -> list[dict]:
        """获取指定 agent 类型的自定义变量块声明。

        Args:
            agent_type: 提示词模板名（包括 Extension 提供的模板）

        Returns:
            变量块声明列表，每个元素包含 key, name, description, default, required
        """
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        raw = agent_config.get("template_variables", [])
        # 兼容两种配置格式：
        # 1. array 格式: [{"key": "xxx", "name": "xxx", "description": "...", "default": "", "required": false}, ...]
        # 2. dict 格式: {"key1": {"description": "...", "default": ""}, "key2": {...}}
        if isinstance(raw, dict):
            result = []
            for key, val in raw.items():
                if isinstance(val, dict):
                    result.append({
                        "key": key,
                        "name": val.get("name", key),
                        "description": val.get("description", ""),
                        "default": val.get("default", ""),
                        "required": val.get("required", False),
                    })
                else:
                    result.append({"key": key, "name": key, "description": str(val), "default": "", "required": False})
            return result
        return raw if isinstance(raw, list) else []

    def update_template_variables(self, template_variables: list[dict], agent_type: str) -> bool:
        """更新指定 agent 类型的自定义变量块声明。

        Args:
            template_variables: 变量块声明列表
            agent_type: 提示词模板名

        Returns:
            是否更新成功
        """
        config = self._read_config()
        agent_config = self._get_agent_config(config, agent_type)
        agent_config["template_variables"] = template_variables
        config["agents"][agent_type] = agent_config
        self._write_config(config)
        logger.info(f"已更新 {agent_type} 的 template_variables: {len(template_variables)} 个变量块")
        return True
