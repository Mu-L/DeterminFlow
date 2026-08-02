"""Extension discovery, registration and lifecycle orchestration."""

from .manager import ExtensionManager
from .lifecycle import (
    ExtensionLifecycle,
    LifecycleCommandError,
    LifecycleCommandResult,
    load_extension_lifecycle,
    parse_extension_lifecycle,
    run_extension_lifecycle,
)
from .resource_ids import (
    ResourceIdConflictError,
    ResourceIdError,
    ResourceIdMapping,
    ResourceIdPlan,
    ResourceIdResolver,
    build_resource_id_plan,
    effective_resource_id,
)
from .resources import LayeredJsonConfig

__all__ = [
    "ExtensionManager",
    "ExtensionLifecycle",
    "LayeredJsonConfig",
    "LifecycleCommandError",
    "LifecycleCommandResult",
    "ResourceIdConflictError",
    "ResourceIdError",
    "ResourceIdMapping",
    "ResourceIdPlan",
    "ResourceIdResolver",
    "build_resource_id_plan",
    "effective_resource_id",
    "load_extension_lifecycle",
    "parse_extension_lifecycle",
    "run_extension_lifecycle",
]
