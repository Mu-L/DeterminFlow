/**
 * SubprocessPopup - 子流程运行进度浮窗
 *
 * 从子流程节点上弹出，展示内部节点的 DAG 执行进度。
 * 浮窗内节点可点击复用 NodeMessageDrawer，点击外部区域关闭。
 */
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import type { WorkflowNodeDef, WorkflowEdgeDef, WorkflowVariable, NodeExecutionInfo } from "../../types";
import { NODE_TYPE_COLORS, AGENT_TYPE_COLORS } from "../../types";
import NodeFailureRuntimePanel from "./NodeFailureRuntimePanel";

interface SubprocessPopupProps {
  workflowId: string;
  taskId: string;
  visible: boolean;
  /** 弹出锚点（相对于视口） */
  anchorX: number;
  anchorY: number;
  /** 子流程节点定义（含 sub_workflow_id） */
  nodeDef: WorkflowNodeDef;
  /** 子流程的 DAG 定义（从 WorkflowTask.snapshot_definition 中提取） */
  childDefinition?: {
    workflow_id: string;
    nodes: WorkflowNodeDef[];
    edges: WorkflowEdgeDef[];
    variables?: WorkflowVariable[];
  } | null;
  /** 子流程内部节点状态（来自 child_states） */
  childStates?: Record<string, NodeExecutionInfo>;
  /** 子流程父节点的运行状态与尝试历史 */
  nodeState?: NodeExecutionInfo;
  /** 点击子流程内部节点 */
  onChildNodeClick?: (nodeId: string, sessionId: string, nodeType?: string, nodeLabel?: string) => void;
  /** 关闭浮窗 */
  onClose: () => void;
  onActionComplete?: () => void | Promise<void>;
}

const STATUS_COLORS: Record<string, string> = {
  pending: "#475569",
  running: "#3B82F6",
  retry_waiting: "#F59E0B",
  completed: "#22C55E",
  failed: "#EF4444",
  waiting_approval: "#F59E0B",
  skipped: "#64748B",
};

