import type { NodeExecutionInfo } from "../../types";

export type NodeFailureAction = "retry" | "skip";

/**
 * Core 返回 available_actions 时以 Core 为准。旧 Task 没有该字段时，
 * 仅允许对 failed 节点显示人工重试和跳过。
 */
export function getNodeFailureActions(
  nodeState: Pick<NodeExecutionInfo, "status" | "available_actions">,
): NodeFailureAction[] {
  if (Array.isArray(nodeState.available_actions)) {
    return nodeState.available_actions.filter(
      (action): action is NodeFailureAction => action === "retry" || action === "skip",
    );
  }
  return nodeState.status === "failed" ? ["retry", "skip"] : [];
}

export function secondsUntilRetry(nextRetryAt: string | null | undefined, now = Date.now()): number | null {
  if (!nextRetryAt) return null;
  const timestamp = Date.parse(nextRetryAt);
  if (!Number.isFinite(timestamp)) return null;
  return Math.max(0, Math.ceil((timestamp - now) / 1000));
}

export function formatRetryCountdown(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder > 0 ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分`;
}

export function getAttemptCount(
  nodeState: Pick<NodeExecutionInfo, "status" | "attempt_count" | "attempt_history">,
): number {
  return Math.max(
    nodeState.attempt_count ?? 0,
    nodeState.attempt_history?.length ?? 0,
    nodeState.status === "failed" || nodeState.status === "retry_waiting" ? 1 : 0,
  );
}

/** CAS 控制值必须使用 Core 原值，不能沿用为展示兼容而补成 1 的次数。 */
export function getControlAttemptCount(
  nodeState: Pick<NodeExecutionInfo, "attempt_count">,
): number {
  const value = nodeState.attempt_count;
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

export function formatAttemptTrigger(trigger: string | undefined): string {
  if (trigger === "auto_retry" || trigger === "automatic_retry") return "自动重试";
  if (trigger === "manual_retry") return "人工重试";
  if (trigger === "initial") return "首次执行";
  return trigger || "执行";
}

export function formatAttemptDuration(startedAt?: string | null, completedAt?: string | null): string {
  if (!startedAt || !completedAt) return "-";
  const duration = Date.parse(completedAt) - Date.parse(startedAt);
  if (!Number.isFinite(duration) || duration < 0) return "-";
  const seconds = Math.round(duration / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
