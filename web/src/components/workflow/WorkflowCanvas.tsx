/**
 * WorkflowCanvas - ReactFlow 画布组件
 *
 * 功能：
 * - 自动管理 START/END 节点（不可删除，START 仅出边，END 仅入边）
 * - 左侧分类 Agent 节点面板（从 API 动态加载）
 * - MiniMap 缩略图导航
 * - 网格对齐
 * - 连线校验：防止形成循环
 * - 运行时查看模式 + 右键上下文菜单
 */
import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { AlertTriangle, X } from "lucide-react";
import ReactFlow, {
  Node,
  Edge,
  type NodeChange,
  type EdgeChange,
  Background,
  Controls,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  MarkerType,
  ReactFlowProvider,
  useReactFlow,
  MiniMap,
} from "reactflow";
import "reactflow/dist/style.css";
import NodePalette from "./NodePalette";
import WorkflowNode from "./WorkflowNode";
import { StartNode, EndNode, ParallelGatewayNode, ConvergeGatewayNode, ConditionGatewayNode, LoopGatewayNode } from "./StartEndNodes";
import NodeConfigPanel from "./NodeConfigPanel";
import VariableManager from "./VariableManager";
import SubprocessPopup from "./SubprocessPopup";
import ContextMenu from "./ContextMenu";
import ConditionEdgeEditor from "./ConditionEdgeEditor";
import type { WorkflowNodeDef, NodeExecutionInfo, WorkflowDefinition, WorkflowDetailResponse, WorkflowVariable } from "../../types";
import {
  buildWorkflowSavePayload,
  canvasNodeExecutionData,
  createDroppedWorkflowNode,
  END_NODE_ID,
  generateEdgeId,
  getVariableReferences,
  loadWorkflowGraph,
  resolveNodeExecutionInfo,
  SNAP_GRID,
  START_NODE_ID,
  toNodeExecutionInfo,
  wouldCreateCycle,
} from "./workflowCanvasUtils";
import type {
  ClipboardData,
  ContextMenuState,
  WorkflowCanvasProps,
} from "./workflowCanvasUtils";

const nodeTypes = {
  startNode: StartNode, endNode: EndNode, workflowNode: WorkflowNode,
  parallelGateway: ParallelGatewayNode, convergeGateway: ConvergeGatewayNode,
  conditionGateway: ConditionGatewayNode, loopGateway: LoopGatewayNode,
};

// ============ Main Component ============

