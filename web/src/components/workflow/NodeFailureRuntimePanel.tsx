import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock3, RotateCcw, SkipForward } from "lucide-react";
import type { NodeAttemptHistoryEntry, NodeExecutionInfo } from "../../types";
import { retryWorkflowNode, skipWorkflowNode } from "../../lib/api";
import {
  formatAttemptDuration,
  formatAttemptTrigger,
  formatRetryCountdown,
  getAttemptCount,
  getControlAttemptCount,
  getNodeFailureActions,
  secondsUntilRetry,
  type NodeFailureAction,
} from "./nodeFailureUtils";

interface NodeFailureRuntimePanelProps {
  workflowId: string;
  taskId: string;
  nodeId: string;
  nodeState: NodeExecutionInfo;
  compact?: boolean;
  onActionComplete?: () => void | Promise<void>;
}

const ATTEMPT_STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  retry_waiting: "等待重试",
  completed: "成功",
  success: "成功",
  failed: "失败",
  failure: "失败",
  skipped: "已跳过",
};

const ATTEMPT_STATUS_CLASSES: Record<string, string> = {
  completed: "text-emerald-400",
  success: "text-emerald-400",
  running: "text-sky-400",
  retry_waiting: "text-amber-400",
  failed: "text-red-400",
  failure: "text-red-400",
  skipped: "text-slate-400",
};

function formatAttemptTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function attemptNumber(entry: NodeAttemptHistoryEntry, index: number): number {
  return typeof entry.attempt_number === "number" ? entry.attempt_number : index + 1;
}

function snapshotText(snapshot: unknown): string {
  if (typeof snapshot === "string") return snapshot;
  try {
    return JSON.stringify(snapshot, null, 2);
  } catch {
    return String(snapshot);
  }
}