export default function SubprocessPopup({
  workflowId,
  taskId,
  visible,
  anchorX,
  anchorY,
  nodeDef,
  childDefinition,
  childStates = {},
  nodeState,
  onChildNodeClick,
  onClose,
  onActionComplete,
}: SubprocessPopupProps) {
  const popupRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ x: anchorX, y: anchorY });

  // Position popup relative to anchor, keeping it on screen
  useEffect(() => {
    const pw = 560;
    const ph = Math.min(560, window.innerHeight - 40);
    const margin = 20;
    let x = anchorX - pw / 2;
    let y = anchorY - ph - 16;
    if (y < margin) y = anchorY + 16;
    if (x < margin) x = margin;
    const vw = window.innerWidth;
    if (x + pw > vw - margin) x = vw - pw - margin;
    if (y + ph > window.innerHeight - margin) y = Math.max(margin, window.innerHeight - ph - margin);
    setPosition({ x, y });
  }, [anchorX, anchorY]);

  // Close on Escape
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [visible, onClose]);

  // Compute nodes/edges before early return to avoid React hooks ordering issue
  const nodes = childDefinition?.nodes || [];
  const edges = childDefinition?.edges || [];

  // Layout: simple top-down ordering (plain function, not useMemo — hooks must all be before conditional return)
  function computeNodePositions(
    nds: WorkflowNodeDef[],
    egs: WorkflowEdgeDef[],
  ): Record<string, { x: number; y: number }> {
    const positions: Record<string, { x: number; y: number }> = {};
    const adj: Record<string, string[]> = {};
    const inDeg: Record<string, number> = {};
    for (const n of nds) {
      adj[n.id] = [];
      inDeg[n.id] = 0;
    }
    for (const e of egs) {
      // Skip virtual START/END nodes — they cause in-degree to never be zero
      if (e.source === "__start__" || e.target === "__end__") continue;
      adj[e.source]?.push(e.target);
      inDeg[e.target] = (inDeg[e.target] || 0) + 1;
    }
    const queue: string[] = [];
    for (const nid of Object.keys(inDeg)) {
      if (inDeg[nid] === 0) queue.push(nid);
    }
    const order: string[] = [];
    while (queue.length > 0) {
      const cur = queue.shift()!;
      order.push(cur);
      for (const next of adj[cur] || []) {
        inDeg[next]--;
        if (inDeg[next] === 0) queue.push(next);
      }
    }
    const yLevels: Record<string, number> = {};
    for (const nid of order) {
      let maxUpstream = 0;
      for (const e of egs) {
        if (e.target === nid && yLevels[e.source] !== undefined) {
          maxUpstream = Math.max(maxUpstream, yLevels[e.source] + 1);
        }
      }
      yLevels[nid] = maxUpstream;
    }
    const rows: Record<number, string[]> = {};
    for (const nid of order) {
      const y = yLevels[nid] ?? 0;
      rows[y] = rows[y] || [];
      rows[y]!.push(nid);
    }
    const PADDING_X = 50;
    const PADDING_Y = 50;
    const NODE_W = 150;
    const NODE_H = 56;
    const MAX_ROW = Math.max(...Object.keys(rows).map(Number), 0);
    for (let y = 0; y <= MAX_ROW; y++) {
      const rowNodes = rows[y] || [];
      const totalW = rowNodes.length * NODE_W + (rowNodes.length - 1) * PADDING_X;
      const startX = Math.max(PADDING_X, (560 - totalW) / 2);
      rowNodes.forEach((nid, i) => {
        positions[nid] = {
          x: startX + i * (NODE_W + PADDING_X),
          y: PADDING_Y + y * (NODE_H + PADDING_Y),
        };
      });
    }
    return positions;
  }

  if (!visible || !childDefinition) return null;

  const nodePositions = computeNodePositions(nodes, edges);

  return (
    <div
      className="fixed inset-0 z-40"
      onClick={(e) => {
        if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
          onClose();
        }
      }}
    >
      {/* Popup panel */}
      <div
        ref={popupRef}
        className="absolute flex flex-col bg-slate-900 border border-indigo-500/30 rounded-xl shadow-2xl overflow-hidden"
        style={{ left: position.x, top: position.y, width: 560, height: "min(560px, calc(100dvh - 40px))" }}
        role="dialog"
        aria-label={`子流程: ${nodeDef.label || "未命名"}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-indigo-500/10 bg-slate-950/50">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: NODE_TYPE_COLORS.subprocess || "#10B981" }} />
            <span className="text-sm font-semibold text-slate-200">
              子流程: {nodeDef.label || childDefinition.workflow_id || "未命名"}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-colors"
            aria-label="关闭子流程浮窗"
          >
            <X size={16} />
          </button>
        </div>

        {nodeState && (
          <NodeFailureRuntimePanel
            workflowId={workflowId}
            taskId={taskId}
            nodeId={nodeDef.id}
            nodeState={nodeState}
            compact
            onActionComplete={onActionComplete}
          />
        )}

        {/* Mini Canvas */}
        <div className="relative flex-1 min-h-0 p-4 overflow-auto">
          {/* SVG Edges */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ zIndex: 0 }}>
            <defs>
              <marker id="sp-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <path d="M0,0 L8,3 L0,6 Z" fill="#6366F1" />
              </marker>
            </defs>
            {edges.map((e) => {
              const sp = nodePositions[e.source];
              const tp = nodePositions[e.target];
              if (!sp || !tp) return null;
              const sx = sp.x + 75;
              const sy = sp.y + 56;
              const tx = tp.x + 75;
              const ty = tp.y;
              return (
                <line
                  key={e.id}
                  x1={sx} y1={sy} x2={tx} y2={ty}
                  stroke={e.condition?.is_default ? "#64748B" : "#6366F1"}
                  strokeWidth={1.5}
                  strokeDasharray={e.condition?.is_default ? "6,3" : "none"}
                  markerEnd="url(#sp-arrow)"
                />
              );
            })}
          </svg>

          {/* Nodes */}
          {nodes
            .filter((n) => !n.id.startsWith("__") || !n.id.endsWith("__"))
            .map((n) => {
              const pos = nodePositions[n.id];
              if (!pos) return null;
              const state = childStates[n.id];
              const status = state?.status || "pending";
              const nt = n.node_type || "agent";
              const color =
                NODE_TYPE_COLORS[nt] || AGENT_TYPE_COLORS[n.agent_type] || "#6366F1";
              const statusColor = STATUS_COLORS[status] || STATUS_COLORS.pending;
              const isRunning = status === "running";
              const hasClick = onChildNodeClick && state?.session_id;

              const badgeText =
                nt === "approval" ? "审批" :
                nt === "script" ? "脚本" :
                nt === "subprocess" ? "子流程" :
                n.agent_type || "agent";

              return (
                <div
                  key={n.id}
                  className={`absolute flex items-stretch rounded-lg bg-slate-800 border ${
                    isRunning ? "animate-pulse" : ""
                  } overflow-hidden`}
                  style={{
                    left: pos.x,
                    top: pos.y,
                    width: 150,
                    height: 56,
                    borderColor: statusColor,
                    cursor: hasClick ? "pointer" : "default",
                    zIndex: 1,
                  }}
                  onClick={() => {
                    if (hasClick && state) {
                      onChildNodeClick?.(n.id, state.session_id, nt, n.label);
                    }
                  }}
                  role="button"
                  aria-label={`子流程节点: ${n.label || "未命名"}, ${status}`}
                  tabIndex={hasClick ? 0 : -1}
                >
                  {/* Color bar */}
                  <div className="w-1 shrink-0 rounded-l-[7px]" style={{ backgroundColor: color }} />
                  <div className="flex-1 px-2 py-1.5 min-w-0">
                    <div className="flex items-center justify-between mb-0.5">
                      <span
                        className="text-[10px] uppercase tracking-wider px-1 rounded font-medium"
                        style={{ backgroundColor: `${color}15`, color }}
                      >
                        {badgeText}
                      </span>
                      <div
                        className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: statusColor }}
                      />
                    </div>
                    <div className="text-xs font-medium text-slate-300 truncate">
                      {n.label || "未命名"}
                    </div>
                  </div>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
