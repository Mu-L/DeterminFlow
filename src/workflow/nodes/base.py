"""
节点插件基类 — 参照 bk-sops 插件设计哲学，与编排引擎解耦。

每种节点类型继承 BaseNodePlugin，通过 node_type 注册到 NodeRegistry。
引擎只通过 execute() 接口调用，不感知具体节点行为。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.workflow.definition import WorkflowDef, WorkflowNode, NodeExecutionState


# ============================================================
# 上下文与结果
# ============================================================

@dataclass
class NodeContext:
    """节点执行上下文 — 引擎传给插件的全部信息。"""
    definition: "WorkflowDef"                             # 工作流定义
    node_def: "WorkflowNode"                              # 当前节点定义
    node_state: "NodeExecutionState"                      # 当前节点运行时状态
    shared_ws: Path | None = None                         # 工作流共享 workspace 路径
    session_ws_path: str = ""                             # 工作流共享 workspace 路径（与 shared_ws 一致）
    parameter_values: dict[str, str] = field(default_factory=dict)  # 任务填参值
    upstream_summary: str = ""                            # 上游节点产出摘要
    needs_approval: bool = False                          # Agent 节点完成后是否需要 main 审批
    parent_id: str = ""                                   # workflow-main session ID
    workflow_id: str = ""                                 # 工作流 ID
    task_id: str = ""                                     # 任务 ID
    execution_order: list[str] = field(default_factory=list)  # 完整执行顺序
    node_index: int = 0                                   # 当前节点在执行顺序中的位置
    session_manager: object = field(default=None, repr=False)  # SessionManager 实例（引擎注入）
    on_reject_upstream: object = field(default=None, repr=False)  # reject_upstream 回调（引擎注入）
    checkpoint: object = field(default=None, repr=False)       # 插件内部持久化检查点回调
    owner_environment: dict[str, str] = field(default_factory=dict, repr=False)
    workflow_environment: Callable[[str], dict[str, str]] | None = field(
        default=None,
        repr=False,
    )


@dataclass
class NodeResult:
    """节点执行结果。"""
    summary: str = ""                                     # 产出摘要
    status: str = "success"                               # "success" | "failed"
    error: str = ""                                       # 失败原因
    session_id: str = ""                                  # 节点子会话 ID（agent 节点才有）
    outputs: dict[str, str] = field(default_factory=dict) # 产出变量 key→value
    stdout: str = ""                                      # 脚本节点标准输出全文
    stderr: str = ""                                      # 脚本节点标准错误全文
    token_usage: dict | None = None                       # LLM token 消耗（按 model_id 分组的累计数据）
    token_usage_calls: list[dict] = field(default_factory=list)  # 调用级 Token 账本


# ============================================================
# 基类
# ============================================================

class BaseNodePlugin:
    """节点插件基类。

    子类必须定义：
    - node_type: 唯一标识（如 "agent", "approval"）
    - label: 前端展示名
    - execute(): 执行入口

    可选覆盖：
    - params_schema: 声明式参数列表（前端动态渲染属性面板）
    - default_icon: 前端节点卡片图标
    """

    node_type: str = ""
    label: str = ""
    default_icon: str = "bot"

    @property
    def params_schema(self) -> list[dict]:
        """声明本节点类型的可配置参数。

        返回格式:
        [
            {
                "key": "file_paths",
                "label": "要展示的文件",
                "type": "textarea",
                "placeholder": "/path/to/file.md",
                "required": False,
                "default": "",
                "description": "每行一个文件路径",
            },
            ...
        ]
        前端根据此 schema 动态渲染属性配置面板。
        """
        return []

    async def execute(self, ctx: NodeContext) -> NodeResult:
        """执行节点。引擎唯一调用的入口。"""
        raise NotImplementedError(f"{self.node_type} 未实现 execute()")


# ============================================================
# 公共工具函数
# ============================================================

def push_node_status(
    workflow_id: str,
    node_id: str,
    session_id: str,
    summary: str,
    status: str,
    error: str,
    parent_node_id: str = "",
) -> None:
    """推送节点状态事件到 EventBus（fire-and-forget）。

    engine.py 和 nodes/agent.py 原各自定义 _push_node_status，
    逻辑完全相同。提取为公共函数消除重复。

    parent_node_id: 子流程内部节点时，标记所属父节点 ID。
    """
    try:
        from src.web.event_bus import event_bus
        event_data = {
            "type": "wf_node_status",
            "workflow_id": workflow_id,
            "node_id": node_id,
            "session_id": session_id,
            "status": status,
            "summary": summary,
            "error": error,
        }
        if parent_node_id:
            event_data["parent_node_id"] = parent_node_id
        asyncio.create_task(event_bus.emit_event(event_data))
    except Exception:
        logger.debug("wf_node_status 事件推送失败", exc_info=True)