export default function NodeFailureRuntimePanel({
  workflowId,
  taskId,
  nodeId,
  nodeState,
  compact = false,
  onActionComplete,
}: NodeFailureRuntimePanelProps) {
  const failureFocused = nodeState.status === "failed" || nodeState.status === "retry_waiting";
  const [historyOpen, setHistoryOpen] = useState(failureFocused);
  const [pendingAction, setPendingAction] = useState<NodeFailureAction | null>(null);
  const [actionError, setActionError] = useState("");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!nodeState.next_retry_at) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [nodeState.next_retry_at]);

  useEffect(() => {
    if (failureFocused) setHistoryOpen(true);
  }, [failureFocused]);

  const actions = getNodeFailureActions(nodeState);
  const secondsRemaining = secondsUntilRetry(nodeState.next_retry_at, now);
  const attempts = nodeState.attempt_history || [];
  const attemptCount = getAttemptCount(nodeState);
  const controlAttemptCount = getControlAttemptCount(nodeState);
  const hasSnapshot = nodeState.input_snapshot != null
    && (typeof nodeState.input_snapshot !== "object" || Object.keys(nodeState.input_snapshot as object).length > 0);
  const serializedSnapshot = useMemo(
    () => snapshotText(nodeState.input_snapshot),
    [nodeState.input_snapshot],
  );
  const hasRuntimeMetadata = failureFocused
    || nodeState.status === "skipped"
    || attemptCount > 0
    || attempts.length > 0
    || hasSnapshot
    || actions.length > 0;

  if (!hasRuntimeMetadata) return null;

  const runAction = async (action: NodeFailureAction) => {
    if (action === "skip") {
      const confirmed = window.confirm(
        "确认跳过该失败节点并继续执行吗？\n\n跳过不会传播失败时的部分输出，下游引用缺失输出时仍可能失败。",
      );
      if (!confirmed) return;
    }

    setPendingAction(action);
    setActionError("");
    try {
      if (action === "retry") {
        await retryWorkflowNode(workflowId, taskId, nodeId, controlAttemptCount);
      } else {
        await skipWorkflowNode(workflowId, taskId, nodeId, controlAttemptCount);
      }
      await onActionComplete?.();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "操作失败，请刷新后重试");
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <section className="border-b border-indigo-500/10 bg-slate-950/30" aria-label="节点失败处理与尝试历史">
      <div className={compact ? "px-3 py-2" : "px-3 py-2.5"}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0 text-xs text-slate-400">
            <span>尝试 {attemptCount}</span>
            {nodeState.automatic_retry_count != null && nodeState.automatic_retry_count > 0 && (
              <span>其中自动重试 {nodeState.automatic_retry_count}</span>
            )}
            {secondsRemaining != null && nodeState.status === "retry_waiting" && (
              <span className="inline-flex items-center gap-1 text-amber-400">
                <Clock3 size={12} aria-hidden="true" />
                {secondsRemaining > 0 ? `${formatRetryCountdown(secondsRemaining)}后重试` : "等待调度"}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {actions.includes("retry") && (
              <button
                type="button"
                onClick={() => runAction("retry")}
                disabled={pendingAction !== null}
                className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RotateCcw size={13} className={pendingAction === "retry" ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
                {nodeState.status === "retry_waiting" ? "立即重试" : "重试本节点"}
              </button>
            )}
            {actions.includes("skip") && (
              <button
                type="button"
                onClick={() => runAction("skip")}
                disabled={pendingAction !== null}
                className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <SkipForward size={13} aria-hidden="true" />
                跳过并继续
              </button>
            )}
          </div>
        </div>

        {actions.includes("retry") && (
          <p className="mt-1 text-xs text-slate-500">重试使用失败时冻结的原参数和输入快照。</p>
        )}
        {actionError && <p className="mt-2 text-xs text-red-400 break-all" role="alert">{actionError}</p>}

        {(attempts.length > 0 || hasSnapshot) && (
          <button
            type="button"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-expanded={historyOpen}
            className="mt-2 inline-flex min-h-[36px] items-center gap-1 text-xs text-slate-400 transition-colors hover:text-slate-200"
          >
            {historyOpen ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
            尝试记录{attempts.length > 0 ? ` (${attempts.length})` : ""}
          </button>
        )}
      </div>

      {historyOpen && (attempts.length > 0 || hasSnapshot) && (
        <div className={`${compact ? "max-h-44" : "max-h-56"} overflow-y-auto border-t border-indigo-500/10 px-3 py-2`}>
          {attempts.length > 0 && (
            <ol className="space-y-2" aria-label="节点尝试列表">
              {attempts.map((attempt, index) => {
                const status = attempt.status || "unknown";
                return (
                  <li key={attempt.attempt_id || `${attemptNumber(attempt, index)}-${attempt.started_at || index}`} className="border-b border-slate-800 pb-2 last:border-b-0 last:pb-0">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-medium text-slate-200">
                        第 {attemptNumber(attempt, index)} 次 · {formatAttemptTrigger(attempt.trigger)}
                      </span>
                      <span className={ATTEMPT_STATUS_CLASSES[status] || "text-slate-400"}>
                        {ATTEMPT_STATUS_LABELS[status] || status}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                      <span>{formatAttemptTime(attempt.started_at)}</span>
                      <span>耗时 {formatAttemptDuration(attempt.started_at, attempt.completed_at)}</span>
                      {attempt.session_id && <span className="font-mono">{attempt.session_id}</span>}
                    </div>
                    {attempt.error && <p className="mt-1 text-xs text-red-400 whitespace-pre-wrap break-all">{attempt.error}</p>}
                  </li>
                );
              })}
            </ol>
          )}

          {hasSnapshot && (
            <details className={attempts.length > 0 ? "mt-3 border-t border-slate-800 pt-2" : ""}>
              <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-200">失败时输入快照</summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-950 p-2 text-xs text-slate-300">
                {serializedSnapshot}
              </pre>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
