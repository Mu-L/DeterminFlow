"""Immutable execution identities stored in Workflow Task snapshots."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from .runtime import effective_agent_definition_sha256


RUNTIME_GUARD_KEY = "_runtime_guard"
RUNTIME_GUARD_SCHEMA = "workflow_node_runtime_guard.v1"
_PLACEHOLDER_RE = re.compile(r"\{\{([\w.-]+)\}\}")
_SCRIPT_NAME_RE = re.compile(r"^[\w]+$")
_SCRIPT_EXTENSIONS = {"shell": "sh", "python": "py"}


class WorkflowRuntimeGuardError(RuntimeError):
    """Raised when a Task cannot establish an immutable execution identity."""


def file_sha256(path: Path) -> str:
    """Hash the exact bytes that the node will execute."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze_values(
    definition: dict,
    parameter_values: dict[str, Any] | None,
) -> dict[str, str]:
    values = {
        str(variable.get("key")): str(variable.get("default") or "")
        for variable in definition.get("variables", [])
        if variable.get("key")
    }
    values.update({
        str(key): str(value or "")
        for key, value in (parameter_values or {}).items()
    })
    return values


def _resolve_static_value(value: Any, values: dict[str, str], *, field: str) -> str:
    resolved = str(value or "")
    for _depth in range(10):
        changed = False

        def replace(match: re.Match) -> str:
            nonlocal changed
            key = match.group(1)
            if key not in values:
                return match.group(0)
            changed = True
            return values[key]

        next_value = _PLACEHOLDER_RE.sub(replace, resolved)
        resolved = next_value
        if not changed:
            break
    if _PLACEHOLDER_RE.search(resolved):
        raise WorkflowRuntimeGuardError(
            f"无法在 Task 创建时冻结动态 {field}: {value!r}"
        )
    return resolved


def _inline_script_path(workflow_dir: Path, node: dict) -> Path | None:
    params = node.get("node_params") or {}
    if node.get("node_type") != "script":
        return None
    if params.get("script_source", "inline") != "inline":
        return None
    script_name = str(params.get("script_name") or "").strip()
    script_type = str(params.get("script_type") or "shell")
    if not script_name or not _SCRIPT_NAME_RE.fullmatch(script_name):
        raise WorkflowRuntimeGuardError(
            f"无法冻结非法 inline script 名称: node={node.get('id')}, "
            f"script_name={script_name!r}"
        )
    extension = _SCRIPT_EXTENSIONS.get(script_type, "sh")
    script_path = workflow_dir / "script" / f"{script_name}.{extension}"
    if not script_path.resolve().is_relative_to(workflow_dir.resolve()):
        raise WorkflowRuntimeGuardError(
            f"inline script 路径逃逸: node={node.get('id')}"
        )
    return script_path


def inline_script_dependency_identities(
    script_dir: Path,
    raw_dependencies: Any,
) -> list[dict[str, str]]:
    """Resolve and hash explicitly declared inline-script dependencies."""
    if raw_dependencies is None:
        return []
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) and item.strip()
        for item in raw_dependencies
    ):
        raise WorkflowRuntimeGuardError(
            "script_dependencies 必须是非空相对路径字符串数组"
        )
    root = script_dir.resolve()
    identities: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in raw_dependencies:
        relative = Path(raw_path)
        if relative.is_absolute():
            raise WorkflowRuntimeGuardError(
                f"inline script dependency 必须使用相对路径: {raw_path}"
            )
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise WorkflowRuntimeGuardError(
                f"inline script dependency 路径逃逸: {raw_path}"
            )
        normalized = path.relative_to(root).as_posix()
        if normalized in seen:
            raise WorkflowRuntimeGuardError(
                f"inline script dependency 重复: {normalized}"
            )
        if not path.is_file():
            raise WorkflowRuntimeGuardError(
                f"inline script dependency 不存在: {normalized}"
            )
        identities.append({
            "path": normalized,
            "content_sha256": file_sha256(path),
        })
        seen.add(normalized)
    return sorted(identities, key=lambda item: item["path"])


def _inline_script_dependencies(
    workflow_dir: Path,
    node: dict,
) -> list[dict[str, str]]:
    if _inline_script_path(workflow_dir, node) is None:
        return []
    params = node.get("node_params") or {}
    return inline_script_dependency_identities(
        workflow_dir / "script",
        params.get("script_dependencies"),
    )


def _library_script_attestation(
    node: dict,
    *,
    values: dict[str, str],
) -> dict[str, Any] | None:
    params = node.get("node_params") or {}
    if node.get("node_type") != "script":
        return None
    if params.get("script_source", "inline") != "library":
        return None

    group = _resolve_static_value(
        params.get("script_group"),
        values,
        field="script_group",
    ).strip()
    script_name = _resolve_static_value(
        params.get("script_name"),
        values,
        field="script_name",
    ).strip()
    script_type = _resolve_static_value(
        params.get("script_type", "shell"),
        values,
        field="script_type",
    ).strip()
    if not group or not script_name:
        raise WorkflowRuntimeGuardError(
            f"Script Library 引用不完整: node={node.get('id')}"
        )

    from .script_library import ScriptLibraryError, get_script_library

    try:
        return get_script_library().attest(
            group,
            script_name,
            script_type,
        )
    except (ScriptLibraryError, ValueError) as exc:
        raise WorkflowRuntimeGuardError(
            f"无法冻结 Script Library 运行身份: node={node.get('id')}: {exc}"
        ) from exc


