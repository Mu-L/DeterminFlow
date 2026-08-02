"""Stable contracts exposed to optional ai-company extensions."""

from .models import (
    CoreRuntime,
    ExtensionManifest,
    ExtensionPage,
    ExtensionProcess,
    HealthCheckResult,
    PromptContextRequest,
    PromptContribution,
)
from .registrar import ExtensionRegistrar
from .workflow import WorkflowRuntime

__all__ = [
    "CoreRuntime",
    "ExtensionManifest",
    "ExtensionPage",
    "ExtensionProcess",
    "ExtensionRegistrar",
    "HealthCheckResult",
    "PromptContextRequest",
    "PromptContribution",
    "WorkflowRuntime",
]
