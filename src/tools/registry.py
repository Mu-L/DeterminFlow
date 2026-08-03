"""
统一工具注册中心 (ToolRegistry)

职责：
1. 统一注册入口：所有来源的工具（MCP / 自定义 Python 函数等）通过 register() 登记
2. 可配置分组：从 config/tool_groups_config.json 加载分组定义，工具按名称匹配到组
3. API 数据源：提供 get_tools() / get_groups() 供 /api/tools 端点查询
4. 工厂管理：存储每个工具的工厂闭包，通过 instantiate() / instantiate_all() 按需创建实例
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """统一工具注册中心"""

    def __init__(self, groups_config_path: str):
        """
        Args:
            groups_config_path: 分组配置文件的路径（如 config/tool_groups_config.json）
        """
        self._tools: dict[str, dict] = {}            # name → tool entry (metadata)
        self._tool_owners: dict[str, str] = {}        # name → core/extension owner
        self._factories: dict[str, Callable] = {}     # name → factory(**deps) -> StructuredTool
        self._groups: list[dict] = []                 # 分组定义列表
        self._tool_group_map: dict[str, str] = {}     # tool_name → group_id
        self._config_path = groups_config_path
        self._load_groups_config(groups_config_path)

    # ──────────────────────────────────────────────
    # 配置加载
    # ──────────────────────────────────────────────

    def _load_groups_config(self, config_path: str) -> None:
        """加载分组配置文件，构建 tool_name → group_id 映射"""
        path = Path(config_path)
        if not path.exists():
            logger.warning(
                f"分组配置文件不存在: {config_path}，"
                f"所有工具归入默认分组。请创建配置文件以启用分组功能"
            )
            self._groups = [{"id": "default", "name": "默认分组", "description": "未分组工具", "tool_ids": []}]
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"分组配置文件解析失败: {e}")
            self._groups = []
            return

        self._groups = data.get("groups", [])

        for group in self._groups:
            group_id = group.get("id", "")
            for tool_name in group.get("tool_ids", []):
                if tool_name in self._tool_group_map:
                    logger.warning(
                        f"工具 '{tool_name}' 已在分组 '{self._tool_group_map[tool_name]}' 中，"
                        f"被分组 '{group_id}' 覆盖"
                    )
                self._tool_group_map[tool_name] = group_id

        logger.info(
            f"已加载 {len(self._groups)} 个工具分组，"
            f"覆盖 {len(self._tool_group_map)} 个工具"
        )

    # ──────────────────────────────────────────────
    # 注册
    # ──────────────────────────────────────────────

    def register(self, name: str, description: str, parameters: dict,
                 factory: Callable | None = None, owner: str = "core") -> None:
        """注册一个工具的元数据（可选附带工厂闭包）。

        Args:
            name: 工具名称（用于匹配分组配置中的 tool_ids）
            description: 工具描述
            parameters: 参数字典 {param_name: {type, description, ...}, ...}
            factory: 可选，工厂闭包 (**deps) -> StructuredTool
        """
        existing_owner = self._tool_owners.get(name)
        if existing_owner is not None and existing_owner != owner:
            raise ValueError(f"工具注册冲突: {name} ({existing_owner} vs {owner})")
        group_id = self._tool_group_map.get(name, "__ungrouped__")

        self._tools[name] = {
            "name": name,
            "description": description,
            "group_id": group_id,
            "parameters": parameters,
            "owner": owner,
        }
        self._tool_owners[name] = owner

        if factory is not None:
            self._factories[name] = factory

        logger.debug(f"已注册工具: {name} -> 分组 '{group_id}'{' (含工厂)' if factory else ''}")

    def register_factory(self, name: str, factory: Callable, description: str, parameters: dict) -> None:
        """注册一个工具的工厂闭包和元数据（委托 register()）。

        .. deprecated::
            此方法与 ``register(name, description, parameters, factory=factory)`` 等价。
            建议直接使用 ``register()`` 并传入 ``factory`` 参数。

        Args:
            name: 工具名称
            factory: 工厂闭包 (**deps) -> StructuredTool
            description: 工具描述
            parameters: 参数字典
        """
        import warnings
        warnings.warn(
            "register_factory() is deprecated, use register() with factory= instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.register(name, description, parameters, factory=factory)

    def register_from_mcp(self, name: str, description: str, input_schema: dict,
                           factory: Callable | None = None, owner: str = "core") -> None:
        """注册一个来自 MCP Server 的工具，自动提取参数并过滤隐式 ctx_ 参数。

        Args:
            name: 工具名称
            description: 工具描述
            input_schema: MCP 工具的 input_schema（含 properties, required 等）
            factory: 可选，MCP 工具调用闭包
        """
        raw_params = input_schema.get("properties", {})
        # 过滤掉隐式上下文参数（不展示给前端）
        filtered_params = {
            k: v for k, v in raw_params.items() if not k.startswith("ctx_")
        }
        self.register(name, description, filtered_params, factory=factory, owner=owner)

    def register_from_structured_tool(self, tool: "StructuredTool", factory: Callable | None = None,
                                      owner: str = "core") -> None:
        """从一个 LangChain StructuredTool 实例注册工具的元数据（可选附带工厂）。

        Args:
            tool: LangChain StructuredTool 实例（用于提取 name/description/parameters）
            factory: 可选，工厂闭包 (**deps) -> StructuredTool，未提供时从 tool 自动生成
        """
        name = tool.name
        description = tool.description or ""

        # 获取 JSON Schema 格式的参数定义
        # Pydantic v2: model_json_schema()，v1: schema()
        properties = {}
        try:
            schema_model = tool.get_input_schema()
            if hasattr(schema_model, "model_json_schema"):
                schema_dict = schema_model.model_json_schema()
            elif hasattr(schema_model, "schema"):
                schema_dict = schema_model.schema()
            else:
                schema_dict = {}
            properties = schema_dict.get("properties", {})
        except Exception:
            properties = {}

        params = {}
        for pname, prop in properties.items():
            entry: dict = {}
            ptype = prop.get("type")
            if ptype:
                entry["type"] = ptype
            pdesc = prop.get("description")
            if pdesc:
                entry["description"] = pdesc
            if prop.get("enum"):
                entry["enum"] = prop["enum"]
            if prop.get("items"):
                entry["items"] = prop["items"]
            if prop.get("default") is not None:
                entry["default"] = prop["default"]
            params[pname] = entry

        self.register(name, description, params, factory=factory, owner=owner)

    def register_group(self, group: dict, *, owner: str = "core") -> None:
        """Register an immutable tool group contributed during startup."""
        group_id = group.get("id", "")
        if not group_id:
            raise ValueError("工具分组缺少 id")
        if any(existing.get("id") == group_id for existing in self._groups):
            raise ValueError(f"工具分组注册冲突: {group_id}")
        registered = {**group, "owner": owner}
        for tool_name in registered.get("tool_ids", []):
            if tool_name in self._tool_group_map:
                raise ValueError(f"工具分组映射冲突: {tool_name}")
        self._groups.append(registered)
        for tool_name in registered.get("tool_ids", []):
            self._tool_group_map[tool_name] = group_id

    def unregister_owner(self, owner: str) -> None:
        """回滚指定扩展注册的工具和工具分组。"""
        tool_names = [
            name
            for name, current_owner in self._tool_owners.items()
            if current_owner == owner
        ]
        for name in tool_names:
            self._tools.pop(name, None)
            self._tool_owners.pop(name, None)
            self._factories.pop(name, None)

        group_ids = {
            group.get("id")
            for group in self._groups
            if group.get("owner") == owner
        }
        self._groups = [
            group for group in self._groups if group.get("owner") != owner
        ]
        self._tool_group_map = {
            name: group_id
            for name, group_id in self._tool_group_map.items()
            if group_id not in group_ids
        }
        logger.info(
            "已回滚扩展工具注册: %s (%s tools, %s groups)",
            owner,
            len(tool_names),
            len(group_ids),
        )

    # ──────────────────────────────────────────────
    # 持久化
    # ──────────────────────────────────────────────

    def _save_config(self) -> None:
        """将当前分组配置写回 JSON 文件（原子写入）"""
        path = Path(self._config_path)
        tmp_path = str(path) + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"groups": self._groups}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(path))
            logger.info(f"分组配置已保存到 {path}")
        except (IOError, OSError) as e:
            logger.error(f"保存分组配置失败: {e}")
            raise

    # ──────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────

    def add_group(self, group_id: str, name: str, description: str = "") -> dict | None:
        """添加新分组。如果 ID 已存在返回 None。"""
        if any(g["id"] == group_id for g in self._groups):
            logger.warning(f"分组 '{group_id}' 已存在")
            return None
        group = {"id": group_id, "name": name, "description": description, "tool_ids": []}
        self._groups.append(group)
        try:
            self._save_config()
        except (IOError, OSError):
            self._groups.pop()  # rollback
            return None
        logger.info(f"已添加分组: {group_id}")
        return group

    def update_group(self, group_id: str, updates: dict) -> bool:
        """更新分组的 name/description。返回是否成功。"""
        for g in self._groups:
            if g["id"] == group_id:
                # 保存旧值用于回滚
                old_values = {}
                if "name" in updates:
                    old_values["name"] = g["name"]
                    g["name"] = updates["name"]
                if "description" in updates:
                    old_values["description"] = g["description"]
                    g["description"] = updates["description"]
                try:
                    self._save_config()
                except (IOError, OSError):
                    g.update(old_values)  # rollback
                    return False
                logger.info(f"已更新分组: {group_id}")
                return True
        logger.warning(f"分组 '{group_id}' 不存在")
        return False

    def delete_group(self, group_id: str) -> tuple[bool, str]:
        """删除分组。如果分组下仍有工具映射则返回 False 和原因。"""
        # 检查是否有工具映射到此组
        mapped_tools = [tname for tname, gid in self._tool_group_map.items() if gid == group_id]
        if mapped_tools:
            msg = f"分组 '{group_id}' 下仍有 {len(mapped_tools)} 个工具，无法删除"
            logger.warning(msg)
            return False, msg

        # 保存旧值用于回滚
        old_groups = self._groups[:]
        old_map = self._tool_group_map.copy()

        # 从 groups 列表中移除
        self._groups = [g for g in self._groups if g["id"] != group_id]
        if len(self._groups) == len(old_groups):
            msg = f"分组 '{group_id}' 不存在"
            return False, msg

        # 清理 tool_group_map 中已删除组的残留映射（安全清理）
        self._tool_group_map = {k: v for k, v in self._tool_group_map.items() if v != group_id}

        try:
            self._save_config()
        except (IOError, OSError):
            self._groups = old_groups  # rollback
            self._tool_group_map = old_map
            return False, "保存配置失败，删除操作已回滚"

        logger.info(f"已删除分组: {group_id}")
        return True, ""

    # ──────────────────────────────────────────────
    # 实例化
    # ──────────────────────────────────────────────

    def _assert_tool_owner_active(self, name: str, owner: str) -> None:
        """Fail closed when an instantiated non-Core tool lost its owner."""
        current_owner = self._tool_owners.get(name)
        if name not in self._tools or current_owner != owner:
            raise RuntimeError(
                f"工具 '{name}' 已不可用: owner '{owner}' 已注销或发生变更"
            )

    def _guard_non_core_tool(
        self,
        registered_name: str,
        owner: str,
        tool: StructuredTool,
    ) -> StructuredTool:
        """Wrap a tool so stale Session graphs cannot call an unloaded owner."""
        updates: dict[str, Callable | None] = {}

        if tool.func is not None:
            def _guarded_sync(
                *args: Any,
                config: RunnableConfig = None,  # type: ignore[assignment]
                callbacks: Any = None,
                **kwargs: Any,
            ) -> Any:
                self._assert_tool_owner_active(registered_name, owner)
                return tool._run(
                    *args,
                    config=config,
                    run_manager=callbacks,
                    **kwargs,
                )

            updates["func"] = _guarded_sync

        if tool.coroutine is not None:
            async def _guarded_async(
                *args: Any,
                config: RunnableConfig = None,  # type: ignore[assignment]
                callbacks: Any = None,
                **kwargs: Any,
            ) -> Any:
                self._assert_tool_owner_active(registered_name, owner)
                return await tool._arun(
                    *args,
                    config=config,
                    run_manager=callbacks,
                    **kwargs,
                )

            updates["coroutine"] = _guarded_async

        return tool.model_copy(update=updates)

    def instantiate(self, name: str, **deps) -> StructuredTool | None:
        """按名创建单个工具实例。

        从 _factories 中取出工厂闭包，传入依赖字典调用并返回新实例。
        每次调用都创建全新实例，避免上下文污染。

        Args:
            name: 工具名称
            **deps: 工厂闭包所需的依赖（如 session_manager, llm_client 等）

        Returns:
            新创建的 StructuredTool 实例，工厂不存在时返回 None
        """
        factory = self._factories.get(name)
        if factory is None:
            logger.warning(f"工具 '{name}' 无已注册工厂，无法实例化")
            return None
        owner = self._tool_owners.get(name)
        if owner is None:
            logger.warning(f"工具 '{name}' 已失去注册 owner，无法实例化")
            return None

        try:
            tool = factory(**deps)
            if tool is None or owner == "core":
                return tool
            if not isinstance(tool, StructuredTool):
                raise TypeError(
                    f"工具工厂 '{name}' 返回了非 StructuredTool 实例"
                )
            return self._guard_non_core_tool(name, owner, tool)
        except Exception as e:
            logger.error(f"实例化工具 '{name}' 失败: {e}", exc_info=True)
            return None

    def instantiate_all(self, names: set[str] | None = None, **deps) -> list[StructuredTool]:
        """批量创建工具实例。

        Args:
            names: 要创建的工具名称集合，None 表示创建全部已注册工厂的工具
            **deps: 工厂闭包所需的依赖字典

        Returns:
            StructuredTool 实例列表
        """
        target_names = names if names is not None else set(self._factories.keys())
        tools = []
        for name in sorted(target_names):
            tool = self.instantiate(name, **deps)
            if tool is not None:
                tools.append(tool)
        return tools

    def get_factory_names(self) -> set[str]:
        """返回所有已注册工厂的工具名集合"""
        return set(self._factories.keys())

    # ──────────────────────────────────────────────
    # 查询
    # ──────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        """返回所有已注册工具列表"""
        return list(self._tools.values())

    def get_groups(self) -> list[dict]:
        """返回分组定义列表（不包含 tool_ids，前端不需了解具体映射）"""
        return [
            {"id": g["id"], "name": g["name"], "description": g["description"]}
            for g in self._groups
        ]

    def get_tool_group_id(self, tool_name: str) -> str:
        """查询指定工具的分组 ID，未匹配返回 '__ungrouped__'"""
        return self._tool_group_map.get(tool_name, "__ungrouped__")

    def get_tool(self, name: str) -> dict | None:
        """按名称查询已注册的工具"""
        return self._tools.get(name)


# ============================================================
# 统一工具工厂注册
# ============================================================

# coding 工具名称集合（单一 source of truth 在 coding_tools.py）
from src.tools.coding_tools import CODING_TOOL_NAMES as _CODING_TOOL_NAMES


def register_all_tool_factories(registry: ToolRegistry, *,
                                 mcp_client,
                                 session_manager,
                                 prompt_manager=None,
                                 skill_manager=None,
                                 rule_manager=None,
                                 approval_manager=None,
                                 llm_client=None) -> None:
    """统一注册所有工具的工厂闭包和元数据到 ToolRegistry。

    这是系统中所有工具工厂的唯一定义入口。添加新工具时，
    只需在此函数中新增 registry.register(..., factory=f) 调用，
    并在 config/tool_groups_config.json 的对应分组中加入工具名。

    Args:
        registry: ToolRegistry 实例
        mcp_client: MCPClient 实例
        session_manager: SessionManager 实例
        skill_manager: SkillManager 实例（可选）
        approval_manager: ApprovalManager 实例（可选）
        llm_client: LLM 客户端（可选，session 工具需要）
    """
    # ── 1. Prompt 工具（直接复用主进程的分层配置）──
    if prompt_manager:
        from src.tools.prompt_tools import create_prompt_tools

        for tool in create_prompt_tools(prompt_manager):
            registry.register_from_structured_tool(
                tool,
                factory=lambda _name=tool.name, **deps: next(
                    current
                    for current in create_prompt_tools(
                        deps.get("prompt_manager", prompt_manager)
                    )
                    if current.name == _name
                ),
            )

    # ── 2. 外部 MCP 工具（排除 coding 工具名）──
    for tool_def in mcp_client.get_tools():
        name = tool_def["name"]
        if name in _CODING_TOOL_NAMES:
            continue
        desc = tool_def.get("description", "")
        input_schema = tool_def.get("input_schema", {})

        # 为每个 MCP 工具创建工厂闭包
        # 所有循环变量（name / desc / input_schema）均通过默认参数捕获，
        # 利用 Python 默认参数在定义时求值的特性，避免闭包延迟求值导致串值
        def _mcp_factory(_name: str = name, _desc: str = desc, _input_schema: dict = input_schema):
            async def _arun(**kwargs) -> str:
                return await mcp_client.call_tool(_name, kwargs)

            def _run(**kwargs) -> str:
                raise NotImplementedError("MCP 工具仅支持异步调用")

            from langchain_core.tools import StructuredTool
            from src.mcp.tool_adapter import _build_args_model
            args_model = _build_args_model(_name, _input_schema)
            return StructuredTool(
                name=_name, description=_desc,
                args_schema=args_model, func=_run, coroutine=_arun,
            )
        registry.register_from_mcp(
            name=name, description=desc, input_schema=input_schema,
            factory=lambda _f=_mcp_factory, **deps: _f(),
            owner=f"mcp:{tool_def.get('server', 'external')}",
        )

    # ── 3. Coding 工具（8 个直接实现）──
    from src.tools.coding_tools import create_coding_tools_direct
    coding_tools = create_coding_tools_direct(approval_manager)
    for tool in coding_tools:
        # coding 工具的工厂：每次调用 create_coding_tools_direct 重新创建
        def _make_coding_factory(tool_name: str):
            def _factory(**deps):
                am = deps.get("approval_manager", approval_manager)
                tools = create_coding_tools_direct(am)
                for t in tools:
                    if t.name == tool_name:
                        return t
                return None
            return _factory

        registry.register_from_structured_tool(
            tool,
            factory=_make_coding_factory(tool.name),
        )

    # ── 4. Session 工具（5 个：create_sub_session / check_sub_progress / ...）──
    from src.mcp.tool_adapter import create_session_tools
    session_tools = create_session_tools(session_manager, llm_client)
    for tool in session_tools:
        def _make_session_factory(tool_name: str):
            def _factory(**deps):
                sm = deps.get("session_manager", session_manager)
                llm = deps.get("llm_client", llm_client)
                tools = create_session_tools(sm, llm)
                for t in tools:
                    if t.name == tool_name:
                        return t
                return None
            return _factory

        registry.register_from_structured_tool(
            tool,
            factory=_make_session_factory(tool.name),
        )

    # ── 5. Skills 工具 ──
    if skill_manager:
        from src.skills.tools import create_get_skills_tool, create_skill_manage_tool, create_skill_group_manage_tool

        get_skills_tool = create_get_skills_tool(skill_manager)
        registry.register_from_structured_tool(
            get_skills_tool,
            factory=lambda **deps: create_get_skills_tool(
                deps.get("skill_manager", skill_manager)
            ),
        )

        skill_manage_tool = create_skill_manage_tool(skill_manager)
        registry.register_from_structured_tool(
            skill_manage_tool,
            factory=lambda **deps: create_skill_manage_tool(
                deps.get("skill_manager", skill_manager)
            ),
        )

        skill_group_manage_tool = create_skill_group_manage_tool(skill_manager)
        registry.register_from_structured_tool(
            skill_group_manage_tool,
            factory=lambda **deps: create_skill_group_manage_tool(
                deps.get("skill_manager", skill_manager)
            ),
        )

    # ── 6. Rules 工具 ──
    if rule_manager:
        from src.rules.tools import create_get_rules_tool, create_rule_manage_tool, create_rule_group_manage_tool

        get_rules_tool = create_get_rules_tool(rule_manager)
        registry.register_from_structured_tool(
            get_rules_tool,
            factory=lambda **deps: create_get_rules_tool(
                deps.get("rule_manager", rule_manager)
            ),
        )

        rule_manage_tool = create_rule_manage_tool(rule_manager)
        registry.register_from_structured_tool(
            rule_manage_tool,
            factory=lambda **deps: create_rule_manage_tool(
                deps.get("rule_manager", rule_manager)
            ),
        )

        rule_group_manage_tool = create_rule_group_manage_tool(rule_manager)
        registry.register_from_structured_tool(
            rule_group_manage_tool,
            factory=lambda **deps: create_rule_group_manage_tool(
                deps.get("rule_manager", rule_manager)
            ),
        )

    # ── 6. Workflow 工具（set_workflow_variable / start_workflow_task / approve_node
    #        + 6 个新增 chat main 查询/操作工具）──
    from src.workflow.tools import (
        create_set_workflow_variable_tool,
        create_start_workflow_task_tool,
        create_approve_node_tool,
        create_list_workflows_tool,
        create_get_workflow_tool,
        create_create_and_attach_task_tool,
        create_list_tasks_tool,
        create_get_task_status_tool,
        create_stop_task_tool,
    )
    from src.workflow.main_result_tools import (
        create_get_task_result_tool,
        create_read_task_artifact_tool,
        create_get_node_messages_tool,
    )
    from src.workflow.main_node_control_tools import (
        create_retry_node_tool,
        create_skip_node_tool,
    )
    sm = session_manager  # 从函数参数获取

    # 通用单参数工厂：仅需 workflow_manager
    def _make_wf_factory(creat):
        def _factory(**d):
            wm = d.get("workflow_manager") or getattr(sm, '_workflow_manager', None)
            return creat(wm)
        return _factory

    # 双参数工厂：workflow_manager + session_manager（从 session 读取绑定）
    def _make_wf_sm_factory(creat):
        def _factory(**d):
            wm = d.get("workflow_manager") or getattr(sm, '_workflow_manager', None)
            s_mgr = d.get("session_manager", sm)
            return creat(wm, s_mgr)
        return _factory

    # 已有 3 个工具（现在需要 workflow_manager + session_manager）
    wf_mgr = getattr(sm, '_workflow_manager', None)
    wf_tool_creators = [
        create_set_workflow_variable_tool,
        create_start_workflow_task_tool,
        create_approve_node_tool,
    ]
    for creator in wf_tool_creators:
        tool_instance = creator(wf_mgr, sm)
        registry.register_from_structured_tool(
            tool_instance,
            factory=_make_wf_sm_factory(creator),
        )

    # 纯查询工具（仅需 workflow_manager，不读 session 绑定）
    query_tool_creators = [
        create_list_workflows_tool,
        create_get_workflow_tool,
    ]
    for creator in query_tool_creators:
        tool_instance = creator(wf_mgr)
        registry.register_from_structured_tool(
            tool_instance,
            factory=_make_wf_factory(creator),
        )

    # 查询/操作工具（需 session 绑定：workflow_manager + session_manager）
    binding_tool_creators = [
        create_list_tasks_tool,
        create_get_task_status_tool,
        create_stop_task_tool,
        create_get_task_result_tool,
        create_read_task_artifact_tool,
        create_get_node_messages_tool,
        create_retry_node_tool,
        create_skip_node_tool,
    ]
    for creator in binding_tool_creators:
        tool_instance = creator(wf_mgr, sm)
        registry.register_from_structured_tool(
            tool_instance,
            factory=_make_wf_sm_factory(creator),
        )

    # create_and_attach_task 需要 workflow_manager + session_manager
    create_attach_instance = create_create_and_attach_task_tool(wf_mgr, sm)
    def _make_create_attach_factory():
        def _factory(**d):
            wm = d.get("workflow_manager") or getattr(sm, '_workflow_manager', None)
            s_mgr = d.get("session_manager", sm)
            return create_create_and_attach_task_tool(wm, s_mgr)
        return _factory
    registry.register_from_structured_tool(
        create_attach_instance,
        factory=_make_create_attach_factory(),
    )

    # ── 7. Cron 工具 ──
    from src.tools.cron_tools import create_cronjob_tool
    cron_tool = create_cronjob_tool(session_manager)
    registry.register_from_structured_tool(
        cron_tool,
        factory=lambda **deps: create_cronjob_tool(
            deps.get("session_manager", session_manager)
        ),
    )

    logger.info(
        f"已注册 {len(registry.get_tools())} 个工具元数据 + "
        f"{len(registry.get_factory_names())} 个工厂到 ToolRegistry"
    )