def _freeze_agent_identity(
    node: dict,
    *,
    values: dict[str, str],
    effective_agent_resolver: Callable[..., dict | None],
    guard: dict[str, Any],
) -> None:
    if node.get("node_type", "agent") != "agent":
        return
    agent_type = _resolve_static_value(
        node.get("agent_type", "default"),
        values,
        field="agent_type",
    )
    raw_model_override = node.get("model_override", "")
    model_override = (
        _resolve_static_value(
            raw_model_override,
            values,
            field="model_override",
        )
        if raw_model_override
        else ""
    )
    effective = effective_agent_resolver(
        agent_type,
        model_override=model_override or None,
    )
    if effective is None:
        raise WorkflowRuntimeGuardError(
            f"Workflow Agent 定义不存在，无法冻结 Task: {agent_type}"
        )
    guard.update({
        "agent_type": agent_type,
        "model_override": model_override,
        "effective_agent_definition_sha256": (
            effective_agent_definition_sha256(effective)
        ),
    })


def freeze_workflow_runtime_guards(
    definition: dict,
    *,
    workflow_dir: Path,
    parameter_values: dict[str, Any] | None,
    effective_agent_resolver: Callable[..., dict | None],
) -> dict:
    """Attach script and Agent identities to a serialized Task definition.

    The input is the Task's private definition snapshot and is mutated in place.
    Live workflow definitions remain free of runtime-only metadata.
    """
    values = _freeze_values(definition, parameter_values)
    for node in definition.get("nodes", []):
        params = node.setdefault("node_params", {})
        guard: dict[str, Any] = {"schema_version": RUNTIME_GUARD_SCHEMA}

        script_path = _inline_script_path(workflow_dir, node)
        if script_path is not None:
            if not script_path.is_file():
                raise WorkflowRuntimeGuardError(
                    f"inline script 不存在，无法创建 Task: {script_path}"
                )
            guard["inline_script_sha256"] = file_sha256(script_path)
            guard["inline_script_dependencies"] = (
                _inline_script_dependencies(workflow_dir, node)
            )
        library_attestation = _library_script_attestation(
            node,
            values=values,
        )
        if library_attestation is not None:
            guard["library_script_attestation"] = library_attestation

        _freeze_agent_identity(
            node,
            values=values,
            effective_agent_resolver=effective_agent_resolver,
            guard=guard,
        )

        if len(guard) > 1:
            params[RUNTIME_GUARD_KEY] = guard
        else:
            params.pop(RUNTIME_GUARD_KEY, None)
    return definition


def refresh_agent_runtime_guards(
    definition: dict,
    *,
    parameter_values: dict[str, Any] | None,
    effective_agent_resolver: Callable[..., dict | None],
) -> dict:
    """Refresh only Agent guards while a pre-running Task accepts inputs."""
    values = _freeze_values(definition, parameter_values)
    for node in definition.get("nodes", []):
        if node.get("node_type", "agent") != "agent":
            continue
        params = node.setdefault("node_params", {})
        guard = params.get(RUNTIME_GUARD_KEY)
        if not isinstance(guard, dict) or guard.get(
            "schema_version"
        ) != RUNTIME_GUARD_SCHEMA:
            raise WorkflowRuntimeGuardError(
                f"Task Agent 运行身份守卫无效: node={node.get('id')}"
            )
        _freeze_agent_identity(
            node,
            values=values,
            effective_agent_resolver=effective_agent_resolver,
            guard=guard,
        )
    return definition


def build_workflow_execution_identity(
    definition: dict,
    *,
    workflow_dir: Path,
) -> dict:
    """Expose hashes of the actual inline scripts used by Core execution."""
    scripts: list[dict[str, Any]] = []
    library_scripts: list[dict[str, Any]] = []
    values = _freeze_values(definition, None)
    for node in definition.get("nodes", []):
        script_path = _inline_script_path(workflow_dir, node)
        if script_path is None:
            continue
        if not script_path.is_file():
            raise WorkflowRuntimeGuardError(
                f"inline script 不存在，无法生成执行身份: {script_path}"
            )
        params = node.get("node_params") or {}
        script_identity = {
            "node_id": str(node.get("id") or ""),
            "script_name": str(params.get("script_name") or ""),
            "script_type": str(params.get("script_type") or "shell"),
            "content_sha256": file_sha256(script_path),
        }
        dependencies = _inline_script_dependencies(workflow_dir, node)
        if dependencies:
            script_identity["dependencies"] = dependencies
        scripts.append(script_identity)
        continue
    for node in definition.get("nodes", []):
        attestation = _library_script_attestation(node, values=values)
        if attestation is None:
            continue
        library_scripts.append({
            "node_id": str(node.get("id") or ""),
            **attestation,
        })
    scripts.sort(key=lambda item: (item["node_id"], item["script_name"]))
    library_scripts.sort(
        key=lambda item: (
            item["node_id"],
            item["group"],
            item["script_name"],
        )
    )
    identity = {
        "schema_version": "workflow_execution_identity.v1",
        "workflow_id": str(definition.get("workflow_id") or ""),
        "definition_version": definition.get("version"),
        "inline_scripts": scripts,
    }
    if library_scripts:
        identity["library_scripts"] = library_scripts
    return identity
