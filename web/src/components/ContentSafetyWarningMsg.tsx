import { useState } from "react";
import { Message } from "../types";
import { AlertTriangle, Search, Loader2 } from "lucide-react";

/**
 * 内容安全警告组件
 *
 * 当 DeepSeek API 返回 Content Exists Risk 时，在会话中展示警告信息，
 * 并提供"运行详细诊断"按钮让用户主动触发二分排除诊断。
 */
export default function ContentSafetyWarningMsg({ message }: { message: Message }) {
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosed, setDiagnosed] = useState(false);

  const sessionId = message.session_id || "";

  const handleDiagnose = () => {
    if (diagnosing || diagnosed || !sessionId) return;
    setDiagnosing(true);

    // 创建临时 WebSocket 连接发送诊断请求
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/chat?session_id=${encodeURIComponent(sessionId)}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "diagnose_content_safety",
          session_id: sessionId,
        })
      );
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "chain_end" || data.type === "error") {
          ws.close();
          setDiagnosing(false);
          setDiagnosed(true);
        }
      } catch {
        // 忽略解析错误
      }
    };

    ws.onerror = () => {
      ws.close();
      setDiagnosing(false);
    };

    // 超时保护（30 秒）
    setTimeout(() => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
        setDiagnosing(false);
      }
    }, 30000);
  };

  const errorMessage = message.content || "请求被 DeepSeek 安全审查拦截";
  const errorDetail = message.detail || "";

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

          {/* 操作按钮 */}
          {!diagnosed ? (
            <button
              type="button"
              onClick={handleDiagnose}
              disabled={diagnosing || !sessionId}
              aria-label={diagnosing ? "正在诊断中" : "运行详细诊断"}
              className={`
                flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                transition-all duration-200 cursor-pointer min-h-[44px]
                ${
                  diagnosing
                    ? "bg-amber-500/10 text-amber-400/50 cursor-wait"
                    : "bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/30"
                }
              `}
            >
              {diagnosing ? (
                <>
                  <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  诊断中...
                </>
              ) : (
                <>
                  <Search size={14} aria-hidden="true" />
                  运行详细诊断
                </>
              )}
            </button>
          ) : (
            <p className="text-xs text-slate-500">诊断已完成，请在下方查看结果</p>
          )}
        </div>
      </div>

      {/* 分割线 */}
      <div className="flex-1 h-px bg-amber-500/30" />
    </div>
  );
}
