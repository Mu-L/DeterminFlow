"""Request contracts shared by the workflow definition and run routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    http_execution_policy: Literal["public"] = "public"
    name: str = ""
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    variables: list[dict] = Field(default_factory=list)
    gateways: list[dict] = Field(default_factory=list)


class WorkflowUpdateRequest(WorkflowCreateRequest):
    pass


class PreStartRequest(BaseModel):
    workspace_override: str | None = Field(default=None)
    main_takeover: bool = Field(default=False)


class WorkflowRunRequest(BaseModel):
    from_node_id: str | None = Field(default=None)


class TaskCreateRequest(BaseModel):
    from_node_id: str | None = Field(default=None)
    parameter_values: dict[str, str] | None = Field(default=None)
    disabled_node_ids: list[str] | None = Field(default=None)
    scheme_id: str | None = Field(default=None)
    selected_node_ids: list[str] | None = Field(default=None)
    workspace_override: str | None = Field(default=None)
