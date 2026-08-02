/**
 * ApprovalPanel - 审批通知面板
 *
 * 位于 ChatPage 顶部的悬浮审批通知条：
 * - 每条审批请求显示命令内容（等宽字体高亮）、来源 Session ID、工作目录、超时倒计时
 * - 操作按钮：✅ 批准 / ❌ 拒绝
 * - 多条排队显示（FIFO）
 * - 审批通过/拒绝后自动消失
 */
import { memo, useState, useEffect } from "react";
import { Shield, Check, X, Clock, Terminal } from "lucide-react";
import { ApprovalRequest } from "../types";

interface ApprovalPanelProps {
  pendingApprovals: ApprovalRequest[];
  resolvedApprovals: { request_id: string; result: string; resolved_at: string }[];
  onApprove: (requestId: string) => void;
  onReject: (requestId: string, reason?: string) => void;
  onClearResolved: (requestId: string) => void;
}

function CountdownBar({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState(100);

  useEffect(() => {
    const expiresMs = new Date(expiresAt).getTime();
    const totalMs = expiresMs - Date.now();
    if (totalMs <= 0) {
      setRemaining(0);
      return;
    }

    const timer = setInterval(() => {
      const now = Date.now();
      const left = expiresMs - now;
      if (left <= 0) {
        setRemaining(0);
        clearInterval(timer);
      } else {
        setRemaining(Math.round((left / totalMs) * 100));
      }
    }, 500);

    return () => clearInterval(timer);
  }, [expiresAt]);

  const seconds = Math.max(0, Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000));

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden" role="progressbar" aria-valuenow={remaining} aria-valuemin={0} aria-valuemax={100} aria-label={`审批超时倒计时 ${seconds} 秒`}>
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            remaining > 50 ? "bg-green-500" : remaining > 20 ? "bg-amber-500" : "bg-red-500"
          }`}
          style={{ width: `${remaining}%` }}
        />
      </div>
      <span className="text-xs text-muted-foreground font-mono w-8 text-right">
        {seconds}s
      </span>
    </div>
  );
}

function ApprovalPanel({
  pendingApprovals,
  resolvedApprovals,
  onApprove,
  onReject,
  onClearResolved,
}: ApprovalPanelProps) {
  // 自动清除已解决的通知（3秒后消失）
  useEffect(() => {
    if (resolvedApprovals.length === 0) return;
    const latest = resolvedApprovals[resolvedApprovals.length - 1];
    const timer = setTimeout(() => {
      onClearResolved(latest.request_id);
    }, 3000);
    return () => clearTimeout(timer);
  }, [resolvedApprovals, onClearResolved]);

  if (pendingApprovals.length === 0 && resolvedApprovals.length === 0) {
    return null;
  }

  return (
    <div className="px-4 py-2 space-y-2" role="region" aria-label="审批通知面板">
      {/* 已解决通知（Toast） */}
      {resolvedApprovals.map((r) => (
        <div
          key={r.request_id}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs animate-fade-in ${
            r.result === "approved"
              ? "bg-green-500/10 border border-green-500/20 text-green-500"
              : r.result === "rejected"
                ? "bg-red-500/10 border border-red-500/20 text-red-500"
                : "bg-amber-500/10 border border-amber-500/20 text-amber-500"
          }`}
          role="alert"
        >
          {r.result === "approved" ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
          <span>
            审批请求 {r.request_id.slice(0, 8)}...{" "}
            {r.result === "approved" ? "已批准" : r.result === "rejected" ? "已拒绝" : "已超时"}
          </span>
        </div>
      ))}

      {/* 待审批请求 */}
      {pendingApprovals.map((request) => (
        <div
          key={request.request_id}
          className="bg-slate-800/40 border border-amber-500/20 rounded-xl px-4 py-3 space-y-2 animate-slide-in"
          role="alert"
          aria-label={`审批请求: ${request.command}`}
        >
          {/* 头部 */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Shield size={14} className="text-amber-500" aria-hidden="true" />
              <span className="text-xs font-medium text-amber-500">命令审批请求</span>
              <span className="text-xs text-muted-foreground font-mono">
                {request.request_id.slice(0, 8)}
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock size={12} aria-hidden="true" />
              <span>来自 {request.session_id}</span>
            </div>
          </div>

          {/* 命令内容 */}
          <div className="flex items-start gap-2 px-3 py-2 rounded bg-slate-900 border border-slate-700">
            <Terminal size={12} className="text-amber-500 mt-0.5 flex-shrink-0" aria-hidden="true" />
            <code className="text-xs font-mono text-amber-500 break-all">
              {request.command}
            </code>
          </div>

          {/* 工作目录 */}
          {request.workspace && (
            <div className="text-xs text-muted-foreground font-mono">
              cwd: {request.workspace}
            </div>
          )}

          {/* 倒计时 */}
          <CountdownBar expiresAt={request.expires_at} />

          {/* 操作按钮 */}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={() => onApprove(request.request_id)}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium
                bg-green-500/20 text-green-500 border border-green-500/30
                hover:bg-green-500/30 transition-all cursor-pointer min-h-[44px]"
              aria-label={`批准命令: ${request.command}`}
            >
              <Check size={14} aria-hidden="true" />
              批准
            </button>
            <button
              onClick={() => onReject(request.request_id)}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium
                bg-red-500/20 text-red-500 border border-red-500/30
                hover:bg-red-500/30 transition-all cursor-pointer min-h-[44px]"
              aria-label={`拒绝命令: ${request.command}`}
            >
              <X size={14} aria-hidden="true" />
              拒绝
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default memo(ApprovalPanel);
