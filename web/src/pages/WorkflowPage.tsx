/**
 * WorkflowPage - 工作流顶层页面（双页签架构）
 *
 * 页签：
 *   模板    - 工作流模板卡片列表 → 查看/编辑/启动任务
 *   任务历史 - 全局跨模板任务历史 → 点击进入执行详情
 *
 * 子视图（仅在模板页签选中具体工作流时生效）：
 *   view   - 只读画布预览
 *   editor - 画布编辑
 *   param-fill - 填参启动
 *   task   - 任务执行视图（两个页签均可进入）
 */
import { lazy, useState, useEffect, useCallback, useRef } from "react";
import { RotateCcw, ArrowLeft, ArrowRight } from "lucide-react";
import {
  WorkflowConfirmDialog,
  WorkflowTabBar,
  WorkflowTemplatePanel,
  type WorkflowConfirmState,
  type WorkflowPageTab,
} from "../components/workflow/WorkflowPageParts";
import { useWebSocket } from "../hooks/useWebSocket";
import { useNodeStreaming } from "../hooks/useNodeStreaming";
import type {
  WorkflowSummary, NodeMessageResponse,
  WfNodeMessageEvent, WfNodeStatusEvent, WfTaskUpdateEvent,
  WfApprovalRequiredEvent, ApprovalFileInfo,
  WorkflowNodeDef,
  ExecutionScheme,
  NodeExecutionInfo,
} from "../types";
import { getTask } from "../lib/api";
import type { WorkflowTask } from "../types";

const WorkflowCanvas = lazy(() => import("../components/workflow/WorkflowCanvas"));
const WorkflowToolbar = lazy(() => import("../components/workflow/WorkflowToolbar"));
const TaskHistoryPanel = lazy(() => import("../components/workflow/TaskHistoryPanel"));
const ScriptLibraryPanel = lazy(() => import("../components/workflow/ScriptLibraryPanel"));
const TaskExecuteToolbar = lazy(() => import("../components/workflow/TaskExecuteToolbar"));
const TaskParamFill = lazy(() => import("../components/workflow/TaskParamFill"));
const NodeMessageDrawer = lazy(() => import("../components/workflow/NodeMessageDrawer"));
const WorkflowMainDrawer = lazy(() => import("../components/workflow/WorkflowMainDrawer"));
const ApprovalPanel = lazy(() => import("../components/workflow/ApprovalPanel"));
const ExecutionSchemeDrawer = lazy(() => import("../components/workflow/ExecutionSchemeDrawer"));

type SubView = "view" | "editor" | "node-select" | "param-fill" | "task" | null;

