"""
工作流数据模型定义

定义 WorkflowNode、WorkflowEdge、WorkflowDef、WorkflowState 等核心数据结构，
用于工作流定义持久化和运行时状态管理。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .model_utils import _generate_id, _now_iso
from .failure_policy import (
    MAX_AUTO_RETRY_COUNT,
    MAX_AUTO_RETRY_INTERVAL_SECONDS,
)
from .runtime_models import (
    NodeExecutionState,
    WorkflowRunRecord,
    WorkflowState,
    WorkflowTask,
)
from .variable_resolution import (
    _PLACEHOLDER_RE,
    _try_parse_json,
    parse_loop_expression,
    resolve_placeholders,
)


def _coerce_integer_setting(value: Any, *, default: int) -> Any:
    """兼容历史 JSON 中的数字字符串，同时保留非法值供 validate 报错。"""
    if value is None:
        return default
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("+-").isdigit():
            return int(stripped)
    return value


def _is_integer_in_range(value: Any, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


# ============================================================
# 执行方案数据模型
# ============================================================

@dataclass
class ExecutionScheme:
    """工作流执行方案 — 保存一组选中的节点，用于快捷复用节点选择。"""
    id: str = field(default_factory=lambda: _generate_id("scheme"))
    name: str = ""                              # 方案名称
    selected_node_ids: list[str] = field(default_factory=list)  # 选中的节点 ID 列表
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionScheme":
        return cls(
            id=data.get("id", _generate_id("scheme")),
            name=data.get("name", ""),
            selected_node_ids=data.get("selected_node_ids", []),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
        )


# ============================================================
# 工作流定义模型
# ============================================================

@dataclass
class WorkflowNode:
    """单个工作流节点的定义。"""
    id: str = field(default_factory=_generate_id)
    label: str = ""                              # 节点显示名称
    node_type: str = "agent"                     # 节点类型："agent" / "approval"（参照 bk-sops 插件体系）
    agent_type: str = "default"                  # 引用 agents_config.json 中的类型（仅 node_type="agent" 时有效）
    system_prompt_template: str = ""             # 注入 system prompt 的补充指令（custom_prompt）
    first_message: str = ""                      # 首条任务消息（task_description）
    position: dict[str, float] = field(default_factory=lambda: {"x": 0, "y": 0})
    var_bindings: dict[str, dict] = field(default_factory=dict)  # 字段→全局变量绑定 {field: {original_value: str, var_key: str}}
    node_params: dict[str, Any] = field(default_factory=dict)    # 节点类型特有参数（支持嵌套 JSON 值）
    auto_flow: bool = False                     # 自动流转：LLM 输出完成即视为成功（Agent 节点专属）
    enable_complete_node_task: bool = True      # 是否注入 complete_node_task 工具
    output_variable: str = ""                   # 输出变量 key：Agent 最后一轮回复文本写入此变量
    enable_reject_upstream: bool = False        # 是否注入 reject_upstream 工具（允许下游拒绝上游产出）
    max_reject_count: int = 3                   # 最大拒绝次数（enable_reject_upstream 为 true 时生效）
    save_output_to_file: bool = False           # 是否将LLM最后输出保存到文件
    output_file_path: str = ""                  # 保存路径（仅限 workspace 内，支持相对路径/{{key}}占位符）
    model_override: str = ""                    # 模型覆盖（格式 "provider_id:model_name"，空则使用 agent 类型默认模型，支持 {{key}} 占位符）
    sub_workflow_id: str | None = None          # 引用的子流程模板 ID（node_type="subprocess" 时有效）
    sub_scheme_id: str | None = None            # 子流程使用的执行方案 ID（空=全部执行，node_type="subprocess" 时有效）
    sub_workflow_params: dict[str, dict] = field(default_factory=dict)  # {var_key: {value, use_default}}
    fail_auto_skip: bool = False                # 开启后节点执行失败自动跳过，继续下一节点
    auto_retry_count: int = 0                   # 首次失败后的自动重试次数
    auto_retry_interval_seconds: int = 0        # 自动重试固定间隔（秒）

    def to_dict(self) -> dict:
        d = asdict(self)
        # 移除 None 值的可选字段，保持 JSON 简洁
        if d.get("sub_workflow_id") is None:
            d.pop("sub_workflow_id", None)
        if d.get("sub_scheme_id") is None:
            d.pop("sub_scheme_id", None)
        # 移除空字符串的可选字段
        if not d.get("model_override"):
            d.pop("model_override", None)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowNode":
        raw_bindings = data.get("var_bindings", {})
        bindings: dict[str, dict] = {}
        for field, binding in raw_bindings.items():
            if isinstance(binding, dict):
                b = dict(binding)
                if "var_key" not in b:
                    b["var_key"] = f"{data.get('id', '')}_{field}"
                bindings[field] = b
        return cls(
            id=data.get("id", _generate_id()),
            label=data.get("label", ""),
            node_type=data.get("node_type", "agent"),  # 无 node_type 的旧数据视为 agent
            agent_type=data.get("agent_type", "default"),
            system_prompt_template=data.get("system_prompt_template", ""),
            first_message=data.get("first_message", ""),
            position=data.get("position", {"x": 0, "y": 0}),
            var_bindings=bindings,
            node_params=data.get("node_params", {}),
            auto_flow=data.get("auto_flow", False),
            enable_complete_node_task=data.get("enable_complete_node_task", True),
            output_variable=data.get("output_variable", ""),
            enable_reject_upstream=data.get("enable_reject_upstream", False),
            max_reject_count=int(data.get("max_reject_count", 3)),
            save_output_to_file=data.get("save_output_to_file", False),
            output_file_path=data.get("output_file_path", ""),
            model_override=data.get("model_override", ""),
            sub_workflow_id=data.get("sub_workflow_id"),
            sub_scheme_id=data.get("sub_scheme_id"),
            sub_workflow_params=data.get("sub_workflow_params", {}),
            auto_retry_count=_coerce_integer_setting(
                data.get("auto_retry_count", 0),
                default=0,
            ),
            auto_retry_interval_seconds=_coerce_integer_setting(
                data.get("auto_retry_interval_seconds", 0),
                default=0,
            ),
            fail_auto_skip=data.get("fail_auto_skip", False),
        )


@dataclass
class WorkflowEdge:
    """两个节点之间的有向边。"""
    id: str = field(default_factory=lambda: _generate_id("edge"))
    source: str = ""   # 上游节点 ID
    target: str = ""   # 下游节点 ID
    condition: dict | None = None  # 仅条件网关的出边携带
    # condition = {"expression": "{{score}} > 80", "label": "高分", "is_default": False}

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowEdge":
        return cls(
            id=data.get("id", _generate_id("edge")),
            source=data.get("source", ""),
            target=data.get("target", ""),
            condition=data.get("condition"),
        )


@dataclass
class WorkflowGateway:
    """工作流网关节点 — 独立于可执行节点的引擎级并行控制原语。

    不通过 NodeRegistry 驱动执行，由引擎直接调度。
    循环网关的循环语义通过出边条件表达式定义（for item in list / for i in range(N)）。
    """
    id: str = field(default_factory=lambda: _generate_id("gw"))
    gateway_type: str = "parallel"        # "parallel" | "converge" | "condition" | "loop"
    label: str = ""                        # 展示名（默认 "并行网关" / "汇聚网关"）
    position: dict[str, float] = field(default_factory=lambda: {"x": 0, "y": 0})
    converge_gateway_id: str | None = None  # 仅 parallel 有值，指向配对汇聚网关

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowGateway":
        return cls(
            id=data.get("id", _generate_id("gw")),
            gateway_type=data.get("gateway_type", "parallel"),
            label=data.get("label", ""),
            position=data.get("position", {"x": 0, "y": 0}),
            converge_gateway_id=data.get("converge_gateway_id"),
        )


@dataclass
class WorkflowVariable:
    """工作流参数变量定义 — 参照 bk-sops 的全局变量设计。

    变量定义属于工作流模板级（WorkflowDef），四种来源：
    - input: 用户填参变量，在填参页面可编辑（text/textarea/select/file）
    - output: 节点产出变量，运行时自动填充，填参页面只读
    """
    key: str = ""                                  # 唯一标识（用于 {{key}} 占位符引用）
    name: str = ""                                 # 前端展示名
    type: str = "text"                             # "text" | "textarea" | "select" | "file" | "list" | "dict"
    default: str = ""                              # 默认值
    required: bool = False                         # 是否必填
    description: str = ""                          # 变量说明
    options: list[dict] = field(default_factory=list)  # select 类型选项：[{"name":"展示","value":"填充值"}]
    source_type: str = "input"                     # "input" | "output" — 变量来源
    source_node_id: str = ""                       # 输出变量时标记来源节点 ID
    hidden: bool = False                           # 是否隐藏（填参页面默认折叠）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowVariable":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            type=data.get("type", "text"),
            default=data.get("default", ""),
            required=data.get("required", False),
            description=data.get("description", ""),
            options=data.get("options", []),
            source_type=data.get("source_type", "input"),
            source_node_id=data.get("source_node_id", ""),
            hidden=data.get("hidden", False),
        )


@dataclass
class WorkflowDef:
    """完整的工作流定义。"""
    workflow_id: str = field(default_factory=lambda: _generate_id("wf"))
    name: str = ""
    version: int = 1
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    variables: list[WorkflowVariable] = field(default_factory=list)
    gateways: list[WorkflowGateway] = field(default_factory=list)
    execution_schemes: list[ExecutionScheme] = field(default_factory=list)
    http_execution_policy: str = "public"
    start_position: dict[str, float] = field(default_factory=lambda: {"x": 300, "y": 50})
    end_position: dict[str, float] = field(default_factory=lambda: {"x": 300, "y": 550})

    @staticmethod
    def _find_convergence_node(
        edges: list, branch_targets: list[str], end_node: str = "__end__"
    ) -> str | None:
        """BFS 从所有分支目标出发，找到第一个共同可达节点（汇合点）。

        用于条件网关：确定所有分支会在哪里重新汇合。
        如果所有分支都至少能到 end_node，返回 end_node；否则返回 None。
        """
        if len(branch_targets) < 2:
            return end_node

        # 构建邻接表
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e.source, []).append(e.target)

        # BFS 从每个分支目标出发，收集可达集合（含 end_node）
        reachable_sets: list[set[str]] = []
        for target in branch_targets:
            reachable: set[str] = set()
            queue = [target]
            visited: set[str] = set()
            while queue:
                curr = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                reachable.add(curr)
                for t in adj.get(curr, []):
                    if t not in visited:
                        queue.append(t)
            reachable_sets.append(reachable)

        # 取交集
        common = reachable_sets[0].copy()
        for rs in reachable_sets[1:]:
            common &= rs

        if not common:
            return end_node

        # 从第一个分支目标 BFS，返回第一个在 common 中的节点（不含分支目标自身）
        queue = [branch_targets[0]]
        visited = set()
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            if curr in common and curr not in branch_targets:
                return curr
            for t in adj.get(curr, []):
                if t not in visited:
                    queue.append(t)
        return end_node

    @staticmethod
    def _collect_branch_nodes(
        edges: list, node_ids: set[str],
        start_node: str, convergence_node: str,
    ) -> set[str]:
        """BFS 从 start_node 出发，收集直到 convergence_node（不含）之间的可执行节点。

        node_ids: 所有真实可执行节点的 ID 集合（排除网关等）。
        """
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e.source, []).append(e.target)

        result: set[str] = set()
        queue = [start_node]
        visited: set[str] = set()
        while queue:
            curr = queue.pop(0)
            if curr in visited or curr == convergence_node:
                continue
            visited.add(curr)
            if curr in node_ids:
                result.add(curr)
            for t in adj.get(curr, []):
                if t not in visited and t != convergence_node:
                    queue.append(t)
        return result

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "variables": [v.to_dict() for v in self.variables],
            "gateways": [g.to_dict() for g in self.gateways],
            "execution_schemes": [s.to_dict() for s in self.execution_schemes],
            "http_execution_policy": self.http_execution_policy,
            "start_position": self.start_position,
            "end_position": self.end_position,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowDef":
        nodes = [WorkflowNode.from_dict(n) for n in data.get("nodes", [])]
        edges = [WorkflowEdge.from_dict(e) for e in data.get("edges", [])]
        variables = [WorkflowVariable.from_dict(v) for v in data.get("variables", [])]
        gateways = [WorkflowGateway.from_dict(g) for g in data.get("gateways", [])]
        execution_schemes = [ExecutionScheme.from_dict(s) for s in data.get("execution_schemes", [])]
        raw_version = data.get("version", 1)
        try:
            version = int(raw_version)
        except (ValueError, TypeError):
            version = int(float(raw_version)) if isinstance(raw_version, str) else 1
        instance = cls(
            workflow_id=data.get("workflow_id", _generate_id("wf")),
            name=data.get("name", ""),
            version=version,
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            nodes=nodes,
            edges=edges,
            variables=variables,
            gateways=gateways,
            execution_schemes=execution_schemes,
            http_execution_policy=str(
                data.get("http_execution_policy") or "public"
            ),
            start_position=data.get("start_position", {"x": 300, "y": 50}),
            end_position=data.get("end_position", {"x": 300, "y": 550}),
        )
        instance._rebuild_caches()
        return instance

    def bump_version(self):
        """版本号递增。"""
        self.version += 1
        self.updated_at = _now_iso()

    def _rebuild_caches(self):
        """重建内部索引缓存"""
        self._nodes_by_id: dict[str, WorkflowNode] = {n.id: n for n in self.nodes}
        self._gateways_by_id: dict[str, WorkflowGateway] = {g.id: g for g in self.gateways}

    def get_node(self, node_id: str) -> WorkflowNode | None:
        if not hasattr(self, '_nodes_by_id'):
            self._rebuild_caches()
        return self._nodes_by_id.get(node_id)

    def get_gateway(self, gateway_id: str) -> WorkflowGateway | None:
        if not hasattr(self, '_gateways_by_id'):
            self._rebuild_caches()
        return self._gateways_by_id.get(gateway_id)

    def get_next_node_id(self, current_node_id: str) -> str | None:
        """按 edges 获取当前节点的下一个节点 ID（串行模式）。"""
        for e in self.edges:
            if e.source == current_node_id:
                return e.target
        return None

    def auto_pair_gateways(self) -> list[str]:
        """自动为每个 ParallelGateway 推导配对的 ConvergeGateway。

        从并行网关的每条出边出发做 BFS，找到所有出边路径共同到达的
        第一个多入边单出边的汇聚网关，填入 converge_gateway_id。

        Returns:
            错误信息列表，为空表示成功
        """
        errors: list[str] = []
        if not self.gateways:
            return errors

        # 构建邻接表（包含网关和普通节点）
        adj: dict[str, list[str]] = {}
        for e in self.edges:
            adj.setdefault(e.source, []).append(e.target)

        parallel_gws = [g for g in self.gateways if g.gateway_type == "parallel"]
        converge_gws = {g.id: g for g in self.gateways if g.gateway_type == "converge"}

        for pg in parallel_gws:
            out_targets = adj.get(pg.id, [])
            if len(out_targets) < 2:
                continue

            # 收集每条出边分支能达到的所有节点
            # 注意：每个分支的可达集必须包含并行网关自身，
            # 以处理汇聚网关与并行网关直接相连的拓扑。
            branch_reachable: list[set[str]] = []
            for target in out_targets:
                reachable: set[str] = {pg.id}
                stack = [target]
                visited: set[str] = set()
                while stack:
                    cur = stack.pop()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    reachable.add(cur)
                    for next_id in adj.get(cur, []):
                        stack.append(next_id)
                branch_reachable.append(reachable)

            # 找到所有分支都能到达的汇聚网关
            pg.converge_gateway_id = None
            for cid, cg in converge_gws.items():
                if cid == pg.id:
                    continue
                if all(cid in reachable for reachable in branch_reachable):
                    pg.converge_gateway_id = cid
                    break

            if pg.converge_gateway_id is None:
                errors.append(f"并行网关 {pg.label or pg.id} 缺少对应的汇聚网关")

        return errors

    def get_execution_plan(self) -> list[dict]:
        """生成带并行/条件标记的执行计划。

        对于无网关的简单工作流，返回纯串行计划（向后兼容）。
        对于有网关的工作流，返回混合调度计划。

        新增 condition_gateway 步骤，并自动检测回环（循环结构）。
        """
        if not self.edges:
            return [{"type": "node", "node_id": n.id} for n in self.nodes if not (n.id.startswith("__") and n.id.endswith("__"))]

        # 构建邻接表
        adj: dict[str, list[str]] = {}
        for e in self.edges:
            adj.setdefault(e.source, []).append(e.target)

        # 找到起点
        all_sources = set(adj.keys())
        all_targets = {t for targets in adj.values() for t in targets}
        start_candidates = all_sources - all_targets
        start_id = next(iter(start_candidates)) if start_candidates else "__start__"
        if start_id == "__start__" and "__start__" not in adj:
            return [{"type": "node", "node_id": n.id} for n in self.nodes if not (n.id.startswith("__") and n.id.endswith("__"))]

        # 如果没有网关，回退到串行
        if not self.gateways:
            plan: list[dict] = []
            visited: set[str] = set()
            current = start_id
            while current and current not in visited:
                visited.add(current)
                node_def = self.get_node(current)
                if node_def:
                    plan.append({"type": "node", "node_id": current})
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes else None
            return plan

        # 有网关：遍历生成混合计划
        parallel_gw_map = {g.id: g for g in self.gateways if g.gateway_type == "parallel"}
        converge_gw_map = {g.id: g for g in self.gateways if g.gateway_type == "converge"}
        condition_gw_map = {g.id: g for g in self.gateways if g.gateway_type == "condition"}
        loop_gw_map = {g.id: g for g in self.gateways if g.gateway_type == "loop"}

        plan: list[dict] = []
        visited: set[str] = set()
        visited_order: list[str] = []  # 维护访问顺序（用于回环检测）
        current = start_id

        while current and current not in visited:
            visited.add(current)
            visited_order.append(current)

            if current in parallel_gw_map:
                pg = parallel_gw_map[current]
                cid = pg.converge_gateway_id
                plan.append({
                    "type": "parallel_gateway",
                    "gateway_id": current,
                    "converge_gateway_id": cid or "",
                })

                out_targets = adj.get(current, [])
                for bi, target in enumerate(out_targets):
                    branch_nodes: list[str] = []
                    bc = target
                    branch_visited: set[str] = set()
                    while bc and bc not in branch_visited and bc != cid:
                        branch_visited.add(bc)
                        if bc in converge_gw_map:
                            break
                        node_def = self.get_node(bc)
                        if node_def:
                            branch_nodes.append(bc)
                        next_nodes = adj.get(bc, [])
                        bc = next_nodes[0] if next_nodes else None
                    plan.append({
                        "type": "branch",
                        "nodes": branch_nodes,
                        "branch_index": bi,
                    })
                    for bn in branch_nodes:
                        visited.add(bn)

                if cid:
                    plan.append({
                        "type": "converge_gateway",
                        "gateway_id": cid,
                        "parallel_gateway_id": current,
                    })
                    visited.add(cid)
                    visited_order.append(cid)
                    next_after = adj.get(cid, [])
                    current = next_after[0] if next_after else None
                else:
                    current = None

            elif current in condition_gw_map:
                # 条件网关：检查是否存在回环
                out_targets = adj.get(current, [])
                branches: list[dict] = []
                loop_detected = False
                loop_body_nodes: list[str] = []
                loop_continue_branch: dict | None = None
                loop_exit_branch: dict | None = None

                for target in out_targets:
                    edge = next((e for e in self.edges if e.source == current and e.target == target), None)
                    branch_info = {
                        "target": target,
                        "condition": edge.condition if edge and edge.condition else None,
                    }
                    branches.append(branch_info)

                    # 检测回环：出边指向已访问过的节点
                    if target in visited and target != current:
                        loop_detected = True
                        loop_continue_branch = branch_info
                        # loop_body: 从 target 到 current 这段的节点（不含 current 即条件网关本身）
                        try:
                            start_idx = visited_order.index(target)
                            end_idx = visited_order.index(current)
                            gateway_ids = {g.id for g in self.gateways}
                            loop_body_nodes = [
                                nid for nid in visited_order[start_idx:end_idx]
                                if self.get_node(nid) and nid not in gateway_ids
                            ]
                        except ValueError:
                            loop_body_nodes = []

                # 找到退出分支（非回环的出边）
                if loop_detected:
                    for bi in branches:
                        if bi["target"] != (loop_continue_branch["target"] if loop_continue_branch else ""):
                            loop_exit_branch = bi
                            break

                step: dict = {
                    "type": "condition_gateway",
                    "gateway_id": current,
                    "branches": branches,
                }
                if loop_detected and loop_body_nodes:
                    step["loop"] = True
                    step["loop_body_nodes"] = loop_body_nodes
                    step["continue_branch"] = loop_continue_branch
                    step["exit_branch"] = loop_exit_branch
                plan.append(step)

                # 没检测到循环时，计算汇合点（供引擎跳过非选中分支使用）
                if not loop_detected:
                    branch_targets = [bi["target"] for bi in branches]
                    convergence = self._find_convergence_node(self.edges, branch_targets)
                    step["convergence_node_id"] = convergence or ""
                    # 仍沿第一条分支继续构建 plan，非选中分支的节点通过 skipped 标记跳过
                    current = out_targets[0] if out_targets else None
                else:
                    # 循环场景：引擎自行处理调度，这里标记循环结束后的出口
                    current = loop_exit_branch["target"] if loop_exit_branch else None

            elif current in loop_gw_map:
                # 循环网关：出边正好 2 条，is_default=true 是 exit，另一条是 continue
                lg = loop_gw_map[current]
                out_targets = adj.get(current, [])
                continue_target: str | None = None
                exit_target: str | None = None

                for target in out_targets:
                    edge = next((e for e in self.edges if e.source == current and e.target == target), None)
                    is_default = edge.condition and edge.condition.get("is_default") if edge else False
                    if is_default:
                        exit_target = target
                    else:
                        continue_target = target

                if not continue_target:
                    continue_target = out_targets[0] if out_targets else None

                # 计算 loop body：从 continue_target 出发 BFS，直到遇到当前 loop 网关自身（形成回环）
                gateway_ids_set = {g.id for g in self.gateways}
                loop_body: list[str] = []
                if continue_target:
                    body_visited: set[str] = set()
                    stack = [continue_target]
                    while stack:
                        nid = stack.pop()
                        if nid == current or nid in body_visited:
                            continue
                        body_visited.add(nid)
                        node_def = self.get_node(nid)
                        if node_def and nid not in gateway_ids_set:
                            loop_body.append(nid)
                        for next_id in adj.get(nid, []):
                            if next_id != current:
                                stack.append(next_id)

                plan.append({
                    "type": "loop_gateway",
                    "gateway_id": current,
                    "loop_body_nodes": loop_body,
                    "continue_target": continue_target,
                    "exit_target": exit_target,
                })

                # 拓扑上，循环结束后的入口是 exit 边
                current = exit_target

            elif current in converge_gw_map:
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes else None
            else:
                node_def = self.get_node(current)
                if node_def:
                    plan.append({"type": "node", "node_id": current})
                next_nodes = adj.get(current, [])
                current = next_nodes[0] if next_nodes else None

        return plan

    def get_variable_references(self) -> dict[str, list[str]]:
        """计算变量→节点引用映射。

        扫描所有节点的文本字段（label, agent_type, system_prompt_template,
        first_message, node_params）和 var_bindings，构建 {variable_key: [node_id, ...]} 映射。
        node_params 递归扫描字符串值，确保脚本 argv 等嵌套参数引用被正确检测。
        """
        def iter_text_values(value: Any):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for nested_value in value.values():
                    yield from iter_text_values(nested_value)
            elif isinstance(value, (list, tuple)):
                for nested_value in value:
                    yield from iter_text_values(nested_value)

        refs: dict[str, set[str]] = {}
        for node in self.nodes:
            texts = [node.label, node.agent_type,
                     node.system_prompt_template, node.first_message,
                     node.output_file_path, node.model_override]
            for text in texts:
                if not text:
                    continue
                for match in _PLACEHOLDER_RE.finditer(text):
                    key = match.group(1)
                    refs.setdefault(key, set()).add(node.id)
            # 扫描 node_params 中的字符串值（包括 script_argv 等嵌套参数）
            for param_value in (node.node_params or {}).values():
                for text in iter_text_values(param_value):
                    if not text:
                        continue
                    for match in _PLACEHOLDER_RE.finditer(text):
                        key = match.group(1)
                        refs.setdefault(key, set()).add(node.id)
            for binding in (node.var_bindings or {}).values():
                if isinstance(binding, dict) and binding.get("var_key"):
                    key = binding["var_key"]
                    refs.setdefault(key, set()).add(node.id)
        return {k: sorted(v) for k, v in refs.items()}

    def validate(self) -> list[str]:
        """校验工作流定义的完整性。返回错误信息列表，为空表示校验通过。

        校验规则：
        1. 如果没有任何节点，视为空工作流，允许保存
        2. 如果有节点，必须存在从 START 到 END 的完整连线
        3. 所有节点都必须在连线上（无孤立节点）
        4. 并行网关至少 2 条出边，汇聚网关至少 2 条入边、仅 1 条出边
        5. 并行网关出边不能直接连汇聚网关
        6. 每个并行网关必须有配对的汇聚网关
        7. 禁止嵌套并行
        8. 条件网关至少 2 条出边、1 条入边，必须有一条默认分支
        """
        errors: list[str] = []
        if self.http_execution_policy not in {"public", "internal_only"}:
            errors.append(
                "http_execution_policy 必须是 public 或 internal_only"
            )
        agent_nodes = [n for n in self.nodes if not (n.id.startswith("__") and n.id.endswith("__"))]

        for node_def in agent_nodes:
            node_label = node_def.label or node_def.id
            if not _is_integer_in_range(
                node_def.auto_retry_count,
                minimum=0,
                maximum=MAX_AUTO_RETRY_COUNT,
            ):
                errors.append(
                    f"节点 '{node_label}' 的自动重试次数必须是 "
                    f"0 到 {MAX_AUTO_RETRY_COUNT} 的整数"
                )
            if not _is_integer_in_range(
                node_def.auto_retry_interval_seconds,
                minimum=0,
                maximum=MAX_AUTO_RETRY_INTERVAL_SECONDS,
            ):
                errors.append(
                    f"节点 '{node_label}' 的自动重试间隔必须是 "
                    f"0 到 {MAX_AUTO_RETRY_INTERVAL_SECONDS} 秒的整数"
                )

        # 空工作流允许保存
        if not agent_nodes:
            return errors

        # 子流程节点必须指定目标流程（在连线校验前执行）
        for node_def in self.nodes:
            if node_def.node_type == "subprocess" and not node_def.sub_workflow_id:
                errors.append(f"子流程节点 '{node_def.label or node_def.id}' 未指定目标流程，请打开节点配置选择要嵌套的流程模板")
        if errors:
            return errors

        if not self.edges:
            errors.append("工作流中存在节点，但没有任何连线")
            return errors

        # 收集上下游关系
        sources = {e.source for e in self.edges}
        targets = {e.target for e in self.edges}
        all_in_edges = sources | targets
        agent_ids = {n.id for n in agent_nodes}

        # 构建邻接表
        adj: dict[str, list[str]] = {}
        rev_adj: dict[str, list[str]] = {}  # 反向邻接
        for e in self.edges:
            adj.setdefault(e.source, []).append(e.target)
            rev_adj.setdefault(e.target, []).append(e.source)

        # --- 网关校验 ---
        parallel_gws = [g for g in self.gateways if g.gateway_type == "parallel"]
        converge_gws = [g for g in self.gateways if g.gateway_type == "converge"]

        # 并行网关：至少 2 条出边
        for pg in parallel_gws:
            out_count = len(adj.get(pg.id, []))
            if out_count < 2:
                errors.append(f"并行网关 {pg.label or pg.id} 出边少于 2 条，无法创建并行分支")
            else:
                # 出边不能直接连汇聚网关
                for target in adj.get(pg.id, []):
                    if target in {c.id for c in converge_gws}:
                        errors.append(f"并行网关 {pg.label or pg.id} 的出边直接连到了汇聚网关，两者之间必须有至少一个可执行节点")

        # 汇聚网关：至少 2 条入边，恰好 1 条出边
        for cg in converge_gws:
            in_count = len(rev_adj.get(cg.id, []))
            out_count = len(adj.get(cg.id, []))
            if in_count < 2:
                errors.append(f"汇聚网关 {cg.label or cg.id} 入边少于 2 条")
            if out_count != 1:
                errors.append(f"汇聚网关 {cg.label or cg.id} 出边必须恰好 1 条，当前为 {out_count}")

        # 嵌套并行检查：在一个并行网关→汇聚网关区块内，不能再有并行网关
        if parallel_gws and converge_gws:
            converge_ids = {c.id for c in converge_gws}
            for pg in parallel_gws:
                cid = pg.converge_gateway_id
                if not cid:
                    continue
                # 遍历每条出边分支中的节点
                for target in adj.get(pg.id, []):
                    stack = [target]
                    branch_visited: set[str] = set()
                    while stack:
                        cur = stack.pop()
                        if cur in branch_visited or cur == cid or cur in converge_ids:
                            continue
                        branch_visited.add(cur)
                        if cur in {p.id for p in parallel_gws}:
                            errors.append(f"并行网关 {pg.label or pg.id} 的分支内嵌套了另一个并行网关，当前不支持嵌套并行")
                            break
                        for next_id in adj.get(cur, []):
                            stack.append(next_id)

        # --- 条件网关校验 ---
        condition_gws = [g for g in self.gateways if g.gateway_type == "condition"]
        for cg in condition_gws:
            out_edges = [e for e in self.edges if e.source == cg.id]
            in_count = len(rev_adj.get(cg.id, []))
            if in_count < 1:
                errors.append(f"条件网关 {cg.label or cg.id} 入边少于 1 条")
            out_count = len(adj.get(cg.id, []))
            if out_count < 2:
                errors.append(f"条件网关 {cg.label or cg.id} 出边少于 2 条（需要至少一个条件分支 + 一个默认分支）")
            has_default = any(e.condition and e.condition.get("is_default") for e in out_edges)
            if not has_default:
                errors.append(f"条件网关 {cg.label or cg.id} 缺少默认分支（必须有一条出边设为默认）")
            # 检查条件表达式基本格式
            for e in out_edges:
                if e.condition and not e.condition.get("is_default"):
                    expr = e.condition.get("expression", "")
                    if not expr.strip():
                        errors.append(f"条件网关 {cg.label or cg.id} 的分支 '{e.condition.get('label', '')}' 表达式为空")

        # --- 循环网关校验 ---
        loop_gws = [g for g in self.gateways if g.gateway_type == "loop"]
        for lg in loop_gws:
            out_edges = [e for e in self.edges if e.source == lg.id]
            in_count = len(rev_adj.get(lg.id, []))
            if in_count < 1:
                errors.append(f"循环网关 {lg.label or lg.id} 入边少于 1 条")
            out_count = len(adj.get(lg.id, []))
            if out_count != 2:
                errors.append(f"循环网关 {lg.label or lg.id} 出边必须恰好 2 条（一条循环体，一条退出），当前为 {out_count}")
            has_default = any(e.condition and e.condition.get("is_default") for e in out_edges)
            if not has_default:
                errors.append(f"循环网关 {lg.label or lg.id} 缺少退出分支（必须有一条出边设为默认）")
            # 禁止嵌套循环
            all_loop_ids = {g.id for g in loop_gws}
            loop_body_set = self._compute_loop_body_node_ids(lg.id, adj)
            for body_nid in loop_body_set:
                if body_nid in all_loop_ids:
                    errors.append(f"循环网关 {lg.label or lg.id} 的循环体内包含另一个循环网关 {body_nid}，不支持嵌套循环")

        # --- 网关 ID 不能与节点 ID 重叠 ---
        gateway_ids = {g.id for g in self.gateways}
        overlap = agent_ids & gateway_ids
        if overlap:
            errors.append(f"网关 ID 与节点 ID 冲突: {', '.join(sorted(overlap))}，网关不能同时存在于 nodes 和 gateways 数组中")

        # --- 普通节点多出边检测（无网关时并行结构无效） ---
        gateway_ids_set = {g.id for g in self.gateways}
        for node_id in agent_ids:
            # 跳过网关节点
            if node_id in gateway_ids_set:
                continue
            out_targets = adj.get(node_id, [])
            if len(out_targets) > 1:
                node_def = self.get_node(node_id)
                node_label = node_def.label if node_def else node_id
                errors.append(
                    f"节点 {node_label} 有 {len(out_targets)} 条出边，但未使用并行网关。"
                    f"请从左侧「流程控制」拖入并行网关和汇聚网关来创建并行结构"
                )

        # --- 原有点边校验 ---
        # 检查孤立节点：每个 agent 节点必须在 edges 或 gateways 中出现
        edge_node_ids = all_in_edges | gateway_ids
        orphaned = agent_ids - edge_node_ids
        if orphaned:
            errors.append(f"存在未连线的节点: {', '.join(sorted(orphaned))}")

        # 检查 START 是否连接了后续节点
        start_has_out = any(e.source == "__start__" for e in self.edges)
        if not start_has_out:
            errors.append("START 节点未连接到任何 Agent 节点")

        # 检查是否有节点连接到 END
        end_has_in = any(e.target == "__end__" for e in self.edges)
        if not end_has_in:
            errors.append("没有节点连接到 END 节点")

        # 检查从 __start__ 到 __end__ 的完整路径
        if start_has_out and end_has_in:
            visited_start: set[str] = set()
            stack = ["__start__"]
            while stack:
                cur = stack.pop()
                if cur in visited_start:
                    continue
                visited_start.add(cur)
                for next_id in adj.get(cur, []):
                    stack.append(next_id)
            if "__end__" not in visited_start:
                errors.append("从 START 到 END 的连接不完整（存在断链）")

            # 反向检查：出现在连线中的每个 agent 节点应该都能从 START 到达
            for nid in agent_ids & edge_node_ids:
                if nid not in visited_start:
                    errors.append(f"节点 {nid} 不在从 START 出发的路径上")

        return errors

    def _compute_loop_body_node_ids(self, gateway_id: str,
                                     adj: dict[str, list[str]]) -> set[str]:
        """计算循环网关的循环体内所有非网关节点 ID 集合（供 validate 和 engine 使用）。"""
        out_targets = adj.get(gateway_id, [])
        continue_target: str | None = None
        for target in out_targets:
            edge = next((e for e in self.edges if e.source == gateway_id and e.target == target), None)
            is_default = edge.condition and edge.condition.get("is_default") if edge else False
            if not is_default:
                continue_target = target
                break
        if not continue_target:
            return set()

        gateway_ids_set = {g.id for g in self.gateways}
        body_set: set[str] = set()
        stack = [continue_target]
        visited: set[str] = set()
        while stack:
            nid = stack.pop()
            if nid == gateway_id or nid in visited:
                continue
            visited.add(nid)
            node_def = self.get_node(nid)
            if node_def and nid not in gateway_ids_set:
                body_set.add(nid)
            for next_id in adj.get(nid, []):
                if next_id != gateway_id:
                    stack.append(next_id)
        return body_set
