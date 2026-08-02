"""Explicit resource-ID plans for namespaced Plugin resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from src.plugin_system import validate_plugin_id, validate_resource_prefix


_RESOURCE_KIND_ALIASES = {
    "agent": "agent",
    "agents": "agent",
    "prompt": "prompt",
    "prompts": "prompt",
    "workflow": "workflow",
    "workflows": "workflow",
    "skill": "skill",
    "skills": "skill",
    "skill_bundles": "skill",
    "rule": "rule",
    "rules": "rule",
    "rule_bundles": "rule",
    "script_library": "script_library",
    "script_libraries": "script_library",
    "preset_phrase": "preset_phrase",
    "preset_phrases": "preset_phrase",
    "skill_group": "skill_group",
    "rule_group": "rule_group",
}


class ResourceIdError(ValueError):
    """Base error for invalid resource identity declarations."""


class ResourceIdConflictError(ResourceIdError):
    """Raised when two declarations produce the same effective resource ID."""


@dataclass(frozen=True)
class ResourceIdMapping:
    """One explicit local-to-effective resource ID mapping."""

    plugin_id: str
    resource_type: str
    local_id: str
    effective_id: str


@dataclass(frozen=True)
class ResourceIdPlan:
    """Immutable mappings for one Plugin revision and installed prefix."""

    plugin_id: str
    resource_prefix: str
    mappings: tuple[ResourceIdMapping, ...]

    def resolve(self, resource_type: str, local_id: str) -> str:
        key = (_normalize_resource_type(resource_type), _normalize_local_id(local_id))
        for mapping in self.mappings:
            if (mapping.resource_type, mapping.local_id) == key:
                return mapping.effective_id
        raise KeyError(
            f"resource mapping not found: {self.plugin_id}/{key[0]}/{key[1]}"
        )


def effective_resource_id(resource_prefix: str, local_id: str) -> str:
    """Apply a prefix once while preserving already-prefixed legacy IDs."""
    prefix = validate_resource_prefix(resource_prefix)
    normalized_local_id = _normalize_local_id(local_id)
    if (
        not prefix
        or normalized_local_id == prefix
        or normalized_local_id.startswith(f"{prefix}-")
    ):
        return normalized_local_id
    return f"{prefix}-{normalized_local_id}"


def build_resource_id_plan(
    plugin_id: str,
    resource_prefix: str,
    resource_ids: Mapping[str, Iterable[str]],
) -> ResourceIdPlan:
    """Build and validate explicit mappings without relying on reverse parsing."""
    try:
        owner = validate_plugin_id(plugin_id)
        prefix = validate_resource_prefix(resource_prefix)
    except ValueError as exc:
        raise ResourceIdError(str(exc)) from exc

    mappings: list[ResourceIdMapping] = []
    local_keys: set[tuple[str, str]] = set()
    effective_keys: dict[tuple[str, str], str] = {}
    for raw_type, raw_ids in resource_ids.items():
        resource_type = _normalize_resource_type(raw_type)
        if isinstance(raw_ids, str):
            raise ResourceIdError(
                f"resource IDs for {resource_type} must be an iterable of IDs"
            )
        for raw_id in raw_ids:
            local_id = _normalize_local_id(raw_id)
            local_key = (resource_type, local_id)
            if local_key in local_keys:
                raise ResourceIdConflictError(
                    f"duplicate local resource ID: {owner}/{resource_type}/{local_id}"
                )
            effective_id = effective_resource_id(prefix, local_id)
            effective_key = (resource_type, effective_id)
            previous = effective_keys.get(effective_key)
            if previous is not None:
                raise ResourceIdConflictError(
                    f"resource IDs {previous!r} and {local_id!r} both resolve "
                    f"to {effective_id!r} in {resource_type}"
                )
            local_keys.add(local_key)
            effective_keys[effective_key] = local_id
            mappings.append(
                ResourceIdMapping(
                    plugin_id=owner,
                    resource_type=resource_type,
                    local_id=local_id,
                    effective_id=effective_id,
                )
            )
    return ResourceIdPlan(
        plugin_id=owner,
        resource_prefix=prefix,
        mappings=tuple(mappings),
    )


class ResourceIdResolver:
    """Resolve exact Plugin-local references from registered explicit plans."""

    def __init__(self) -> None:
        self._plans: dict[str, ResourceIdPlan] = {}
        self._effective: dict[tuple[str, str], ResourceIdMapping] = {}

    def register(self, plan: ResourceIdPlan) -> None:
        if plan.plugin_id in self._plans:
            raise ResourceIdConflictError(
                f"resource ID plan already registered: {plan.plugin_id}"
            )
        for mapping in plan.mappings:
            key = (mapping.resource_type, mapping.effective_id)
            existing = self._effective.get(key)
            if existing is not None:
                section = {
                    "agent": "agents",
                    "prompt": "agents",
                    "skill": "skills",
                    "rule": "rules",
                    "preset_phrase": "phrases",
                }.get(mapping.resource_type, mapping.resource_type)
                raise ResourceIdConflictError(
                    f"扩展资源冲突: {section}.{mapping.effective_id}；"
                    f"effective resource ID {mapping.effective_id!r} in "
                    f"{mapping.resource_type} conflicts between "
                    f"{existing.plugin_id!r} and {mapping.plugin_id!r}"
                )
        self._plans[plan.plugin_id] = plan
        for mapping in plan.mappings:
            self._effective[(mapping.resource_type, mapping.effective_id)] = mapping

    def unregister(self, plugin_id: str) -> None:
        """Remove one complete plan after a failed cold-start transaction."""
        owner = validate_plugin_id(plugin_id)
        plan = self._plans.pop(owner, None)
        if plan is None:
            return
        for mapping in plan.mappings:
            key = (mapping.resource_type, mapping.effective_id)
            if self._effective.get(key) == mapping:
                self._effective.pop(key, None)

    def resolve(
        self,
        plugin_id: str,
        resource_type: str,
        local_id: str,
    ) -> str:
        owner = validate_plugin_id(plugin_id)
        try:
            plan = self._plans[owner]
        except KeyError as exc:
            raise KeyError(f"resource ID plan not found: {owner}") from exc
        return plan.resolve(resource_type, local_id)

    def mapping_for(
        self,
        resource_type: str,
        effective_id: str,
    ) -> ResourceIdMapping | None:
        """Return provenance from the explicit index, without parsing the ID."""
        key = (
            _normalize_resource_type(resource_type),
            _normalize_local_id(effective_id),
        )
        return self._effective.get(key)


def _normalize_resource_type(resource_type: str) -> str:
    normalized = str(resource_type).strip()
    if not normalized:
        raise ResourceIdError("resource type cannot be empty")
    return _RESOURCE_KIND_ALIASES.get(normalized, normalized)


def _normalize_local_id(local_id: str) -> str:
    normalized = str(local_id).strip()
    if not normalized:
        raise ResourceIdError("local resource ID cannot be empty")
    return normalized
