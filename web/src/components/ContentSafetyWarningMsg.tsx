import { useEffect, useReducer } from "react";
import type { Message } from "../types";
import { AlertTriangle, Search } from "lucide-react";
import {
  contentSafetyDiagnosticRequestReducer,
  createContentSafetyDiagnosticRequestId,
  initialContentSafetyDiagnosticRequestState,
  subscribeContentSafetyDiagnosticControlEvent,
} from "../features/conversation/contentSafetyDiagnosticProtocol";

/**
 * 内容安全警告组件
 *
 * 当 DeepSeek API 返回 Content Exists Risk 时，在会话中展示警告信息，
 * 并提供"运行详细诊断"按钮让用户主动触发二分排除诊断。
 */
interface ContentSafetyWarningMsgProps {
  message: Message;
  onCommand?: (payload: { type: string; [key: string]: unknown }) => boolean;
  readonly?: boolean;
}

export default function ContentSafetyWarningMsg({
  message,
  onCommand,
  readonly = false,
}: ContentSafetyWarningMsgProps) {
  const [requestState, dispatch] = useReducer(
    contentSafetyDiagnosticRequestReducer,
    initialContentSafetyDiagnosticRequestState,
  );
  const sessionId = message.session_id || null;

  useEffect(() => {
    dispatch({ type: "reset" });
  }, [message.id, sessionId]);

  useEffect(() => subscribeContentSafetyDiagnosticControlEvent((event) => {
    if (event.sessionId !== sessionId) return;
    dispatch({ type: "control_event", event });
  }), [sessionId]);

  const handleDiagnose = () => {
    if (
      requestState.phase === "submitting" ||
      requestState.phase === "accepted" ||
      requestState.phase === "completed"
    ) return;
    if (!sessionId || !onCommand) return;
    const requestId = createContentSafetyDiagnosticRequestId();
    if (onCommand({ type: "diagnose_content_safety", request_id: requestId })) {
      dispatch({ type: "sent", requestId });
    } else {
      dispatch({ type: "send_failed", message: "连接不可用，请稍后重试" });
    }
  };

  const errorMessage = message.content || "请求被 DeepSeek 安全审查拦截";
  const errorDetail = message.detail || "";
  const requestPending = requestState.phase === "submitting" || requestState.phase === "accepted";
  const requestCompleted = requestState.phase === "completed";
  const buttonLabel = requestCompleted
    ? "诊断完成"
    : requestPending
      ? requestState.phase === "accepted" ? "诊断运行中" : "正在提交诊断"
      : "运行详细诊断";

  return (
    <div className="flex items-center gap-2 my-4">
      {/* 分割线 */}
      <div className="flex-1 h-px bg-amber-500/30" />

      {/* 警告卡片 */}
      <div className="flex-shrink-0 max-w-[85%]">
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3" role="alert" aria-label="内容安全警告">
          {/* 标题行 */}
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={16} className="text-amber-400" aria-hidden="true" />
            <span className="text-sm font-medium text-amber-400">内容安全警告</span>
          </div>

          {/* 错误消息 */}
          <p className="text-xs text-slate-300 mb-2 leading-relaxed">{errorMessage}</p>

          {/* 错误详情 */}
          {errorDetail && (
            <p className="text-xs text-slate-500 mb-3 leading-relaxed">
              {errorDetail}
            </p>
          )}

          {requestState.message && (
            <p className={`mb-2 text-xs ${requestState.phase === "failed" ? "text-red-300" : "text-slate-400"}`} role={requestState.phase === "failed" ? "alert" : "status"}>
              {requestState.message}
            </p>
          )}

          {/* 操作按钮 */}
          <button
            type="button"
            onClick={handleDiagnose}
            disabled={requestPending || requestCompleted || readonly || !onCommand || !sessionId}
            aria-label="运行详细诊断"
            className="flex min-h-[44px] cursor-pointer items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-400 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Search size={14} aria-hidden="true" />
            {buttonLabel}
          </button>
        </div>
      </div>

      {/* 分割线 */}
      <div className="flex-1 h-px bg-amber-500/30" />
    </div>
  );
}
