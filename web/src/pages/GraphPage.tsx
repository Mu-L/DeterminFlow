import { useState } from "react";
import { useSessions } from "../hooks/useSessions";
import { useSystemStatus } from "../hooks/useSystemStatus";
import SessionGraphView from "../components/SessionGraphView";
import SessionDetailPanel from "../components/SessionDetailPanel";
import LangGraphView from "../components/LangGraphView";
import { SessionDetail } from "../types";
import { fetchSessionDetail } from "../lib/api";
import { GitBranch, Network, Loader2 } from "lucide-react";

export default function GraphPage() {
  const { sessions } = useSessions();
  const { graphStructure } = useSystemStatus();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<SessionDetail | null>(null);
  const [viewMode, setViewMode] = useState<"sessions" | "langgraph">("sessions");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const mainSession = sessions.find((s) => s.type === "main");

  const handleNodeClick = async (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const detail = await fetchSessionDetail(sessionId);
      setSelectedDetail(detail);
    } catch {
      setDetailError("加载会话详情失败，请重试");
      setSelectedDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div className="h-[calc(100dvh-3.5rem)] flex flex-col">
      {/* View Mode Toggle */}
      <div
        className="px-4 py-2 border-b border-border flex items-center gap-4"
        role="toolbar"
        aria-label="视图切换"
      >
        <div className="flex gap-1" role="tablist" aria-label="图谱视图">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "sessions"}
            aria-controls="panel-sessions"
            onClick={() => setViewMode("sessions")}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors duration-200 cursor-pointer min-h-[44px] flex items-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 ${
              viewMode === "sessions"
                ? "bg-indigo-500/20 text-indigo-400 border border-indigo-500/30"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <GitBranch size={14} className="inline mr-1.5" aria-hidden="true" />
            会话图谱
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "langgraph"}
            aria-controls="panel-langgraph"
            onClick={() => setViewMode("langgraph")}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors duration-200 cursor-pointer min-h-[44px] flex items-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${
              viewMode === "langgraph"
                ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Network size={14} className="inline mr-1.5" aria-hidden="true" />
            LangGraph 结构
          </button>
        </div>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {viewMode === "sessions"
            ? `${sessions.length} 个会话 · 点击节点查看详情`
            : "Main Agent + Sub Agent 图结构"}
        </span>
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        {viewMode === "sessions" ? (
          <div id="panel-sessions" role="tabpanel" aria-label="会话图谱面板" className="flex-1 flex flex-col min-h-0">
            {/* Session Graph - Upper Half */}
            <section
              aria-label="会话关系图谱"
              className={`${selectedDetail ? "h-[45%]" : "flex-1"} border-b border-border`}
            >
              <SessionGraphView
                sessions={sessions}
                mainSessionId={mainSession?.session_id || null}
                onNodeClick={handleNodeClick}
                selectedSessionId={selectedSessionId}
              />
            </section>

            {/* Session Detail - Lower Half */}
            {detailLoading && (
              <div className="flex-1 flex items-center justify-center" role="status">
                <Loader2 size={20} className="animate-spin motion-reduce:animate-none text-muted-foreground" aria-hidden="true" />
                <span className="ml-2 text-sm text-muted-foreground">加载会话详情...</span>
                <span className="sr-only">正在加载会话详情</span>
              </div>
            )}
            {detailError && (
              <div className="flex-1 flex items-center justify-center gap-3" role="alert">
                <span className="text-sm text-red-400">{detailError}</span>
                <button
                  type="button"
                  onClick={() => selectedSessionId && handleNodeClick(selectedSessionId)}
                  className="text-xs px-3 py-1 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 cursor-pointer min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
                  aria-label="重新加载会话详情"
                >
                  重试
                </button>
              </div>
            )}
            {!detailLoading && !detailError && selectedDetail && (
              <div className="flex-1 min-h-0">
                <SessionDetailPanel session={selectedDetail} />
              </div>
            )}
          </div>
        ) : (
          /* LangGraph Structure View */
          <div id="panel-langgraph" role="tabpanel" aria-label="LangGraph 结构面板" className="flex-1">
            {graphStructure ? (
              <LangGraphView
                mainGraph={graphStructure.main_graph}
                subGraph={graphStructure.sub_graph}
              />
            ) : (
              <div
                className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground"
                role="status"
              >
                <Loader2 size={20} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                <span className="text-sm">加载 LangGraph 结构...</span>
                <span className="sr-only">正在加载 LangGraph 结构</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
