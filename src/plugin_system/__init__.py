"""Public plugin package storage and process lifecycle interfaces."""

from .dependencies import (
    PluginDependencyError,
    install_applied_plugin_requirements,
    install_plugin_requirements,
)
from .models import (
    PluginLockRecord,
    PluginRevision,
    ProcessHealthCheck,
    ProcessSpec,
    validate_plugin_id,
    validate_plugin_ref,
    validate_plugin_subdirectory,
    validate_resource_prefix,
)
from .processes import ProcessManager, ProcessManagerError, ProcessStartError
from .registry import PluginRegistryConfig, PluginRegistryError
from .store import (
    InvalidPluginPackageError,
    PluginStore,
    PluginStoreError,
    SourceTrustError,
)

__all__ = [
    "InvalidPluginPackageError",
    "PluginLockRecord",
    "PluginDependencyError",
    "PluginRegistryConfig",
    "PluginRegistryError",
    "PluginRevision",
    "PluginStore",
    "PluginStoreError",
    "ProcessHealthCheck",
    "ProcessManager",
    "ProcessManagerError",
    "ProcessSpec",
    "ProcessStartError",
    "SourceTrustError",
    "install_applied_plugin_requirements",
    "install_plugin_requirements",
    "validate_plugin_id",
    "validate_plugin_ref",
    "validate_plugin_subdirectory",
    "validate_resource_prefix",
]
