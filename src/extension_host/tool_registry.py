"""Owner-scoped ToolRegistry facade exposed to one Extension."""

from __future__ import annotations

from typing import Any


class ExtensionToolRegistry:
    """Force all registrations to use the current Extension owner."""

    def __init__(self, registry: Any, owner: str):
        self._registry = registry
        self._owner = owner

    def _owned_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        supplied_owner = kwargs.pop("owner", self._owner)
        if supplied_owner != self._owner:
            raise ValueError(
                f"扩展 {self._owner} 不能以其他 owner 注册资源: {supplied_owner}"
            )
        return {**kwargs, "owner": self._owner}

    def register(self, *args: Any, **kwargs: Any) -> Any:
        return self._registry.register(*args, **self._owned_kwargs(kwargs))

    def register_from_mcp(self, *args: Any, **kwargs: Any) -> Any:
        return self._registry.register_from_mcp(
            *args,
            **self._owned_kwargs(kwargs),
        )

    def register_from_structured_tool(self, *args: Any, **kwargs: Any) -> Any:
        return self._registry.register_from_structured_tool(
            *args,
            **self._owned_kwargs(kwargs),
        )

    def register_group(self, *args: Any, **kwargs: Any) -> Any:
        return self._registry.register_group(*args, **self._owned_kwargs(kwargs))

    def register_factory(
        self,
        name: str,
        factory: Any,
        description: str,
        parameters: dict,
    ) -> Any:
        return self._registry.register(
            name,
            description,
            parameters,
            factory=factory,
            owner=self._owner,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)
