"""
节点插件注册中心 — 管理 node_type → plugin class 映射。

新节点类型只需在此目录下创建 Python 文件，定义继承 BaseNodePlugin 的类，
并在模块末尾调用 register() 即可自动注册。
"""
from __future__ import annotations

import logging
from typing import Type

from .base import BaseNodePlugin, NodeContext, NodeResult

logger = logging.getLogger(__name__)

__all__ = ["BaseNodePlugin", "NodeContext", "NodeResult", "registry"]


class NodeRegistry:
    """节点插件注册中心。

    使用方式：
        from src.workflow.nodes import registry
        plugin = registry.get("agent")
        result = await plugin.execute(ctx)
    """

    def __init__(self):
        self._plugins: dict[str, Type[BaseNodePlugin]] = {}
        self._owners: dict[str, str] = {}

    def register(self, plugin_cls: Type[BaseNodePlugin], *, owner: str = "core"):
        """注册一个节点插件。"""
        if not plugin_cls.node_type:
            raise ValueError(f"插件 {plugin_cls.__name__} 缺少 node_type")
        existing_owner = self._owners.get(plugin_cls.node_type)
        if existing_owner is not None and existing_owner != owner:
            raise ValueError(
                f"工作流节点注册冲突: {plugin_cls.node_type} "
                f"({existing_owner} vs {owner})"
            )
        self._plugins[plugin_cls.node_type] = plugin_cls
        self._owners[plugin_cls.node_type] = owner
        logger.debug(f"节点插件已注册: {plugin_cls.node_type}")

    def get(self, node_type: str) -> Type[BaseNodePlugin] | None:
        """根据 node_type 获取插件类。"""
        return self._plugins.get(node_type)

    def unregister_owner(self, owner: str) -> None:
        """移除指定扩展注册的全部节点，Core 节点不受影响。"""
        node_types = [
            node_type
            for node_type, current_owner in self._owners.items()
            if current_owner == owner
        ]
        for node_type in node_types:
            self._plugins.pop(node_type, None)
            self._owners.pop(node_type, None)
            logger.debug("节点插件已卸载: %s/%s", owner, node_type)

    def list_all(self) -> list[dict]:
        """列出所有已注册插件（供前端节点面板使用）。"""
        return [
            {
                "node_type": p.node_type,
                "label": p.label,
                "icon": p.default_icon,
                "params_schema": p().params_schema,
                "owner": self._owners[p.node_type],
            }
            for p in self._plugins.values()
        ]

    def __contains__(self, node_type: str) -> bool:
        return node_type in self._plugins


# 全局注册中心实例
registry = NodeRegistry()

# 导入并注册各插件模块
from . import agent   # noqa: E402, F401
from . import approval  # noqa: E402, F401
from . import script  # noqa: E402, F401
from . import subprocess  # noqa: E402, F401

registry.register(agent.AgentNode)
registry.register(approval.ApprovalNode)
registry.register(script.ScriptNode)
registry.register(subprocess.SubprocessNode)
