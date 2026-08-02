"""Agent tools backed by the process-wide layered PromptManager."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.prompts.manager import PromptManager


class GetSystemPromptArgs(BaseModel):
    agent_type: str = Field(default="main", description="Agent 提示词类型")


class UpdateSystemPromptArgs(BaseModel):
    section_name: str = Field(description="要更新的 section 名称")
    new_content: str = Field(default="", description="新的 section 内容")
    reason: str = Field(default="", description="本次修改原因")
    agent_type: str = Field(default="main", description="Agent 提示词类型")
    workflow_only: str = Field(
        default="",
        description="是否仅用于工作流，可填写 true/false；留空表示不修改",
    )


class ListAgentTypesArgs(BaseModel):
    pass


def create_prompt_tools(prompt_manager: "PromptManager") -> list[StructuredTool]:
    """Create prompt tools over the same layered store used by agent runtime."""

    def get_system_prompt(agent_type: str = "main") -> str:
        sections = prompt_manager.get_sections(agent_type)
        preambles = prompt_manager.get_preambles(agent_type)
        return json.dumps(
            {
                "message": f"当前 {agent_type} 提示词配置",
                "agent_type": agent_type,
                "data": {
                    "sections": sections,
                    "preambles": preambles,
                    "sections_count": len(sections),
                },
            },
            ensure_ascii=False,
        )

    def update_system_prompt(
        section_name: str,
        new_content: str = "",
        reason: str = "",
        agent_type: str = "main",
        workflow_only: str = "",
    ) -> str:
        updates = {}
        if new_content.strip():
            updates["content"] = new_content
        if workflow_only:
            updates["workflow_only"] = workflow_only.strip().lower() in {
                "true",
                "1",
                "yes",
                "on",
            }
        if not updates:
            return json.dumps(
                {"message": "没有提供任何要更新的内容", "success": False},
                ensure_ascii=False,
            )

        success = prompt_manager.update_section(
            section_name,
            updates,
            reason=reason,
            agent_type=agent_type,
        )
        return json.dumps(
            {
                "message": (
                    f"Section {section_name}（{agent_type}）已更新"
                    if success
                    else f"Section {section_name} 在 {agent_type} 中不存在"
                ),
                "agent_type": agent_type,
                "success": success,
            },
            ensure_ascii=False,
        )

    def list_agent_types() -> str:
        agent_types = prompt_manager.list_agent_types()
        return json.dumps(
            {
                "message": "可用的提示词 Agent 类型",
                "agent_types": agent_types,
                "count": len(agent_types),
            },
            ensure_ascii=False,
        )

    return [
        StructuredTool(
            name="get_system_prompt",
            description="查看指定 Agent 类型的系统提示词 sections 配置。",
            args_schema=GetSystemPromptArgs,
            func=get_system_prompt,
        ),
        StructuredTool(
            name="update_system_prompt",
            description="修改指定 Agent 类型系统提示词的一个 section。",
            args_schema=UpdateSystemPromptArgs,
            func=update_system_prompt,
        ),
        StructuredTool(
            name="list_agent_types",
            description="列出 PromptManager 中所有可用的 Agent 提示词类型。",
            args_schema=ListAgentTypesArgs,
            func=list_agent_types,
        ),
    ]
