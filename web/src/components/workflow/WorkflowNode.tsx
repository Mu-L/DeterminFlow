/**
 * WorkflowNode - 节点画布渲染（支持 Agent / 审批等多节点类型）
 *
 * 吸取 bk-sops TaskNode 设计：
 * - 左侧色块（对应 node_type 颜色）
 * - 右上角类型角标
 * - 运行时状态：pending(grey) → running(blue+pulse) → waiting_approval(yellow) → completed(green) → failed(red)
 * - 底部显示执行摘要
 */
import { Handle, Position, type NodeProps } from "reactflow";
import { AGENT_TYPE_COLORS, NODE_TYPE_COLORS } from "../../types";

const STATUS_CLASSES: Record<string, string> = {
  pending: "border-slate-400/30",
  running: "border-blue-500 shadow-blue-500/20",
  retry_waiting: "border-amber-500 shadow-amber-500/20",
  completed: "border-green-500 shadow-green-500/20",
  failed: "border-red-500 shadow-red-500/20",
  waiting_approval: "border-amber-500 shadow-amber-500/20",
  skipped: "border-slate-600/40",
};

const STATUS_DOT_COLORS: Record<string, string> = {
  pending: "#94a3b8",
  running: "#3b82f6",
  retry_waiting: "#f59e0b",
  completed: "#22c55e",
  failed: "#ef4444",
  waiting_approval: "#f59e0b",
  skipped: "#64748b",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  retry_waiting: "等待重试",
  completed: "已完成",
  failed: "失败",
  waiting_approval: "待审批",
  skipped: "已跳过",
};

export default function WorkflowNode({ id, data }: NodeProps) {
  const { label, agent_type, node_type, status, summary, selectionMode, checked, onToggleCheck, is_skipped: legacySkipped, error: nodeError, attempt_count: attemptCount } = data;
  const nt = node_type || "agent";
  const isSelectionMode = selectionMode === true;
  const isChecked = checked !== false; // 默认勾选

  // 颜色：优先 node_type 颜色，其次 agent_type 颜色
  const color =
    NODE_TYPE_COLORS[nt] || AGENT_TYPE_COLORS[agent_type] || "#6366F1";
  const isSkipped = legacySkipped || status === "skipped";
  const effectiveStatus = isSkipped ? "skipped" : status || "pending";
  const borderClass = STATUS_CLASSES[effectiveStatus] || STATUS_CLASSES.pending;
  const dotColor = STATUS_DOT_COLORS[effectiveStatus] || "#94A3B8";
  const isRunning = status === "running";
  const isWaitingApproval = status === "waiting_approval";

  // 角标文本
  const badgeText = nt === "approval" ? "审批" : nt === "script" ? "脚本" : nt === "subprocess" ? "子流程" : agent_type || "agent";

  return (
    <div
      className={`relative flex items-stretch rounded-xl bg-slate-900 border-2 ${borderClass} min-w-[180px] shadow-lg transition-all duration-300 overflow-hidden ${
        isRunning ? "animate-pulse motion-reduce:animate-none" : ""
      }`}
      style={{ borderColor: effectiveStatus === "pending" ? `${color}40` : undefined }}
      role="article"
      aria-label={`工作流节点: ${label || "未命名"}，状态: ${effectiveStatus}`}
    >
      {/* Left color block */}
      <div
        className="w-1 shrink-0 rounded-l-[10px]"
        style={{ backgroundColor: color }}
      />

      {/* Node body */}
      <div className="flex-1 px-3 py-2.5 min-w-0">
        <Handle
          type="target"
          position={Position.Top}
          className="!bg-slate-400 !w-2.5 !h-2.5 !border-2 !border-slate-900"
        />

        {/* Header: type badge + (status dot / checkbox) */}
        <div className="flex items-center justify-between mb-1">
          <span
            className="text-xs uppercase tracking-wider px-1.5 py-0.5 rounded-md font-medium"
            style={{ backgroundColor: `${color}15`, color }}
          >
            {isWaitingApproval ? "待审批" : badgeText}
          </span>
          {isSelectionMode ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToggleCheck?.(id, !isChecked);
              }}
              disabled={!onToggleCheck}
              aria-label={`${isChecked ? "取消勾选" : "勾选"}节点 ${label || "未命名"}`}
              className="w-6 h-6 min-w-[24px] min-h-[24px] rounded-full flex items-center justify-center border-2 transition-all duration-200 flex-shrink-0 ml-2 hover:scale-110 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30"
              style={{
                borderColor: isChecked ? "#22c55e" : "#64748b",
                backgroundColor: isChecked ? "rgba(34,197,94,0.125)" : "transparent",
              }}
            >
              {isChecked ? (
                <svg className="w-3.5 h-3.5 text-green-500" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
              ) : (
                <svg className="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              )}
            </button>
          ) : (
            effectiveStatus !== "pending" && (
              <div className="flex items-center gap-1 text-[10px] text-slate-400">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: dotColor }}
                  aria-hidden="true"
                />
                <span>{STATUS_LABELS[effectiveStatus] || effectiveStatus}</span>
              </div>
            )
          )}
        </div>

        {/* Label */}
        <div className="text-sm font-medium text-slate-100 truncate">
          {label || "未命名"}
        </div>

        {/* Skipped error tooltip */}
        {isSkipped && nodeError && (
          <div className="mt-1 text-xs text-amber-500 truncate max-w-[200px] leading-tight" title={nodeError}>
            ⚠ {nodeError}
          </div>
        )}

        {/* Summary (truncated) */}
        {!isSkipped && summary && (
          <div className="mt-1 text-xs text-slate-500 truncate max-w-[200px] leading-tight" title={summary}>
            {summary}
          </div>
        )}

        {(status === "failed" || status === "retry_waiting") && typeof attemptCount === "number" && attemptCount > 0 && (
          <div className="mt-1 text-xs text-slate-500">已尝试 {attemptCount} 次</div>
        )}

        <Handle
          type="source"
          position={Position.Bottom}
          className="!bg-slate-400 !w-2.5 !h-2.5 !border-2 !border-slate-900"
        />
      </div>
    </div>
  );
}
