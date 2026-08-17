import { AlertTriangle, Loader2, RotateCcw } from "lucide-react";

import type { FailedTurnState } from "../../features/conversation/conversationTypes";

interface FailedTurnCardProps {
  failedTurn: FailedTurnState;
  retrying?: boolean;
  onRetry?: () => void;
}

export default function FailedTurnCard({
  failedTurn,
  retrying = false,
  onRetry,
}: FailedTurnCardProps) {
  const canRetry = failedTurn.retryable && Boolean(onRetry);
  const blockedMessage = failedTurn.toolStarted
    ? "本轮已经启动工具。为避免重复操作，不能直接重试；你仍可继续发送消息。"
    : failedTurn.retryBlockReason === "retry_in_progress"
      ? "上次重试未确认结束。为避免重复执行，不能直接重试；你仍可继续发送消息。"
      : "本轮不能直接重试；你仍可继续发送消息。";
  const needsModelChange =
    failedTurn.errorCode === "authentication_failed" ||
    failedTurn.errorCode === "quota_exhausted";

  return (
    <aside
      className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3"
      role="alert"
      aria-busy={retrying}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          size={17}
          className="mt-0.5 shrink-0 text-red-400"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-sm font-medium text-red-300">本轮生成失败</p>
            <p className="mt-0.5 text-sm text-slate-300">{failedTurn.errorMessage}</p>
          </div>
          <p className="line-clamp-2 break-words border-l border-slate-600 pl-3 text-xs text-slate-400">
            {failedTurn.content}
          </p>
          {needsModelChange && (
            <p className="text-xs text-slate-400">请先切换模型或更新模型配置。</p>
          )}
          {failedTurn.attemptCount > 1 && (
            <p className="text-xs text-slate-500">已尝试 {failedTurn.attemptCount} 次</p>
          )}
          {canRetry ? (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="inline-flex min-h-10 items-center gap-2 rounded-md border border-red-500/25 bg-red-500/10 px-3 text-sm text-red-200 transition-colors hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {retrying ? (
                <Loader2
                  size={14}
                  className="animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
              ) : (
                <RotateCcw size={14} aria-hidden="true" />
              )}
              {retrying ? "正在重试" : "重试本轮"}
            </button>
          ) : (
            <p className="text-xs leading-5 text-amber-200/80">{blockedMessage}</p>
          )}
        </div>
      </div>
    </aside>
  );
}