function WorkflowCanvasInner({ workflowId, taskId, readOnly, selectionMode, disabledNodeIds, onNodeToggle, onNodeClick, onDirtyChange, saveRequested, onSaveComplete, onSaveError, liveNodeStates }: WorkflowCanvasProps) {
  const reactFlowInstance = useReactFlow();
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState<WorkflowNodeDef | null>(null);
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveErrorDialog, setSaveErrorDialog] = useState<string | null>(null);  // 保存校验失败弹窗
  const [showVariableManager, setShowVariableManager] = useState(false);
  const [workflowVariables, setWorkflowVariables] = useState<WorkflowVariable[]>([]);
  const [editingEdge, setEditingEdge] = useState<Edge | null>(null);  // 正在编辑条件的边
  const [editingEdgeIsLoop, setEditingEdgeIsLoop] = useState(false);   // 是否为循环网关出边
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    show: false,
    x: 0,
    y: 0,
    type: "node",
  });
  // Subprocess popup state
  const [subprocessPopup, setSubprocessPopup] = useState<{
    nodeId: string;
    anchorX: number;
    anchorY: number;
    nodeDef: WorkflowNodeDef;
  } | null>(null);
  const [subprocessChildDef, setSubprocessChildDef] = useState<{
    workflow_id: string;
    nodes: WorkflowNodeDef[];
    edges: WorkflowDefinition["edges"];
  } | null>(null);
  const clipboardRef = useRef<ClipboardData>({ nodeType: "", nodeData: null });
  const dirtyRef = useRef(false);
  const initialLoadRef = useRef(true);
  const definitionRef = useRef<WorkflowDefinition | null>(null);
  // Keep definitionRef in sync so node click handler never reads stale definition
  definitionRef.current = definition;
  // Store full task node states for child_states lookup
  const taskNodeStatesRef = useRef<Record<string, NodeExecutionInfo>>({});

  // 未保存变更通知
  const notifyDirty = useCallback((dirty: boolean) => {
    if (dirtyRef.current !== dirty) {
      dirtyRef.current = dirty;
      onDirtyChange?.(dirty);
    }
  }, [onDirtyChange]);

  // 任务模式（taskId 存在时强制只读）或查看模式（readOnly prop）
  const isTaskMode = Boolean(taskId);
  const isReadOnly = readOnly || isTaskMode || selectionMode;
  const agentNodeCount = nodes.filter(
    (n) => n.id !== START_NODE_ID && n.id !== END_NODE_ID
  ).length;

  // 计算变量→节点引用映射（用于 VariableManager 引用计数显示）
  const varRefs = useMemo(() => getVariableReferences(definition), [definition]);

  // ---- Load workflow ----
  const loadWorkflow = useCallback(async () => {
    try {
      const loaded = await loadWorkflowGraph({
        workflowId,
        taskId,
        isTaskMode,
        isReadOnly: Boolean(isReadOnly),
        selectionMode: Boolean(selectionMode),
      });
      if (loaded) {
        setDefinition(loaded.definition);
        setWorkflowVariables(loaded.definition.variables || []);
        taskNodeStatesRef.current = loaded.nodeStates;
        setNodes(loaded.nodes);
        setEdges(loaded.edges);
      }
    } catch (error) {
      console.error("加载工作流失败:", error);
    } finally {
      setLoading(false);
      initialLoadRef.current = false;
    }
  }, [workflowId, taskId, isTaskMode, isReadOnly, selectionMode, setNodes, setEdges]);

  useEffect(() => {
    loadWorkflow();
  }, [loadWorkflow]);

  // ---- 节点选择模式：动态更新节点 data（不重新拉取工作流） ----
  useEffect(() => {
    if (!selectionMode) return;
    // 等待 loadWorkflow 完成后节点数据就位
    if (nodes.length <= 2) return; // 只有 START+END，业务节点尚未加载
    setNodes((nds) =>
      nds.map((n) => {
        // 只更新 business 节点，START/END 不更新
        if (n.id === START_NODE_ID || n.id === END_NODE_ID) return n;
        return {
          ...n,
          data: {
            ...n.data,
            selectionMode: true,
            checked: disabledNodeIds ? !disabledNodeIds.has(n.id) : true,
            onToggleCheck: onNodeToggle,
          },
        };
      })
    );
  }, [selectionMode, disabledNodeIds, onNodeToggle, setNodes, nodes.length]);

  // ---- WebSocket 驱动的节点状态更新（替代 HTTP 轮询） ----
  useEffect(() => {
    if (!isTaskMode || !liveNodeStates) return;

    const hasRunning = Object.values(liveNodeStates).some((ns) => ns.status === "running");
    const hasActiveNode = Object.values(liveNodeStates).some(
      (ns) => ns.status === "running" || ns.status === "retry_waiting",
    );

    setNodes((nds) =>
      nds.map((n) => {
        const ns = liveNodeStates[n.id];
        return ns
          ? {
              ...n,
              data: { ...n.data, ...canvasNodeExecutionData(ns) },
            }
          : n;
      })
    );
    setEdges((eds) =>
      eds.map((e) => ({ ...e, animated: hasRunning }))
    );

    // 任务结束时重新加载完整画布
    if (!hasActiveNode) loadWorkflow();
  }, [liveNodeStates, isTaskMode, setNodes, setEdges, loadWorkflow]);

  // ---- Edge connection ----
  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      if (connection.source === connection.target) return;
      if (connection.target === START_NODE_ID) return;
      if (connection.source === END_NODE_ID) return;

      // 汇聚网关出边限制：只能有1条出边
      const sourceNode = nodes.find((n) => n.id === connection.source);
      if (sourceNode?.type === "convergeGateway") {
        const existingOut = edges.filter((e) => e.source === connection.source);
        if (existingOut.length >= 1) {
          setSaveMessage("汇聚网关只能有1条出边");
          setTimeout(() => setSaveMessage(null), 2500);
          return;
        }
      }

      // 普通节点（非网关节多出边）多出边检测：引导用户使用并行网关
      if (
        sourceNode?.type !== "parallelGateway" &&
        sourceNode?.type !== "convergeGateway" &&
        sourceNode?.type !== "conditionGateway" &&
        sourceNode?.type !== "loopGateway" &&
        sourceNode?.id !== START_NODE_ID
      ) {
        const existingOut = edges.filter((e) => e.source === connection.source);
        if (existingOut.length >= 1) {
          setSaveMessage(
            "检测到多条出边。如需并行执行，请从左侧「流程控制」拖入并行网关和汇聚网关"
          );
          setTimeout(() => setSaveMessage(null), 4000);
          return;
        }
      }

      // 条件网关和循环网关允许回环（循环结构），其他节点禁止
      const targetNode = nodes.find((n) => n.id === connection.target);
      const isLoopTarget =
        targetNode?.type === "conditionGateway" ||
        targetNode?.type === "loopGateway";
      const isLoopSource =
        sourceNode?.type === "conditionGateway" ||
        sourceNode?.type === "loopGateway";
      if (!isLoopSource && !isLoopTarget) {
        if (wouldCreateCycle(connection.source, connection.target, edges)) {
          setSaveMessage("无法创建连线：会形成循环");
          setTimeout(() => setSaveMessage(null), 2500);
          return;
        }
      }

      const sourceId = connection.source;
      const targetId = connection.target;
      if (!sourceId || !targetId) return;

      // 并行网关不能直接连汇聚网关
      if (sourceNode?.type === "parallelGateway") {
        const targetNode = nodes.find((n) => n.id === connection.target);
        if (targetNode?.type === "convergeGateway") {
          setSaveMessage("并行网关不能直接连接汇聚网关，两者之间必须有至少一个可执行节点");
          setTimeout(() => setSaveMessage(null), 2500);
          return;
        }
      }

      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            id: generateEdgeId(sourceId, targetId),
            style: { stroke: "#6366F1", strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#6366F1" },
            deletable: !isReadOnly,
          },
          eds
        )
      );
      notifyDirty(true);
    },
    [edges, nodes, setEdges, isReadOnly, notifyDirty]
  );

  // ---- Node click ----
  const onNodeClickHandler = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.id === START_NODE_ID || node.id === END_NODE_ID) return;

      // 网关节点：不打开配置面板
      if (
        node.type === "parallelGateway" ||
        node.type === "convergeGateway" ||
        node.type === "loopGateway"
      ) return;

      const d = node.data as Record<string, unknown>;
      const nodeType = (d.node_type as string) || "agent";

      // 任务模式：触发外部回调打开消息抽屉
      if (isTaskMode && onNodeClick) {
        // 子流程节点：只打开浮窗，不打开消息抽屉（子流程节点本身无 session_id）
        if (nodeType === "subprocess") {
          const defNode = definitionRef.current?.nodes?.find((n) => n.id === node.id);
          if (defNode?.sub_workflow_id) {
            setSubprocessPopup({
              nodeId: node.id,
              anchorX: node.positionAbsolute?.x ?? node.position.x,
              anchorY: node.positionAbsolute?.y ?? node.position.y,
              nodeDef: defNode,
            });
            // Fetch child definition
            fetch(`/api/workflows/${encodeURIComponent(defNode.sub_workflow_id)}`)
              .then((res) => res.json())
              .then((data: WorkflowDetailResponse) => {
                setSubprocessChildDef({
                  workflow_id: data.definition?.workflow_id || defNode.sub_workflow_id || "",
                  nodes: data.definition?.nodes || [],
                  edges: data.definition?.edges || [],
                });
              })
              .catch(() => setSubprocessChildDef(null));
          }
          return;
        }

        const sessionId = (d.session_id as string) || "";
        onNodeClick(node.id, sessionId, nodeType, d.label as string|undefined, d.status as string|undefined);
        return;
      }

      // 编辑模式：打开节点配置面板（通过 ref 读取最新 definition，避免 stale closure）
      const def = definitionRef.current?.nodes?.find((n) => n.id === node.id);
      if (def) setSelectedNode(def);
    },
    [isTaskMode, onNodeClick]
  );

  // ---- Context menu events ----
  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault();
      if (node.id === START_NODE_ID || node.id === END_NODE_ID) return;
      setContextMenu({
        show: true,
        x: event.clientX,
        y: event.clientY,
        type: "node",
        nodeId: node.id,
      });
    },
    []
  );

  const onEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.preventDefault();
      setContextMenu({
        show: true,
        x: event.clientX,
        y: event.clientY,
        type: "edge",
        edgeId: edge.id,
      });
    },
    []
  );

  const closeContextMenu = useCallback(() => {
    setContextMenu((prev) => ({ ...prev, show: false }));
  }, []);

  // ---- Drag-drop: create agent node with type from palette ----
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      if (isReadOnly || !reactFlowWrapper.current) return;

      let nodeType = "";
      for (const item of event.dataTransfer.types) {
        if (item.startsWith("application/workflow-node:")) {
          nodeType = item.replace("application/workflow-node:", "");
          break;
        }
      }
      if (!nodeType) {
        nodeType = event.dataTransfer.getData("application/workflow-agent") || "agent";
      }

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });
      const snappedPosition = {
        x: Math.round(position.x / SNAP_GRID[0]) * SNAP_GRID[0],
        y: Math.round(position.y / SNAP_GRID[1]) * SNAP_GRID[1],
      };
      const dropped = createDroppedWorkflowNode(nodeType, snappedPosition);

      setNodes((currentNodes) => {
        const endIndex = currentNodes.findIndex((node) => node.id === END_NODE_ID);
        if (endIndex < 0) return currentNodes.concat(dropped.canvasNode);
        const updated = [...currentNodes];
        updated.splice(endIndex, 0, dropped.canvasNode);
        return updated;
      });

      if (dropped.definitionNode) {
        setDefinition((current) => current
          ? { ...current, nodes: [...current.nodes, dropped.definitionNode!] }
          : {
              workflow_id: workflowId,
              name: "",
              version: 1,
              created_at: "",
              updated_at: "",
              nodes: [dropped.definitionNode!],
              edges: [],
            });
      }
      notifyDirty(true);
    },
    [reactFlowInstance, setNodes, isReadOnly, workflowId, notifyDirty],
  );

  // ---- Node delete guard ----
  const handleNodesDelete = useCallback(
    (deleted: Node[]) => {
      const blocked = deleted.find(
        (n) => n.id === START_NODE_ID || n.id === END_NODE_ID
      );
      if (blocked) return;
    },
    []
  );


  // Wrap onNodesChange/onEdgesChange to track dirty (skip initial load)
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes);
    if (!isReadOnly && !isTaskMode && !initialLoadRef.current) notifyDirty(true);
  }, [isReadOnly, isTaskMode, onNodesChange, notifyDirty]);

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    onEdgesChange(changes);
    if (!isReadOnly && !isTaskMode && !initialLoadRef.current) notifyDirty(true);
  }, [isReadOnly, isTaskMode, onEdgesChange, notifyDirty]);

  // ---- Explicit save (triggered by parent via saveRequested) ----
  const explicitSave = useCallback(async () => {
    if (isReadOnly) return;
    const payload = buildWorkflowSavePayload(definition, nodes, edges, workflowVariables);
    try {
      const response = await fetch(`/api/workflows/${workflowId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (response.ok) {
        notifyDirty(false);
        setSaveErrorDialog(null);
        onSaveComplete?.();
      } else if (response.status === 400) {
        const data = await response.json().catch(() => null);
        setSaveErrorDialog(data?.detail || "校验失败");
        onSaveError?.();
      }
    } catch {
      setSaveErrorDialog("网络错误，保存失败");
      onSaveError?.();
    }
  }, [workflowId, nodes, edges, definition, workflowVariables, isReadOnly, notifyDirty, onSaveComplete, onSaveError]);

  // Watch saveRequested changes to trigger explicit save
  const lastSaveRequestedRef = useRef(0);
  useEffect(() => {
    if (saveRequested && saveRequested > lastSaveRequestedRef.current) {
      lastSaveRequestedRef.current = saveRequested;
      explicitSave();
    }
  }, [saveRequested, explicitSave]);

  // Auto-focus save error dialog for Escape key support
  useEffect(() => {
    if (!saveErrorDialog) return;
    const timer = setTimeout(() => {
      const dialog = document.querySelector('[aria-label="保存校验失败"]') as HTMLElement | null;
      dialog?.focus();
    }, 50);
    return () => clearTimeout(timer);
  }, [saveErrorDialog]);

  // ---- Node update callback for config panel ----
  const handleNodeUpdate = useCallback(
    (nodeId: string, updates: Partial<WorkflowNodeDef>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? {
                ...n,
                data: {
                  ...n.data,
                  label: updates.label ?? n.data.label,
                  agent_type: updates.agent_type ?? n.data.agent_type,
                },
              }
            : n
        )
      );
      if (definition) {
        setDefinition({
          ...definition,
          nodes: definition.nodes.map((n) =>
            n.id === nodeId ? { ...n, ...updates } : n
          ),
        });
      }
      // 同步更新 selectedNode，确保 NodeConfigPanel 拿到最新状态
      setSelectedNode((prev) =>
        prev && prev.id === nodeId ? { ...prev, ...updates } : prev
      );
      notifyDirty(true);
    },
    [setNodes, definition, notifyDirty]
  );

  // ---- Variable change handler (from NodeConfigPanel hook toggle) ----
  const handleVarChange = useCallback(
    (action: "create" | "delete", variable: WorkflowVariable, field: string, originalValue: string) => {
      if (!selectedNode) return;
      const nodeId = selectedNode.id;
      const isOutputVar = variable.source_type === "output";

      if (action === "create") {
        // 添加变量到全局列表
        setWorkflowVariables((prev) => [...prev, variable]);
        // 输出变量不绑定节点字段（仅添加到变量列表）
        if (!isOutputVar && field) {
          // 更新节点 var_bindings 和字段值
          const updatedBindings = { ...(selectedNode.var_bindings || {}), [field]: { original_value: originalValue, var_key: variable.key } };
          const updates: Partial<WorkflowNodeDef> = { var_bindings: updatedBindings };
          if (field === "label") updates.label = `{{${variable.key}}}`;
          else if (field === "agent_type") updates.agent_type = `{{${variable.key}}}`;
          else if (field === "system_prompt_template") updates.system_prompt_template = `{{${variable.key}}}`;
          else if (field === "first_message") updates.first_message = `{{${variable.key}}}`;
          handleNodeUpdate(nodeId, updates);
        }
      } else {
        // 删除变量，恢复原始值
        setWorkflowVariables((prev) => prev.filter((v) => v.key !== variable.key));
        if (!isOutputVar && field) {
          const updatedBindings = { ...(selectedNode.var_bindings || {}) };
          delete updatedBindings[field];
          const updates: Partial<WorkflowNodeDef> = { var_bindings: updatedBindings };
          if (field === "label") updates.label = originalValue;
          else if (field === "agent_type") updates.agent_type = originalValue;
          else if (field === "system_prompt_template") updates.system_prompt_template = originalValue;
          else if (field === "first_message") updates.first_message = originalValue;
          handleNodeUpdate(nodeId, updates);
        }
      }
      notifyDirty(true);
    },
    [selectedNode, handleNodeUpdate, notifyDirty]
  );

  // ---- Context menu actions ----
  const handleContextMenuAction = useCallback(
    (action: string, payload?: Record<string, unknown>) => {
      closeContextMenu();

      if (action === "configure" && payload?.nodeId) {
        const nodeId = payload.nodeId as string;
        const def = definition?.nodes?.find((n) => n.id === nodeId);
        if (def) setSelectedNode(def);
      }

      if (action === "delete-node" && payload?.nodeId) {
        const nodeId = payload.nodeId as string;
        const nodeLabel = definition?.nodes?.find((n) => n.id === nodeId)?.label || nodeId;
        if (!window.confirm(`确认删除节点 "${nodeLabel}" 吗？`)) return;
        setNodes((nds) => nds.filter((n) => n.id !== nodeId));
        setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
        setDefinition((prev) =>
          prev
            ? { ...prev, nodes: prev.nodes.filter((n) => n.id !== nodeId) }
            : null
        );
        if (selectedNode?.id === nodeId) setSelectedNode(null);
        notifyDirty(true);
      }

      if (action === "copy-node" && payload?.nodeId) {
        const nodeId = payload.nodeId as string;
        const nodeDef = definition?.nodes?.find((n) => n.id === nodeId);
        if (nodeDef) {
          clipboardRef.current = { nodeType: nodeDef.agent_type, nodeData: { ...nodeDef } };
        }
      }

      if (action === "disconnect-node" && payload?.nodeId) {
        const nodeId = payload.nodeId as string;
        setEdges((eds) =>
          eds.filter((e) => e.source !== nodeId && e.target !== nodeId)
        );
        notifyDirty(true);
      }

      if (action === "delete-edge" && payload?.edgeId) {
        const edgeId = payload.edgeId as string;
        setEdges((eds) => eds.filter((e) => e.id !== edgeId));
        notifyDirty(true);
      }
    },
    [
      closeContextMenu,
      definition,
      selectedNode,
      setNodes,
      setDefinition,
      setSelectedNode,
      setEdges,
      notifyDirty,
    ]
  );

  // ---- Render ----
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400" role="status" aria-label="正在加载工作流">
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.3s]" />
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.15s]" />
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" />
          </div>
          <span className="sr-only">加载工作流中...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden">
      {/* Left: Node Palette */}
      {!isReadOnly && <NodePalette />}

      {/* Center: Canvas */}
      <div className="flex-1 relative" ref={reactFlowWrapper}>

        {/* Variable Manager Toggle (edit mode only) */}
        {!isReadOnly && (
          <button
            onClick={() => {
              setShowVariableManager((prev) => !prev);
              setSelectedNode(null);
            }}
            aria-label={`变量管理器${workflowVariables.length > 0 ? `，${workflowVariables.length} 个变量` : ""}`}
            aria-pressed={showVariableManager}
            className={`absolute top-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer min-h-[44px] ${
              showVariableManager
                ? "bg-indigo-500 text-white"
                : "bg-slate-900/80 border border-indigo-500/20 text-slate-400 hover:border-indigo-500/40"
            }`}
          >
            <span className="text-sm font-mono font-bold" aria-hidden="true">{`{x}`}</span>
            变量 {workflowVariables.length > 0 ? `(${workflowVariables.length})` : ""}
          </button>
        )}

        {/* Toast — inline connection warnings */}
        {saveMessage && (
          <div className="absolute top-12 left-1/2 -translate-x-1/2 z-10 px-4 py-1.5 rounded-lg bg-red-500/20 border border-red-500/30 text-xs text-red-400" role="alert" aria-live="polite">
            {saveMessage}
          </div>
        )}

        {/* Modal — 保存校验失败弹窗 */}
        {saveErrorDialog && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setSaveErrorDialog(null)} role="dialog" aria-modal="true" aria-label="保存校验失败" onKeyDown={(e) => { if (e.key === "Escape") setSaveErrorDialog(null); }} tabIndex={-1}>
            <div className="bg-slate-900 border-2 border-red-500/40 rounded-2xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden" onClick={(e) => e.stopPropagation()}>
              {/* Header */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-red-500/20">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-red-500/20 flex items-center justify-center">
                    <AlertTriangle className="w-4 h-4 text-red-400" aria-hidden="true" />
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-red-400">保存校验失败</h3>
                    <p className="text-xs text-slate-500">请修复以下问题后重试</p>
                  </div>
                </div>
                <button onClick={() => setSaveErrorDialog(null)} className="text-slate-500 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center" aria-label="关闭对话框">
                  <X className="w-5 h-5" aria-hidden="true" />
                </button>
              </div>
              {/* Body — 解析后台返回的多行错误 */}
              <div className="px-6 py-4 max-h-64 overflow-y-auto">
                <ul className="space-y-2" role="list">
                  {saveErrorDialog.split("\n").filter(Boolean).map((line, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-slate-200 leading-relaxed" role="listitem">
                      <span className="text-red-400 mt-0.5 shrink-0" aria-hidden="true">•</span>
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
              </div>
              {/* Footer */}
              <div className="px-6 py-3 border-t border-indigo-500/10 flex justify-end">
                <button
                  type="button"
                  onClick={() => setSaveErrorDialog(null)}
                  className="px-4 py-2 rounded-lg bg-indigo-500/20 border border-indigo-500/30 text-xs text-indigo-400 hover:bg-indigo-500/30 transition-colors cursor-pointer min-h-[44px]"
                >
                  我知道了
                </button>
              </div>
            </div>
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={isReadOnly ? undefined : handleNodesChange}
          onEdgesChange={isReadOnly ? undefined : handleEdgesChange}
          onConnect={isReadOnly ? undefined : onConnect}
          onNodeClick={onNodeClickHandler}
          onNodeContextMenu={onNodeContextMenu}
          onEdgeContextMenu={onEdgeContextMenu}
          onEdgeClick={isReadOnly ? undefined : (_event, edge) => {
            const sourceNode = nodes.find((n) => n.id === edge.source);
            if (sourceNode?.type === "conditionGateway" || sourceNode?.type === "loopGateway") {
              setEditingEdge(edge);
              setEditingEdgeIsLoop(sourceNode?.type === "loopGateway");
            }
          }}
          onNodesDelete={handleNodesDelete}
          onDragOver={isReadOnly ? undefined : onDragOver}
          onDrop={isReadOnly ? undefined : onDrop}
          nodeTypes={nodeTypes}
          fitView
          snapToGrid={!isReadOnly}
          snapGrid={SNAP_GRID}
          className="bg-slate-950"
          defaultEdgeOptions={{
            style: { stroke: "#6366F1", strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#6366F1" },
          }}
          aria-label="工作流画布"
          nodesDraggable={!isReadOnly}
          nodesConnectable={!isReadOnly}
          elementsSelectable={!isReadOnly}
          deleteKeyCode={null}
          multiSelectionKeyCode={null}
          selectNodesOnDrag={false}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={16}
            size={1}
            color="#334155"
          />
          {!isReadOnly && (
            <Controls className="!bg-slate-900 !border-slate-800 !fill-slate-400" />
          )}
          <MiniMap
            nodeStrokeColor="#6366F1"
            nodeColor={(n) => {
              if (n.id === START_NODE_ID) return "#22C55E";
              if (n.id === END_NODE_ID) return "#EF4444";
              const agentColor = (n.data as Record<string, string>)?.agent_type;
              const nodeStatus = (n.data as Record<string, string>)?.status;
              if (nodeStatus === "running") return "#3B82F6";
              if (nodeStatus === "retry_waiting") return "#F59E0B";
              if (nodeStatus === "completed") return "#22C55E";
              if (nodeStatus === "failed") return "#EF4444";
              return agentColor === "coder"
                ? "#22C55E"
                : agentColor === "reviewer"
                ? "#3B82F6"
                : agentColor === "researcher"
                ? "#F59E0B"
                : agentColor === "reader"
                ? "#8B5CF6"
                : "#6366F1";
            }}
            maskColor="rgba(15, 23, 42, 0.7)"
            className="!bg-slate-900/80 !border !border-indigo-500/10"
            aria-label="工作流缩略图导航"
          />
        </ReactFlow>

        {/* Empty state */}
        {!isReadOnly && agentNodeCount === 0 && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none" role="status" aria-label="画布为空，请从左侧拖拽节点">
            <div className="text-center">
              <div className="mb-3">
                <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900/60 border border-dashed border-indigo-500/30">
                  <div className="w-2 h-2 rounded-full bg-indigo-500 animate-bounce" aria-hidden="true" />
                  <span className="text-sm text-slate-400">
                    从左侧拖拽 Agent 节点到此处
                  </span>
                </div>
              </div>
              <p className="text-xs text-slate-500">
                从 START 连线到 Agent，再到 END
              </p>
            </div>
          </div>
        )}

        {/* Context menu */}
        {contextMenu.show && (
          <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            type={contextMenu.type}
            nodeId={contextMenu.nodeId}
            edgeId={contextMenu.edgeId}
            onAction={handleContextMenuAction}
            onClose={closeContextMenu}
          />
        )}
      </div>

      {/* Right: Variable Manager Panel */}
      {showVariableManager && (
        <VariableManager
          variables={workflowVariables}
          varRefs={varRefs}
          onClose={() => setShowVariableManager(false)}
          onUpdate={isReadOnly ? undefined : (updated) => {
            setWorkflowVariables(updated);
            notifyDirty(true);
          }}
          onOutputVarRename={isReadOnly ? undefined : (oldKey, newKey, nodeId) => {
            // 同步回源节点的 output_variable 字段
            const node = (definition?.nodes || nodes).find((item) => item.id === nodeId);
            if (node) {
              handleNodeUpdate(nodeId, {
                output_variable: newKey,
              } as Partial<WorkflowNodeDef>);
            }
          }}
          isReadOnly={isReadOnly}
        />
      )}

      {/* Right: Node Config Panel */}
      {selectedNode && !showVariableManager && (
        <NodeConfigPanel
          node={selectedNode}
          isReadOnly={isReadOnly}
          workflowId={workflowId}
          onClose={() => setSelectedNode(null)}
          onUpdate={handleNodeUpdate}
          variables={workflowVariables}
          varBindings={selectedNode.var_bindings || {}}
          onVarChange={handleVarChange}
          onDelete={
            isReadOnly
              ? undefined
              : (nodeId) => {
                  setNodes((nds) => nds.filter((n) => n.id !== nodeId));
                  setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
                  setDefinition((prev) =>
                    prev
                      ? {
                          ...prev,
                          nodes: prev.nodes.filter((n) => n.id !== nodeId),
                        }
                      : null
                  );
                  setSelectedNode(null);
                }
          }
        />
      )}

      {/* Edge condition editor */}
      {editingEdge && (
        <ConditionEdgeEditor
          edge={editingEdge}
          isLoopGate={editingEdgeIsLoop}
          onClose={() => { setEditingEdge(null); setEditingEdgeIsLoop(false); }}
          onSave={(edgeId, condition) => {
            setEdges((eds) =>
              eds.map((e) => {
                if (e.id !== edgeId) return e;
                if (!condition) {
                  // 清除条件
                  return {
                    ...e,
                    label: undefined,
                    labelStyle: undefined,
                    style: { stroke: "#6366F1", strokeWidth: 2 },
                    markerEnd: { type: MarkerType.ArrowClosed, color: "#6366F1" },
                    data: { ...e.data, condition: null },
                  };
                }
                return {
                  ...e,
                  label: condition.is_default ? "默认" : (condition.label || condition.expression),
                  labelStyle: { fontSize: 12, fill: condition.is_default ? "#64748B" : "#3B82F6" },
                  style: {
                    stroke: condition.is_default ? "#64748B" : "#3B82F6",
                    strokeWidth: 2,
                  },
                  markerEnd: {
                    type: MarkerType.ArrowClosed,
                    color: condition.is_default ? "#64748B" : "#3B82F6",
                  },
                  data: { ...e.data, condition },
                };
              })
            );
            setEditingEdge(null);
            notifyDirty(true);
          }}
        />
      )}

      {/* Subprocess popup */}
      {subprocessPopup && (
        <SubprocessPopup
          workflowId={workflowId}
          taskId={taskId || ""}
          visible={true}
          anchorX={subprocessPopup.anchorX}
          anchorY={subprocessPopup.anchorY}
          nodeDef={subprocessPopup.nodeDef}
          childDefinition={subprocessChildDef}
          childStates={
            // Merge initial child_states from task data with real-time WS updates
            (() => {
              const parentState = taskNodeStatesRef.current[subprocessPopup.nodeId];
              const initialChildStates = parentState?.child_states || {};
              const merged: Record<string, NodeExecutionInfo> = { ...initialChildStates };
              // Apply live updates (WS events with parent_node_id)
              if (liveNodeStates) {
                for (const [nid, ns] of Object.entries(liveNodeStates)) {
                  if (ns.parent_node_id === subprocessPopup.nodeId) {
                    const normalizedState = toNodeExecutionInfo(nid, ns, merged[nid]);
                    if (normalizedState) {
                      merged[nid] = { ...merged[nid], ...normalizedState };
                    }
                  }
                }
              }
              return Object.keys(merged).length > 0 ? merged : undefined;
            })()
          }
          nodeState={resolveNodeExecutionInfo(
            subprocessPopup.nodeId,
            liveNodeStates?.[subprocessPopup.nodeId],
            taskNodeStatesRef.current[subprocessPopup.nodeId],
          )}
          onActionComplete={loadWorkflow}
          onChildNodeClick={(nodeId, sessionId, nodeType, nodeLabel) => {
            // Forward to the parent's onNodeClick (opens NodeMessageDrawer)
            if (nodeType && nodeLabel) {
              onNodeClick?.(nodeId, sessionId, nodeType, nodeLabel, "pending");
            } else {
              onNodeClick?.(nodeId, sessionId);
            }
          }}
          onClose={() => {
            setSubprocessPopup(null);
            setSubprocessChildDef(null);
          }}
        />
      )}
    </div>
  );
}

// ============ Wrapper with ReactFlowProvider ============

export default function WorkflowCanvas(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
