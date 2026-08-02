import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  NodeProps,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
} from "reactflow";
import "reactflow/dist/style.css";
import { Session } from "../types";
import { getStatusConfig, truncate, formatRelativeTime } from "../lib/utils-helpers";
import {
  Activity,
  Zap,
  CheckCircle2,
  XCircle,
  PauseCircle,
  Moon,
} from "lucide-react";

// ============ Status icon mapping (lucide, not emoji) ============

const STATUS_ICON_MAP: Record<string, React.ReactNode> = {
  running: <Activity size={14} />,
  streaming: <Zap size={14} />,
  completed: <CheckCircle2 size={14} />,
  error: <XCircle size={14} />,
  waiting: <PauseCircle size={14} />,
  idle: <Moon size={14} />,
};

const STATUS_LABEL_MAP: Record<string, string> = {
  running: "运行中",
  streaming: "流式传输",
  completed: "已完成",
  error: "错误",
  waiting: "等待中",
  idle: "空闲",
};

// ============ Custom Node Component ============

function SessionNode({ data }: NodeProps) {
  const { session, selected, onHover } = data;
  const cfg = getStatusConfig(session.status);
  const isMain = session.type === "main";

  const borderColor = STATUS_COLOR_MAP[session.status] || DEFAULT_BORDER_COLOR;
  const statusLabel = STATUS_LABEL_MAP[session.status] || session.status;

  return (
    <div
      className={`relative cursor-pointer transition-all duration-300 motion-reduce:transition-none ${
        selected ? "scale-110" : "hover:scale-105 motion-reduce:hover:scale-100"
      }`}
      onMouseEnter={() => onHover(session)}
      onMouseLeave={() => onHover(null)}
      role="button"
      tabIndex={0}
      aria-label={`${isMain ? "主会话" : "子会话"} ${session.session_id}，状态：${statusLabel}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onHover(session);
        }
      }}
    >
      {/* Pulse indicator for running/streaming */}
      {(session.status === "running" || session.status === "streaming") && (
        <div
          className="absolute -inset-2 rounded-xl opacity-30 status-running motion-reduce:hidden"
          style={{ background: `radial-gradient(circle, ${borderColor}40, transparent)` }}
          aria-hidden="true"
        />
      )}

      <div
        className={`relative px-4 py-3 rounded-xl bg-slate-800/80 border border-slate-700 ${
          selected ? "ring-2 ring-indigo-500" : ""
        }`}
        style={{
          borderColor: `${borderColor}60`,
          borderWidth: "2px",
          minWidth: isMain ? "180px" : "150px",
        }}
      >
        <Handle type="target" position={Position.Top} className="opacity-0" />

        <div className="flex items-center gap-2 mb-1">
          <span
            className={`w-2.5 h-2.5 rounded-full ${(session.status === "running" || session.status === "streaming") ? "status-running motion-reduce:animate-none" : ""}`}
            style={{ backgroundColor: borderColor }}
            aria-hidden="true"
          />
          <span className="text-xs font-mono font-bold text-foreground">
            {isMain ? "MAIN" : session.session_id}
          </span>
          <span className="sr-only">{statusLabel}</span>
        </div>

        {session.task && (
          <p className="text-xs text-muted-foreground leading-tight">
            {truncate(session.task, isMain ? 30 : 25)}
          </p>
        )}

        <div className="flex items-center justify-between mt-1.5">
          <span className={`text-xs ${cfg.color}`}>{session.status}</span>
          <span className="text-xs text-muted-foreground">{session.message_count} msgs</span>
        </div>

        <Handle type="source" position={Position.Bottom} className="opacity-0" />
      </div>
    </div>
  );
}

// Module-level constants (prevent re-creation on every render)
const STATUS_COLOR_MAP: Record<string, string> = {
  running: "#22c55e",   // green-500
  streaming: "#06b6d4", // cyan-500
  completed: "#3b82f6", // blue-500
  error: "#ef4444",     // red-500
  waiting: "#f59e0b",   // amber-500
  idle: "#94a3b8",      // slate-400
};
const DEFAULT_BORDER_COLOR = "#6366f1"; // indigo-500

const nodeTypes = { sessionNode: SessionNode };

// ============ Main Component ============

interface SessionGraphViewProps {
  sessions: Session[];
  mainSessionId: string | null;
  onNodeClick: (sessionId: string) => void;
  selectedSessionId: string | null;
}

export default function SessionGraphView({
  sessions,
  onNodeClick,
  selectedSessionId,
}: SessionGraphViewProps) {
  const [hoveredSession, setHoveredSession] = useState<Session | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const rafRef = useRef<number>(0);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      setMousePos({ x: e.clientX, y: e.clientY });
    });
  }, []);

  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
    const mainSessions = sessions.filter((s) => s.type === "main");
    const subSessions = sessions.filter((s) => s.type === "sub");

    const nodes: Node[] = [];
    const edges: Edge[] = [];

    // 构建 main→subs 的映射（通过 parent_id）
    const mainSubsMap: Record<string, Session[]> = {};
    for (const sub of subSessions) {
      if (sub.parent_id) {
        if (!mainSubsMap[sub.parent_id]) mainSubsMap[sub.parent_id] = [];
        mainSubsMap[sub.parent_id].push(sub);
      }
    }

    // 为每个 main 创建独立的星形簇
    const clusterSpacingX = 400;
    const clusterSpacingY = 320;
    const cols = Math.max(1, Math.ceil(Math.sqrt(mainSessions.length)));

    mainSessions.forEach((main, idx) => {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      const centerX = 250 + col * clusterSpacingX;
      const centerY = 80 + row * clusterSpacingY;

      nodes.push({
        id: main.session_id,
        type: "sessionNode",
        position: { x: centerX, y: centerY },
        data: {
          session: main,
          selected: selectedSessionId === main.session_id,
          onHover: setHoveredSession,
        },
      });

      const subs = mainSubsMap[main.session_id] || [];
      const subCount = subs.length;
      if (subCount > 0) {
        const startAngle = -Math.PI / 3;
        const endAngle = Math.PI / 3;
        const radius = 160;

        subs.forEach((sub, i) => {
          const angle = subCount === 1
            ? 0
            : startAngle + (endAngle - startAngle) * (i / (subCount - 1));
          const x = centerX + Math.sin(angle) * radius;
          const y = centerY + Math.cos(angle) * radius + 70;

          nodes.push({
            id: sub.session_id,
            type: "sessionNode",
            position: { x, y },
            data: {
              session: sub,
              selected: selectedSessionId === sub.session_id,
              onHover: setHoveredSession,
            },
          });

          edges.push({
            id: `${main.session_id}-${sub.session_id}`,
            source: main.session_id,
            target: sub.session_id,
            animated: sub.status === "running" || sub.status === "streaming",
            style: {
              stroke: (sub.status === "running" || sub.status === "streaming") ? "#22c55e" : "#6366f1",
              strokeWidth: 2,
            },
            className: "motion-reduce:!transition-none motion-reduce:!animate-none",
          });
        });
      }
    });

    return { nodes, edges };
  }, [sessions, selectedSessionId]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Update nodes when sessions change
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeClick(node.id);
    },
    [onNodeClick]
  );

  const cfg = hoveredSession ? getStatusConfig(hoveredSession.status) : null;

  return (
    <div
      className="relative w-full h-full"
      onMouseMove={handleMouseMove}
      role="application"
      aria-label="会话关系图谱，可交互节点"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        fitView
        className="bg-slate-900"
      >
        <Background color="#334155" gap={20} />
        <Controls
          className="!bg-slate-800 !border-indigo-500/20 !rounded-lg"
        />
      </ReactFlow>

      {/* Hover Tooltip */}
      {hoveredSession && cfg && (
        <div
          id="session-tooltip"
          role="tooltip"
          aria-label={`会话 ${hoveredSession.session_id} 详情`}
          className="fixed z-50 bg-slate-800 border border-slate-700 rounded-lg p-3 min-w-[220px] max-w-[320px] pointer-events-none shadow-lg"
          style={{
            left: Math.min(mousePos.x + 15, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 340),
            top: Math.min(mousePos.y + 15, (typeof window !== 'undefined' ? window.innerHeight : 800) - 200),
          }}
        >
          <div className="flex items-center gap-2 mb-2">
            <span className={`${cfg.color}`} aria-hidden="true">
              {STATUS_ICON_MAP[hoveredSession.status]}
            </span>
            <span className="text-xs font-mono font-bold text-cyan-400">{hoveredSession.session_id}</span>
            <span className={`text-xs ${cfg.color}`}>
              {STATUS_LABEL_MAP[hoveredSession.status] || hoveredSession.status}
            </span>
          </div>
          {hoveredSession.task && (
            <p className="text-xs text-slate-300 mb-1.5">{truncate(hoveredSession.task, 80)}</p>
          )}
          <div className="grid grid-cols-2 gap-1 text-xs text-muted-foreground">
            <span>消息: {hoveredSession.message_count}</span>
            <span>类型: {hoveredSession.type}</span>
            <span>创建: {formatRelativeTime(hoveredSession.created_at)}</span>
            <span>更新: {formatRelativeTime(hoveredSession.updated_at)}</span>
          </div>
          {hoveredSession.last_message && (
            <div className="mt-1.5 text-xs text-slate-400 border-t border-border pt-1.5">
              最新: {truncate(hoveredSession.last_message, 60)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
