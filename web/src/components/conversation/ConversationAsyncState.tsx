import { AlertTriangle, Loader2, MessageSquare, RefreshCw } from "lucide-react";

export type ConversationAsyncStateKind = "loading" | "empty" | "error";

export interface ConversationAsyncStateProps {
  kind: ConversationAsyncStateKind;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

const DEFAULT_MESSAGES: Record<ConversationAsyncStateKind, string> = {
  loading: "正在加载消息",
  empty: "暂无消息",
  error: "消息加载失败",
};

export default function ConversationAsyncState({
  kind,
  message,
  onRetry,
  className = "",
}: ConversationAsyncStateProps) {
  const visibleMessage = message || DEFAULT_MESSAGES[kind];
  const role = kind === "error" ? "alert" : "status";

  return (
    <div className={`flex min-h-40 flex-col items-center justify-center gap-3 px-4 py-8 text-center ${className}`} role={role}>
      {kind === "loading" && <Loader2 size={22} className="animate-spin text-indigo-400 motion-reduce:animate-none" aria-hidden="true" />}
      {kind === "empty" && <MessageSquare size={22} className="text-slate-600" aria-hidden="true" />}
      {kind === "error" && <AlertTriangle size={22} className="text-red-400" aria-hidden="true" />}
      <p className={`text-sm ${kind === "error" ? "text-red-300" : "text-slate-500"}`}>{visibleMessage}</p>
      {kind === "error" && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-10 items-center gap-2 rounded-md border border-red-500/25 bg-red-500/10 px-3 text-sm text-red-300 transition-colors hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
        >
          <RefreshCw size={14} aria-hidden="true" />
          重试
        </button>
      )}
    </div>
  );
}