export default function WorkflowPage() {
  // 顶层页签
  const [tab, setTab] = useState<WorkflowPageTab>("templates");

  // 模板子视图
  const [subView, setSubView] = useState<SubView>(null);

  // 选中状态
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  // 工作流列表
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [loading, setLoading] = useState(false);

  // 当前选中工作流的名称（从列表派生）
  const selectedWorkflowName = selectedId
    ? (workflows.find(w => w.workflow_id === selectedId)?.name || "")
    : "";

  // 未保存变更追踪
  const [hasUnsaved, setHasUnsaved] = useState(false);

  // 节点选择状态（node-select 和 param-fill 之间共享）
  const [disabledNodeIds, setDisabledNodeIds] = useState<Set<string>>(new Set());
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [allNodeIds, setAllNodeIds] = useState<string[]>([]);

  // 执行方案状态
  const [schemes, setSchemes] = useState<ExecutionScheme[]>([]);
  const [activeSchemeId, setActiveSchemeId] = useState<string | null>(null);
  const [schemeModified, setSchemeModified] = useState(false);
  const [schemeCollapsed, setSchemeCollapsed] = useState(false);

  // 保存触发
  const [saveRequested, setSaveRequested] = useState(0);
  const [saving, setSaving] = useState(false);

  // 错误通知
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // 确认对话框
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const [confirmDialog, setConfirmDialog] = useState<WorkflowConfirmState | null>(null);

  // 任务历史刷新触发
  const [historyRefresh, setHistoryRefresh] = useState(0);

  // 节点消息抽屉
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerNodeId, setDrawerNodeId] = useState<string | null>(null);
  const [drawerMessages, setDrawerMessages] = useState<NodeMessageResponse | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerSessionId, setDrawerSessionId] = useState<string | null>(null);
  // 选中节点类型（用于 drawer 区分 agent/script 渲染）— 必须在 hook 前声明
  const [drawerNodeType, setDrawerNodeType] = useState<string>("");

  // 节点流式消息 hook（连接 /ws/chat 获取实时 token）
  const nodeStreaming = useNodeStreaming({
    sessionId: drawerSessionId,
    autoConnect: drawerOpen && drawerNodeType === "agent",
  });

  // Workflow Main 抽屉
  const [mainDrawerOpen, setMainDrawerOpen] = useState(false);
  const [mainSessionId, setMainSessionId] = useState<string | null>(null);

  // 重做任务上下文
  const [reuseTaskId, setReuseTaskId] = useState<string | null>(null);
  const [reuseTaskName, setReuseTaskName] = useState<string | null>(null);
  const [reuseParameterValues, setReuseParameterValues] = useState<Record<string, string> | null>(null);
  const [reuseWorkspaceOverride, setReuseWorkspaceOverride] = useState<string | null>(null);

  // 节点状态数据（来自 WS wf_task_update）
  const [liveTaskStatus, setLiveTaskStatus] = useState<string>("");
  const [liveNodeStates, setLiveNodeStates] = useState<Record<string, NodeExecutionInfo>>({});
  const [liveTaskCompletedAt, setLiveTaskCompletedAt] = useState<string | null>(null);
  // 审批面板
  const [approvalData, setApprovalData] = useState<{
    workflowId: string; taskId: string; nodeId: string; nodeLabel: string;
    files: ApprovalFileInfo[]; placeholder: string;
  } | null>(null);

  const isExecutionView = subView === "task" || (!!selectedTaskId);

  // WebSocket（仅在执行视图连接）
  useWebSocket({
    url: "/ws/events",
    autoConnect: isExecutionView,
    onMessage: useCallback((raw: unknown) => {
      const event = raw as WfNodeMessageEvent | WfNodeStatusEvent | WfTaskUpdateEvent | WfApprovalRequiredEvent | { type: string };
      if (event.type === "wf_node_message") {
        // 消息实时更新已由 useNodeStreaming hook 通过 /ws/chat 处理
        // 此处不再需要手动追加到 drawerMessages
      } else if (event.type === "wf_node_status") {
        const statusEvt = event as WfNodeStatusEvent;
        const nextStatus: NodeExecutionInfo["status"] = statusEvt.status === "success"
          ? "completed"
          : statusEvt.status === "failure" ? "failed" : statusEvt.status;
        if (drawerOpen && drawerNodeId === statusEvt.node_id) {
          setDrawerMessages((prev) => {
            if (!prev) return prev;
            return { ...prev, node_status: nextStatus, summary: statusEvt.summary, error: statusEvt.error };
          });
        }
        setLiveNodeStates((prev) => {
          const previous = prev[statusEvt.node_id];
          return {
            ...prev,
            [statusEvt.node_id]: {
              ...previous,
              node_id: statusEvt.node_id,
              status: nextStatus,
              summary: statusEvt.summary || previous?.summary || "",
              session_id: statusEvt.session_id || previous?.session_id || "",
              error: statusEvt.error || previous?.error,
              parent_node_id: statusEvt.parent_node_id || previous?.parent_node_id,
            },
          };
        });
      } else if (event.type === "wf_task_update") {
        const taskEvt = event as WfTaskUpdateEvent;
        setLiveTaskStatus(taskEvt.status);
        setLiveTaskCompletedAt(taskEvt.completed_at);
        setLiveNodeStates(taskEvt.node_states || {});
      } else if (event.type === "wf_approval_required") {
        const approvalEvt = event as WfApprovalRequiredEvent;
        setApprovalData({
          workflowId: approvalEvt.workflow_id,
          taskId: approvalEvt.task_id,
          nodeId: approvalEvt.node_id,
          nodeLabel: approvalEvt.node_label,
          files: approvalEvt.files,
          placeholder: approvalEvt.placeholder,
        });
      }
    }, [drawerNodeId, drawerOpen]),
    reconnectInterval: 5000,
  });

  // ============ 工作流列表加载 ============

  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/workflows");
      if (res.ok) setWorkflows(await res.json());
    } catch (e) {
      console.error("加载工作流列表失败:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "templates" && subView === null) fetchWorkflows();
  }, [tab, subView, fetchWorkflows]);

  // 进入任务视图时加载任务状态（包括 node_states 和 main_session_id）
  useEffect(() => {
    if (!selectedId || !selectedTaskId || subView !== "task") return;
    let cancelled = false;
    fetch(`/api/workflows/${selectedId}/tasks/${selectedTaskId}`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        const task = data.task;
        setMainSessionId(task?.main_session_id || null);
        // 从任务数据填充 node_states（包括已完成/已失败的任务）
        if (task?.node_states) {
          setLiveNodeStates(task.node_states);
        }
        if (task?.status) setLiveTaskStatus(task.status);
        if (task?.completed_at) setLiveTaskCompletedAt(task.completed_at);
      })
      .catch(() => {
        if (!cancelled) setMainSessionId(null);
      });
    return () => { cancelled = true; };
  }, [selectedId, selectedTaskId, subView]);

  // 确认对话框自动聚焦确认按钮
  useEffect(() => {
    if (confirmDialog) {
      setTimeout(() => confirmBtnRef.current?.focus(), 50);
    }
  }, [confirmDialog]);

  // beforeunload 拦截
  useEffect(() => {
    if (subView !== "editor") return;
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsaved) { e.preventDefault(); e.returnValue = ""; }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [subView, hasUnsaved]);

  // ============ 导航处理 ============

  const handleCreate = async () => {
    try {
      const res = await fetch("/api/workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "新工作流", nodes: [], edges: [] }),
      });
      if (res.ok) {
        const data = await res.json();
        setSelectedId(data.definition.workflow_id);
        setSubView("editor");
      }
    } catch (e) {
      console.error("创建工作流失败:", e);
    }
  };

  const handleView = (id: string) => { setSelectedId(id); setHasUnsaved(false); setSubView("view"); };
  const handleEdit = (id?: string) => { if (id) setSelectedId(id); setSubView("editor"); };

  const handleBack = () => {
    if (subView === "editor" && hasUnsaved) {
      setConfirmDialog({
        message: "您有未保存的更改，确定要离开吗？",
        onConfirm: () => {
          setSelectedId(null);
          setSubView(null);
          setHasUnsaved(false);
          setConfirmDialog(null);
        },
      });
      return;
    }
    setSelectedId(null);
    setSubView(null);
    setHasUnsaved(false);
  };

  const handleSaveRequest = () => { setSaving(true); setSaveRequested((p) => p + 1); };
  const handleSaveComplete = () => { setSaving(false); setSubView("view"); };
  const handleSaveError = () => { setSaving(false); };

  const handleRename = useCallback(async (newName: string) => {
    if (!selectedId || !newName.trim()) return;
    const trimmed = newName.trim();
    const oldName = workflows.find(w => w.workflow_id === selectedId)?.name || "";

    // 乐观更新
    setWorkflows(prev => prev.map(w =>
      w.workflow_id === selectedId ? { ...w, name: trimmed } : w
    ));

    try {
      const res = await fetch(`/api/workflows/${selectedId}`);
      if (!res.ok) throw new Error("获取工作流定义失败");
      const data = await res.json();
      const def = data.definition;

      const putRes = await fetch(`/api/workflows/${selectedId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: trimmed,
          nodes: def.nodes || [],
          edges: def.edges || [],
          variables: def.variables || [],
          gateways: def.gateways || [],
          execution_schemes: def.execution_schemes || [],
          start_position: def.start_position || { x: 300, y: 50 },
          end_position: def.end_position || { x: 300, y: 550 },
        }),
      });
      if (!putRes.ok) throw new Error("保存名称失败");
    } catch (e: unknown) {
      setErrorMessage("重命名失败: " + (e instanceof Error ? e.message : "未知错误"));
      setTimeout(() => setErrorMessage(null), 5000);
      // 回滚
      setWorkflows(prev => prev.map(w =>
        w.workflow_id === selectedId ? { ...w, name: oldName } : w
      ));
    }
  }, [selectedId, workflows]);

  const handleStartTaskFill = () => {
    // 清除重做上下文（普通启动）
    setReuseTaskId(null);
    setReuseTaskName(null);
    setReuseParameterValues(null);
    setReuseWorkspaceOverride(null);
    // 先进入节点选择页
    // 加载所有业务节点 ID 和执行方案
    fetch(`/api/workflows/${selectedId}`)
      .then(r => r.json())
      .then(data => {
        const def = data.definition;
        const businessNodeIds = (def.nodes || [])
          .filter((n: WorkflowNodeDef) => n.id !== "__start__" && n.id !== "__end__")
          .map((n: WorkflowNodeDef) => n.id);
        setAllNodeIds(businessNodeIds);
        setSelectedNodeIds([...businessNodeIds]); // 默认全选
        setDisabledNodeIds(new Set()); // 默认无禁用
        setSchemes(def.execution_schemes || []);
        setActiveSchemeId(null);
        setSchemeModified(false);
        setSubView("node-select");
      })
      .catch(() => setSubView("node-select"));
  };

  /** 重做任务：使用原任务参数进入节点选择页 */
  const handleRedoTask = async (taskId: string, workflowId: string) => {
    try {
      // 1. 获取原任务数据
      const taskData = await getTask(workflowId, taskId);
      const task = taskData.task;
      const snapshotDef = taskData.definition;

      // 2. 获取当前工作流定义（用于版本对比）
      const defRes = await fetch(`/api/workflows/${workflowId}`);
      if (!defRes.ok) throw new Error("获取工作流定义失败");
      const defData = await defRes.json();
      const currentDef = defData.definition;

      // 3. 版本变化检测
      const snapshotNodeIds = (snapshotDef?.nodes || []).map((n: { id: string }) => n.id);
      const currentNodeIds = (currentDef?.nodes || []).map((n: { id: string }) => n.id);
      const deletedNodes = snapshotNodeIds.filter((id: string) => !currentNodeIds.includes(id));
      const addedNodes = currentNodeIds.filter((id: string) => !snapshotNodeIds.includes(id));

      // 4. 有变更时弹出确认对话框
      if (deletedNodes.length > 0 || addedNodes.length > 0) {
        const messages: string[] = [];
        if (deletedNodes.length > 0) {
          messages.push(`已删除 ${deletedNodes.length} 个节点: ${deletedNodes.join(", ")}`);
        }
        if (addedNodes.length > 0) {
          messages.push(`新增 ${addedNodes.length} 个节点: ${addedNodes.join(", ")}`);
        }
        setConfirmDialog({
          message: `工作流定义已发生变化：\n${messages.join("\n")}\n\n是否继续重做？`,
          onConfirm: () => {
            setConfirmDialog(null);
            doRedo(workflowId, task, currentDef);
          },
        });
        return;
      }

      // 无变更，直接执行
      doRedo(workflowId, task, currentDef);
    } catch (e) {
      console.error("重做任务失败:", e);
      setErrorMessage("获取原任务数据失败，无法重做");
      setTimeout(() => setErrorMessage(null), 5000);
    }
  };

  /** 执行重做：设置状态并进入节点选择 */
  const doRedo = (workflowId: string, task: WorkflowTask, currentDef: Record<string, unknown>) => {
    setTab("templates"); // 确保在模板页签下显示
    setSelectedId(workflowId);

    // 提取业务节点
    const businessNodeIds = ((currentDef.nodes || []) as { id: string }[])
      .filter((n) => n.id !== "__start__" && n.id !== "__end__")
      .map((n) => n.id);
    setAllNodeIds(businessNodeIds);

    // 从原任务恢复节点选择
    const disabledSet = new Set(task.disabled_node_ids || []);
    setDisabledNodeIds(disabledSet);
    setSelectedNodeIds(businessNodeIds.filter((id) => !disabledSet.has(id)));

    // 恢复执行方案
    const schemes = (currentDef.execution_schemes as ExecutionScheme[]) || [];
    setSchemes(schemes);
    if (task.scheme_id && schemes.some((s) => s.id === task.scheme_id)) {
      setActiveSchemeId(task.scheme_id);
      setSchemeModified(false);
    } else {
      setActiveSchemeId(null);
      setSchemeModified(false);
    }

    // 保存重做上下文供填参页使用
    setReuseTaskId(task.task_id);
    setReuseTaskName(task.name || task.task_id);
    setReuseParameterValues(task.parameter_values || null);
    setReuseWorkspaceOverride(task.workspace_override || null);

    setSubView("node-select");
  };

  /** 节点选择页：勾选/取消节点 */
  const handleNodeToggle = useCallback((nodeId: string, checked: boolean) => {
    setDisabledNodeIds(prev => {
      const next = new Set(prev);
      if (checked) next.delete(nodeId);
      else next.add(nodeId);
      // 同步更新 selectedNodeIds
      const allBizNodes = allNodeIds.length > 0 ? allNodeIds : Array.from(next);
      const selected = allBizNodes.filter(id => !next.has(id));
      setSelectedNodeIds(selected);
      // 如果之前选了方案，现在手动改动 → 标记为已修改
      if (activeSchemeId) {
        setSchemeModified(true);
      }
      return next;
    });
  }, [allNodeIds, activeSchemeId]);

  /** 节点选择页：确认并进入参数填写 */
  const handleConfirmNodeSelect = () => {
    setSubView("param-fill");
  };

  /** 节点选择页：批量全选 */
  const handleSelectAll = useCallback(() => {
    setDisabledNodeIds(new Set());
    setSelectedNodeIds([...allNodeIds]);
    if (activeSchemeId) setSchemeModified(true);
  }, [allNodeIds, activeSchemeId]);

  /** 节点选择页：批量取消全选 */
  const handleDeselectAll = useCallback(() => {
    const allSet = new Set(allNodeIds);
    setDisabledNodeIds(allSet);
    setSelectedNodeIds([]);
    if (activeSchemeId) setSchemeModified(true);
  }, [allNodeIds, activeSchemeId]);

  /** 应用执行方案：将画布选择设置为方案中的节点 */
  const handleApplyScheme = useCallback((scheme: ExecutionScheme) => {
    const schemeSet = new Set(scheme.selected_node_ids);
    const disabled = new Set(allNodeIds.filter(id => !schemeSet.has(id)));
    setDisabledNodeIds(disabled);
    setSelectedNodeIds(scheme.selected_node_ids);
    setActiveSchemeId(scheme.id);
    setSchemeModified(false);
  }, [allNodeIds]);

  /** 当方案列表变更时刷新 */
  const handleSchemesChange = useCallback((newSchemes: ExecutionScheme[]) => {
    setSchemes(newSchemes);
  }, []);

  /** 参数填写页：返回节点选择 */
  const handleBackToNodeSelect = () => {
    setSubView("node-select");
  };

  const handleTaskStarted = (taskId: string, openMainDrawer?: boolean) => {
    setSelectedTaskId(taskId);
    setSubView("task");
    if (openMainDrawer) setMainDrawerOpen(true);
    // 清除重做上下文（任务已创建）
    setReuseTaskId(null);
    setReuseTaskName(null);
    setReuseParameterValues(null);
    setReuseWorkspaceOverride(null);
  };

  // 从任务历史页签点击任务 → 进入执行视图
  const handleHistoryTaskClick = (taskId: string, workflowId: string) => {
    setSelectedId(workflowId);
    setSelectedTaskId(taskId);
    setSubView("task");
  };

  // 从执行视图返回 → 历史页签
  const handleBackFromTask = () => {
    setSelectedTaskId(null);
    setSubView(null);
    setDrawerOpen(false);
    setDrawerNodeId(null);
    setDrawerMessages(null);
    setDrawerSessionId(null);
    setDrawerNodeType("");
    nodeStreaming.clearMessages();
    setMainDrawerOpen(false);
    setMainSessionId(null);
    setHistoryRefresh((p) => p + 1);
    setTab("history");
  };

  // 切换页签
  const handleTabChange = (t: WorkflowPageTab) => {
    setTab(t);
    // 切到任务历史或脚本库时清除模板子视图
    if (t === "history" || t === "scripts") {
      setSubView(null);
      setSelectedId(null);
      setSelectedTaskId(null);
    }
    // 切到模板时清除任务历史子视图
    if (t === "templates") {
      setSelectedTaskId(null);
      if (subView !== "task" && subView !== "param-fill" && subView !== "node-select") {
        setSubView(null);
      }
    }
  };

  const handleNodeClick = async (nodeId: string, sessionId: string, nodeType?: string, nodeLabel?: string) => {
    if (!selectedId || !selectedTaskId) return;
    setMainDrawerOpen(false);

    // 审批节点 → 打开审批面板（不管 approvalData 是否存在）
    if (nodeType === "approval") {
      setDrawerOpen(false); setDrawerNodeId(null);
      setDrawerNodeType("");
      setDrawerSessionId(null);
      if (approvalData) {
        // 已有 WS 数据，直接展示；展开一个副本触发重新渲染
        setApprovalData({ ...approvalData });
      } else {
        // 尚无 WS 数据，用最简信息打开空面板
        setApprovalData({
          workflowId: selectedId,
          taskId: selectedTaskId,
          nodeId,
          nodeLabel: nodeLabel || "审批",
          files: [],
          placeholder: "请输入驳回原因...",
        });
      }
      return;
    }

    // 脚本节点：跳过消息 API，直接从 liveNodeStates 获取输出数据
    if (nodeType === "script") {
      setApprovalData(null);
      setDrawerOpen(true); setDrawerNodeId(nodeId);
      setDrawerNodeType("script");
      setDrawerMessages(null); setDrawerLoading(false);
      setDrawerSessionId(null);
      return;
    }

    // Agent 节点：关闭审批面板，打开消息抽屉
    setApprovalData(null);
    setDrawerOpen(true); setDrawerNodeId(nodeId);
    setDrawerNodeType("agent");

    // 优先从 liveNodeStates 获取 session_id（WebSocket 已同步）
    const existingSessionId = liveNodeStates[nodeId]?.session_id || sessionId || null;
    if (existingSessionId) {
      setDrawerSessionId(existingSessionId);
    }

    // 如果同节点已有缓存消息，直接复用，不重新加载
    if (drawerMessages && drawerNodeId === nodeId && drawerMessages.session_id) {
      setDrawerLoading(false);
      // 仍然异步刷新一次，获取最新消息
      fetch(`/api/workflows/${selectedId}/tasks/${selectedTaskId}/nodes/${nodeId}/messages`)
        .then((res) => res.ok ? res.json() : null)
        .then((data) => {
          if (data) {
            setDrawerMessages(data);
            nodeStreaming.setBaseMessages(data.messages || []);
            if (data.session_id) setDrawerSessionId(data.session_id);
          }
        })
        .catch(() => {});
      return;
    }

    // 首次打开或切换节点：加载消息
    setDrawerLoading(true); setDrawerMessages(null);
    nodeStreaming.clearMessages();
    try {
      const res = await fetch(`/api/workflows/${selectedId}/tasks/${selectedTaskId}/nodes/${nodeId}/messages`);
      if (res.ok) {
        const data = await res.json();
        setDrawerMessages(data);
        nodeStreaming.setBaseMessages(data.messages || []);
        if (data.session_id) setDrawerSessionId(data.session_id);
      } else {
        setDrawerMessages(null);
      }
    } catch (e) { console.error("加载节点消息失败:", e); setDrawerMessages(null); }
    finally { setDrawerLoading(false); }
  };

  // Main 抽屉开关互斥：打开 Main 抽屉时关闭节点抽屉（保留缓存消息）
  const handleMainDrawerOpen = (open: boolean) => {
    if (open) {
      setDrawerOpen(false);
      setDrawerNodeId(null);
    }
    setMainDrawerOpen(open);
  };

  const handleDelete = async (id: string) => {
    setConfirmDialog({
      message: "确认删除该工作流及其所有任务记录？",
      onConfirm: async () => {
        try {
          await fetch(`/api/workflows/${id}`, { method: "DELETE" });
          fetchWorkflows();
        } catch (e) { console.error("删除工作流失败:", e); }
        setConfirmDialog(null);
      },
    });
  };

  const renderTabBar = () => <WorkflowTabBar tab={tab} onTabChange={handleTabChange} />;

  // ============ 渲染：执行视图 ============

  const renderExecutionView = () => (
    <section aria-label="工作流任务执行" className="flex-1 flex flex-col min-h-0">
      <TaskExecuteToolbar
        workflowId={selectedId!}
        taskId={selectedTaskId!}
        onBack={handleBackFromTask}
        liveStatus={liveTaskStatus}
        liveCompletedAt={liveTaskCompletedAt}
      />
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 flex flex-col min-w-0">
          <WorkflowCanvas
            workflowId={selectedId!}
            taskId={selectedTaskId!}
            onNodeClick={handleNodeClick}
            liveNodeStates={liveNodeStates}
          />
        </div>
        {/* Workflow Main 抽屉：仅在有 main_session_id 时渲染 */}
        {mainSessionId && (
          <WorkflowMainDrawer
            mode="drawer"
            workflowId={selectedId!}
            taskId={selectedTaskId!}
            mainSessionId={mainSessionId}
            isOpen={mainDrawerOpen}
            onOpenChange={handleMainDrawerOpen}
          />
        )}
        {drawerOpen && (
          <NodeMessageDrawer
            workflowId={selectedId!}
            taskId={selectedTaskId!}
            nodeId={drawerNodeId || ""}
            messages={drawerMessages}
            loading={drawerLoading}
            nodeType={drawerNodeType}
            nodeState={drawerNodeId ? liveNodeStates[drawerNodeId] : undefined}
            streamingSegments={nodeStreaming.streamingSegments}
            isStreaming={nodeStreaming.isStreaming}
            onClose={() => { setDrawerOpen(false); setDrawerNodeId(null); setDrawerNodeType(""); }}
          />
        )}
        {approvalData && (
          <ApprovalPanel
            workflowId={approvalData.workflowId}
            taskId={approvalData.taskId}
            nodeId={approvalData.nodeId}
            nodeLabel={approvalData.nodeLabel}
            files={approvalData.files}
            placeholder={approvalData.placeholder}
            nodeState={liveNodeStates[approvalData.nodeId]}
            onClose={() => setApprovalData(null)}
            onResolved={() => {
              // 审批完成后清除数据
              setApprovalData(null);
            }}
          />
        )}
      </div>
    </section>
  );

  // ============ 渲染：执行视图（带页签栏） ============

  if (isExecutionView && selectedId && selectedTaskId) {
    return (
      <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
        {renderTabBar()}
        {renderExecutionView()}
      </div>
    );
  }

  // ============ 渲染：模板页签 ============

  if (tab === "templates") {
    // 子视图：查看模式
    if (subView === "view" && selectedId) {
      return (
        <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
          {renderTabBar()}
          <section aria-label={`查看工作流: ${selectedWorkflowName || "未命名"}`} className="flex-1 flex flex-col min-h-0">
            <WorkflowToolbar workflowId={selectedId} mode="view" onBack={handleBack} onEdit={() => handleEdit()} onStartTaskFill={handleStartTaskFill} onTaskStarted={handleTaskStarted} name={selectedWorkflowName} onRename={handleRename} />
            <WorkflowCanvas workflowId={selectedId} readOnly={true} />
          </section>
        </div>
      );
    }

    // 子视图：编辑器
    if (subView === "editor" && selectedId) {
      return (
        <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
          {renderTabBar()}
          <section aria-label={`编辑工作流: ${selectedWorkflowName || "未命名"}`} className="flex-1 flex flex-col min-h-0">
            <WorkflowToolbar workflowId={selectedId} mode="editor" onBack={handleBack} onSave={handleSaveRequest} hasUnsaved={hasUnsaved} saving={saving} name={selectedWorkflowName} onRename={handleRename} />
            <WorkflowCanvas workflowId={selectedId} onDirtyChange={setHasUnsaved} saveRequested={saveRequested} onSaveComplete={handleSaveComplete} onSaveError={handleSaveError} />
          </section>
        </div>
      );
    }

    // 子视图：节点选择
    if (subView === "node-select" && selectedId) {
      return (
        <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
          {renderTabBar()}
          {/* 重做提示条 */}
          {reuseTaskId && reuseTaskName && (
            <div className="px-6 py-2 bg-indigo-500/10 border-b border-indigo-500/20 shrink-0">
              <div className="flex items-center gap-2 text-sm text-indigo-300">
                <RotateCcw size={14} aria-hidden="true" />
                <span>正在基于任务</span>
                <span className="font-medium text-indigo-200">{reuseTaskName}</span>
                <span>重做，节点选择和参数已沿用，可在此调整</span>
              </div>
            </div>
          )}
          {/* 顶部栏 */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-white/5 shrink-0">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setSubView("view");
                  // 清除重做上下文（返回到查看模式即中止重做）
                  setReuseTaskId(null);
                  setReuseTaskName(null);
                  setReuseParameterValues(null);
                  setReuseWorkspaceOverride(null);
                }}
                aria-label="返回查看模式"
                className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-100 transition-colors duration-200 cursor-pointer"
              >
                <ArrowLeft size={14} aria-hidden="true" />返回
              </button>
              <h2 className="text-sm font-medium text-slate-100">选择执行节点</h2>
              <span className="text-xs text-slate-500">
                （勾选需要执行的节点，START/END 始终执行）
              </span>
              {activeSchemeId && schemeModified && (
                <span className="text-xs text-amber-400">
                  方案已修改（未保存）
                </span>
              )}
              {activeSchemeId && !schemeModified && (
                <span className="text-xs text-indigo-400">
                  方案：{schemes.find(s => s.id === activeSchemeId)?.name || ""}
                </span>
              )}
              {/* 快捷键：全选/取消全选 */}
              <button
                type="button"
                onClick={handleSelectAll}
                aria-label="批量全选所有节点"
                className="text-xs px-2.5 py-1 rounded border border-white/10 text-slate-300 hover:bg-slate-800 hover:border-white/20 transition-colors cursor-pointer"
              >
                全选
              </button>
              <button
                type="button"
                onClick={handleDeselectAll}
                aria-label="批量取消全选所有节点"
                className="text-xs px-2.5 py-1 rounded border border-white/10 text-slate-300 hover:bg-slate-800 hover:border-white/20 transition-colors cursor-pointer"
              >
                取消全选
              </button>
            </div>
            <button
              type="button"
              onClick={handleConfirmNodeSelect}
              aria-label="确认选择并进入参数填写"
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors duration-200 cursor-pointer min-h-[44px]"
            >
              下一步：填写参数<ArrowRight size={14} aria-hidden="true" />
            </button>
          </div>
          {/* 画布 + 执行方案抽屉 */}
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 flex min-w-0">
              <WorkflowCanvas
                workflowId={selectedId}
                readOnly={true}
                selectionMode={true}
                disabledNodeIds={disabledNodeIds}
                onNodeToggle={handleNodeToggle}
              />
            </div>
            <ExecutionSchemeDrawer
              workflowId={selectedId}
              schemes={schemes}
              onSchemesChange={handleSchemesChange}
              selectedNodeIds={selectedNodeIds}
              selectedCount={selectedNodeIds.length}
              onApplyScheme={handleApplyScheme}
              activeSchemeId={schemeModified ? null : activeSchemeId}
              allNodeIds={allNodeIds}
              collapsed={schemeCollapsed}
              onToggleCollapse={() => setSchemeCollapsed(p => !p)}
            />
          </div>
        </div>
      );
    }

    // 子视图：填参
    if (subView === "param-fill" && selectedId) {
      // 确定传递给 createTask 的方案参数
      const effectiveSchemeId = (!schemeModified && activeSchemeId) ? activeSchemeId : undefined;
      const effectiveSelectedNodeIds = (schemeModified || !activeSchemeId) ? selectedNodeIds : undefined;
      return (
        <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
          {renderTabBar()}
          <TaskParamFill
            workflowId={selectedId}
            onBack={handleBackToNodeSelect}
            onTaskStarted={handleTaskStarted}
            selectedNodeIds={selectedNodeIds}
            disabledNodeIds={Array.from(disabledNodeIds)}
            schemeId={effectiveSchemeId}
            effectiveSelectedNodeIds={effectiveSelectedNodeIds}
            prefillValues={reuseParameterValues}
            prefillWorkspace={reuseWorkspaceOverride}
          />
        </div>
      );
    }

    // 默认：模板卡片列表
    return (
      <>
        <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
          {renderTabBar()}
          <WorkflowTemplatePanel
            workflows={workflows}
            loading={loading}
            errorMessage={errorMessage}
            onDismissError={() => setErrorMessage(null)}
            onCreate={handleCreate}
            onView={handleView}
            onDelete={handleDelete}
          />
        </div>
        <WorkflowConfirmDialog
          dialog={confirmDialog}
          onClose={() => setConfirmDialog(null)}
          confirmButtonRef={confirmBtnRef}
          descriptionId="confirm-msg-tpl"
        />
      </>
    );
  }

  // ============ 渲染：脚本库页签 ============

  if (tab === "scripts") {
    return (
      <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
        {renderTabBar()}
        <section id="wf-tabpanel-scripts" role="tabpanel" aria-label="脚本库管理" className="flex-1 min-h-0 flex flex-col">
          <div className="px-6 py-4 border-b border-indigo-500/10 shrink-0">
            <h2 className="text-lg font-semibold text-slate-100">脚本库</h2>
            <p className="text-sm text-slate-400 mt-0.5">
              管理可复用的 Shell/Python 脚本，供工作流脚本节点引用
            </p>
          </div>
          <ScriptLibraryPanel />
        </section>
      </div>
    );
  }

  // ============ 渲染：任务历史页签 ============

  return (
    <>
      <div className="h-[calc(100dvh-3.5rem)] flex flex-col bg-slate-950">
        {renderTabBar()}
        <section id="wf-tabpanel-history" role="tabpanel" aria-label="任务执行历史" className="flex-1 min-h-0">
          <TaskHistoryPanel
            onTaskClick={handleHistoryTaskClick}
            onRedoTask={handleRedoTask}
            refreshTrigger={historyRefresh}
          />
        </section>
      </div>

      <WorkflowConfirmDialog
        dialog={confirmDialog}
        onClose={() => setConfirmDialog(null)}
        confirmButtonRef={confirmBtnRef}
        descriptionId="confirm-msg-hist"
      />
    </>
  );
}
