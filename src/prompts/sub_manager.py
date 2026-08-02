"""Sub Agent / Compressor 提示词配置管理器（委托给统一 PromptManager）。"""
from __future__ import annotations

from typing import Any

from src.prompts.manager import PromptManager


class _TypedPromptManager:
    """通用提示词管理器基类，通过 agent_type 参数化委托调用。

    SubAgentPromptManager 和 CompressorPromptManager 的所有方法结构完全相同，
    仅 agent_type 不同。本基类消除重复代码，子类只需声明 _agent_type。
    """

    _agent_type: str = ""  # 子类必须覆盖

    def __init__(self, unified_manager: PromptManager | None = None):
        self._manager = unified_manager or PromptManager()

    def get_sections(self) -> list[dict[str, Any]]:
        return self._manager.get_sections(agent_type=self._agent_type)

    def get_section(self, name: str) -> dict[str, Any] | None:
        return self._manager.get_section(name, agent_type=self._agent_type)

    def update_section(self, name: str, updates: dict, reason: str = "") -> bool:
        return self._manager.update_section(name, updates, reason, agent_type=self._agent_type)

    def update_sections(self, sections: list[dict], reason: str = "") -> bool:
        return self._manager.update_sections(sections, reason, agent_type=self._agent_type)

    def add_section(self, section: dict) -> bool:
        return self._manager.add_section(section, agent_type=self._agent_type)

    def delete_section(self, name: str) -> bool:
        return self._manager.delete_section(name, agent_type=self._agent_type)

    def rename_section(self, old_name: str, new_name: str) -> bool:
        return self._manager.rename_section(old_name, new_name, agent_type=self._agent_type)

    def get_config(self) -> dict[str, Any]:
        return self._manager.get_config(agent_type=self._agent_type)

    def get_prompt(self) -> str:
        return self._manager.get_prompt(agent_type=self._agent_type)

    def get_prompt_text(self) -> str:
        return self._manager.get_prompt_text(agent_type=self._agent_type)

    def get_version(self) -> int:
        return self._manager.get_version(agent_type=self._agent_type)

    def is_customized(self) -> bool:
        return self._manager.is_customized(agent_type=self._agent_type)

    def reload(self) -> None:
        return self._manager.reload()


class SubAgentPromptManager(_TypedPromptManager):
    """管理 subagent 提示词（委托给统一 PromptManager）。"""

    _agent_type = "subagent"


class CompressorPromptManager(_TypedPromptManager):
    """管理 compressor 提示词（委托给统一 PromptManager）。"""

    _agent_type = "compressor"
