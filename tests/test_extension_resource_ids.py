from __future__ import annotations

import pytest

from src.extension_host.resource_ids import (
    ResourceIdConflictError,
    ResourceIdResolver,
    build_resource_id_plan,
    effective_resource_id,
)


def test_effective_resource_id_preserves_existing_prefix() -> None:
    assert effective_resource_id("novel", "writer") == "novel-writer"
    assert effective_resource_id("novel", "novel-writer") == "novel-writer"
    assert effective_resource_id("novel", "novel") == "novel"
    assert effective_resource_id("", "legacy-writer") == "legacy-writer"


@pytest.mark.parametrize(
    ("declared_kind", "stable_kind"),
    [
        ("agent", "agent"),
        ("agents", "agent"),
        ("prompt", "prompt"),
        ("prompts", "prompt"),
        ("workflow", "workflow"),
        ("workflows", "workflow"),
        ("skill_bundles", "skill"),
        ("skills", "skill"),
        ("rule_bundles", "rule"),
        ("rules", "rule"),
        ("script_libraries", "script_library"),
        ("preset_phrases", "preset_phrase"),
        ("skill_group", "skill_group"),
        ("rule_group", "rule_group"),
    ],
)
def test_plan_normalizes_resource_kind_aliases(
    declared_kind: str,
    stable_kind: str,
) -> None:
    plan = build_resource_id_plan(
        "demo-plugin",
        "demo",
        {declared_kind: ["item"]},
    )

    assert plan.mappings[0].resource_type == stable_kind
    assert plan.resolve(stable_kind, "item") == "demo-item"


def test_plan_keeps_explicit_mapping_and_rejects_effective_collision() -> None:
    plan = build_resource_id_plan(
        "novel-workflows",
        "novel",
        {"agents": ["writer"], "workflows": ["wf-nvl-build"]},
    )

    assert plan.resolve("agents", "writer") == "novel-writer"
    assert plan.resolve("agent", "writer") == "novel-writer"
    assert plan.resolve("workflows", "wf-nvl-build") == "novel-wf-nvl-build"
    assert {mapping.resource_type for mapping in plan.mappings} == {
        "agent",
        "workflow",
    }
    with pytest.raises(KeyError):
        plan.resolve("agents", "novel-writer")

    with pytest.raises(ResourceIdConflictError, match="both resolve"):
        build_resource_id_plan(
            "novel-workflows",
            "novel",
            {"agents": ["writer", "novel-writer"]},
        )


def test_resolver_rejects_cross_plugin_effective_collision_and_tracks_owner() -> None:
    resolver = ResourceIdResolver()
    resolver.register(
        build_resource_id_plan(
            "first-plugin",
            "shared",
            {"prompts": ["draft"]},
        )
    )

    mapping = resolver.mapping_for("prompts", "shared-draft")

    assert mapping is not None
    assert mapping.plugin_id == "first-plugin"
    assert mapping.local_id == "draft"
    assert resolver.resolve("first-plugin", "prompts", "draft") == "shared-draft"

    with pytest.raises(ResourceIdConflictError, match="first-plugin.*second-plugin"):
        resolver.register(
            build_resource_id_plan(
                "second-plugin",
                "shared",
                {"prompts": ["draft"]},
            )
        )
