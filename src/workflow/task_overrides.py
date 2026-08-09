"""Validate and apply Task-scoped Workflow definition overrides."""

from __future__ import annotations

from typing import Any


TASK_OVERRIDE_ERROR_CODES = frozenset({
    "workflow_node_model_overrides_invalid",
    "workflow_node_model_override_unknown_node",
    "workflow_node_model_override_not_agent",
    "workflow_model_reference_invalid",
    "workflow_model_provider_not_found",
    "workflow_model_not_found",
    "workflow_model_provider_invalid",
})


class TaskOverrideValidationError(ValueError):
    """A stable validation failure suitable for extension and HTTP callers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.code,
            "message": str(self),
        }


def _validate_model_reference(
    raw_model: object,
    *,
    node_id: str,
    model_manager: Any,
) -> str:
    if not isinstance(raw_model, str):
        raise TaskOverrideValidationError(
            "workflow_model_reference_invalid",
            f"Agent 节点 {node_id} 的模型必须使用 provider_id:model_name",
        )

    model_reference = raw_model.strip()
    provider_id, separator, model_name = model_reference.partition(":")
    if (
        not separator
        or not provider_id
        or not model_name
        or "{{" in model_reference
        or "}}" in model_reference
    ):
        raise TaskOverrideValidationError(
            "workflow_model_reference_invalid",
            f"Agent 节点 {node_id} 的模型必须使用 provider_id:model_name",
        )

    provider = model_manager.get_provider(provider_id)
    if provider is None:
        raise TaskOverrideValidationError(
            "workflow_model_provider_not_found",
            f"Core 中不存在 Provider: {provider_id}",
        )

    configured_models = provider.get("models", [])
    if not isinstance(configured_models, list) or model_name not in configured_models:
        raise TaskOverrideValidationError(
            "workflow_model_not_found",
            f"Provider {provider_id} 未配置模型 {model_name}",
        )

    try:
        model_manager.get_model_provider_type(provider_id, model_name)
    except ValueError as exc:
        raise TaskOverrideValidationError(
            "workflow_model_provider_invalid",
            f"Provider {provider_id} 的模型适配配置无效: {model_name}",
        ) from exc
    return model_reference


def apply_node_model_overrides(
    definition: dict[str, Any],
    node_model_overrides: dict[str, str] | None,
    *,
    model_manager: Any | None = None,
) -> None:
    """Apply validated Agent model choices to a private Task snapshot."""
    if node_model_overrides is None:
        return
    if not isinstance(node_model_overrides, dict):
        raise TaskOverrideValidationError(
            "workflow_node_model_overrides_invalid",
            "node_model_overrides 必须是 object",
        )
    if not all(isinstance(node_id, str) for node_id in node_model_overrides):
        raise TaskOverrideValidationError(
            "workflow_node_model_overrides_invalid",
            "node_model_overrides 的节点 ID 必须是字符串",
        )
    if not node_model_overrides:
        return

    nodes = {
        str(node.get("id")): node
        for node in definition.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    unknown = sorted(set(node_model_overrides) - set(nodes))
    if unknown:
        raise TaskOverrideValidationError(
            "workflow_node_model_override_unknown_node",
            "模型覆盖包含不存在的节点: " + ", ".join(unknown),
        )

    if model_manager is None:
        from src.core.model_manager import get_model_manager

        model_manager = get_model_manager()

    for node_id, raw_model in node_model_overrides.items():
        node = nodes[node_id]
        if node.get("node_type", "agent") != "agent":
            raise TaskOverrideValidationError(
                "workflow_node_model_override_not_agent",
                f"模型覆盖只能用于 Agent 节点: {node_id}",
            )
        node["model_override"] = _validate_model_reference(
            raw_model,
            node_id=node_id,
            model_manager=model_manager,
        )
