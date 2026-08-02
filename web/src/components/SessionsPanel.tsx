import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Trash2, X, Plus, ChevronDown, ChevronRight, Zap } from "lucide-react";
import { Session } from "../types";
import { getStatusConfig, formatRelativeTime, truncate } from "../lib/utils-helpers";
import { useAgentTypes } from "../hooks/useAgentTypes";

interface SessionsPanelProps {
  sessions: Session[];
  viewingSessionId: string | null;
  mainSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string, e: React.MouseEvent) => void;
  onKillSession: (sessionId: string, e: React.MouseEvent) => void;
  onCreateSession: (agentType?: string) => void;
}

function isWorkflowMain(session: Session): boolean {
  return session.type === "main" && (session.task || "").startsWith("Workflow:");
}

const AGENT_TYPE_LABELS: Record<string, string> = {
  main: "通用助手",
  coder: "编码助手",
  reviewer: "审查助手",
  researcher: "研究助手",
  reader: "阅读助手",
  default: "默认助手",
};

function SessionCard({
  session, isViewing, isSub, canDelete, canKill,
  onViewSession, onDeleteSession, onKillSession,
}: {
  session: Session; isViewing: boolean;
  isSub: boolean; canDelete: boolean; canKill: boolean;
  onViewSession: (id: string) => void;
  onDeleteSession: (id: string, e: React.MouseEvent) => void;
  onKillSession: (id: string, e: React.MouseEvent) => void;
}) {
  const cfg = getStatusConfig(session.status);
  const wfMain = isWorkflowMain(session);
  const label = session.type === "main"
    ? (wfMain ? "WF-MAIN" : "MAIN")
    : "SUB";

  return (
    <div
      key={session.session_id}
      role="button"
      tabIndex={0}
      onClick={() => onViewSession(session.session_id)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onViewSession(session.session_id); } }}
      aria-label={`${session.type === "main" ? "主会话" : "子会话"} ${session.session_id}，${session.task || ""}`}
      className={`bg-slate-800/50 border border-slate-700/50 rounded-lg transition-all cursor-pointer group relative ${
        isSub ? "px-1.5 py-1 ml-4" : "px-3 py-2.5"
      } ${
        isViewing
          ? "border-indigo-500/60 bg-indigo-500/10 shadow-lg shadow-indigo-500/10"
          : "hover:border-indigo-500/30"
      }`}
    >
      {isViewing && (
        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-indigo-500 rounded-r" />
      )}

      <div className={`flex items-center justify-between ${isSub ? "mb-0.5" : "mb-1"}`}>
        <div className="flex items-center gap-1.5">
          <span className={`inline-block w-2 h-2 rounded-full ${cfg.dotColor}`} aria-hidden="true" />
          <span className={`font-mono text-cyan-400 text-xs`}>
            {session.session_id}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isViewing && (
            <Badge variant="outline" className="text-xs text-indigo-400 border-indigo-500/30">查看中</Badge>
          )}
          <Badge
            variant="outline"
            className={`text-xs ${wfMain ? "text-purple-400 border-purple-500/30" : cfg.color} border-current/30`}
          >
            {label}
          </Badge>
        </div>
      </div>

      <div className={`flex items-center gap-1.5 text-muted-foreground text-xs ${isSub ? "mb-0" : "mb-1"}`}>
        {session.agent_type && session.agent_type !== "main" && (
          <Badge variant="outline" className="text-xs text-cyan-400 border-cyan-500/30">
            {session.agent_type}
          </Badge>
        )}
      </div>

      <p className={`text-muted-foreground text-xs`}>
        {truncate(session.task || (session.type === "main" ? "主会话" : ""), isSub ? 30 : 60)}
      </p>

      <div className={`flex items-center justify-between text-muted-foreground text-xs ${isSub ? "mt-0.5" : "mt-1.5"}`}>
        <span>{session.message_count} 条消息</span>
        <div className="flex items-center gap-1">
          <span>{formatRelativeTime(session.updated_at)}</span>
          {canKill && (
            <button
              type="button"
              onClick={(e) => onKillSession(session.session_id, e)}
              aria-label={`终止会话 ${session.session_id}`}
              className="ml-1 p-0.5 rounded text-amber-400 hover:bg-amber-500/20 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            >
              <X size={12} />
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              onClick={(e) => onDeleteSession(session.session_id, e)}
              aria-label={`删除会话 ${session.session_id}`}
              className="p-0.5 rounded text-red-400 hover:bg-red-500/20 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            >
              <Trash2 size={12} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function SessionsPanel({
  sessions, viewingSessionId, mainSessionId,
  onViewSession, onDeleteSession, onKillSession, onCreateSession,
}: SessionsPanelProps) {
  const [collapsedMains, setCollapsedMains] = useState<Set<string>>(new Set());
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const { agentTypes } = useAgentTypes({ endpoint: "/api/agent-types", filterSubSessionOnly: true });
  const dropdownRef = useRef<HTMLDivElement>(null);
  const collapseInitializedRef = useRef(false);

  // 首次加载 sessions 后，默认折叠所有有子会话的 main
  useEffect(() => {
    if (collapseInitializedRef.current) return;
    const mains = sessions.filter(s => s.type === "main");
    const subs = sessions.filter(s => s.type === "sub");
    const mainIdsWithSubs = new Set(
      mains.filter(m => subs.some(sub => sub.parent_id === m.session_id)).map(m => m.session_id)
    );
    if (mainIdsWithSubs.size > 0) {
      setCollapsedMains(mainIdsWithSubs);
      collapseInitializedRef.current = true;
    }
  }, [sessions]);

  // 外部点击关闭下拉菜单 + Escape 关闭
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDropdownOpen(false);
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKey);
    };
  }, [dropdownOpen]);

  // 按 main 分组
  const groups = useMemo(() => {
    const mains = sessions.filter(s => s.type === "main");
    const subs = sessions.filter(s => s.type === "sub");
    return mains.map(main => ({
      main,
      subs: subs.filter(s => s.parent_id === main.session_id),
    }));
  }, [sessions]);

  const toggleCollapse = (mainId: string, e: React.SyntheticEvent) => {
    e.stopPropagation();
    setCollapsedMains(prev => {
      const next = new Set(prev);
      if (next.has(mainId)) next.delete(mainId);
      else next.add(mainId);
      return next;
    });
  };

  const handleCreateWithType = useCallback((agentType: string) => {
    onCreateSession(agentType);
    setDropdownOpen(false);
  }, [onCreateSession]);

  return (
    <ScrollArea className="h-full">
      <div className="px-3 py-2 space-y-2">
        {/* Split Button */}
        <div className="relative" ref={dropdownRef}>
          <div className="flex rounded-lg overflow-hidden">
            {/* 左侧主按钮 */}
            <button
              type="button"
              onClick={() => { onCreateSession("main"); setDropdownOpen(false); }}
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 transition-colors text-xs font-medium cursor-pointer"
            >
              <Plus size={14} />
              新建会话
            </button>
            {/* 右侧下拉触发按钮 */}
            <button
              type="button"
              onClick={() => setDropdownOpen((prev) => !prev)}
              aria-haspopup="menu"
              aria-expanded={dropdownOpen}
              aria-label="选择会话类型"
              className="px-2 py-2 bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 transition-colors border-l border-indigo-500/30 cursor-pointer"
            >
              <ChevronDown size={14} className={`transition-transform ${dropdownOpen ? "rotate-180" : ""}`} />
            </button>
          </div>

          {/* 下拉菜单 */}
          {dropdownOpen && (
            <div className="absolute left-0 right-0 mt-1 z-50 bg-slate-800 border border-border/60 rounded-lg shadow-xl py-1 max-h-64 overflow-y-auto" role="menu" aria-label="选择会话类型">
              {agentTypes.map((t) => (
                <button
                  key={t.agent_type}
                  onClick={() => handleCreateWithType(t.agent_type)}
                  role="menuitem"
                  className="w-full flex items-start gap-3 px-3 py-2 text-left hover:bg-indigo-500/10 transition-colors cursor-pointer"
                >
                  <Zap size={14} className="mt-0.5 text-indigo-400 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-200">
                      {AGENT_TYPE_LABELS[t.agent_type] || t.agent_type}
                    </div>
                    <div className="text-xs text-muted-foreground truncate">
                      {t.description || t.agent_type}
                    </div>
                  </div>
                </button>
              ))}
              {agentTypes.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted-foreground text-center">
                  暂无可用类型
                </div>
              )}
            </div>
          )}
        </div>

        {groups.map(({ main, subs }) => {
          const isViewing = viewingSessionId === main.session_id;
          const canDelete = main.session_id !== mainSessionId && main.status !== "running";
          const canKill = false; // main sessions not killable via this button
          const isCollapsed = collapsedMains.has(main.session_id);

          return (
            <div key={main.session_id} className="space-y-1">
              {/* Main card with collapse toggle */}
              <div className="flex items-start gap-1">
                <button
                  type="button"
                  onClick={(e) => toggleCollapse(main.session_id, e)}
                  aria-expanded={!isCollapsed}
                  aria-label={isCollapsed ? `展开 ${subs.length} 个子会话` : "折叠子会话"}
                  className="mt-2 p-0.5 rounded hover:bg-slate-800 transition-colors cursor-pointer flex-shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center"
                >
                  {subs.length > 0 && (
                    isCollapsed ? <ChevronRight size={12} className="text-muted-foreground" />
                              : <ChevronDown size={12} className="text-muted-foreground" />
                  )}
                </button>
                <div className="flex-1">
                  <SessionCard
                    session={main}
                    isViewing={isViewing}
                    isSub={false}
                    canDelete={canDelete}
                    canKill={canKill}
                    onViewSession={onViewSession}
                    onDeleteSession={onDeleteSession}
                    onKillSession={onKillSession}
                  />
                </div>
              </div>

              {/* Collapsed subs: 堆叠指示器 */}
              {isCollapsed && subs.length > 0 && (
                <div
                  className="relative ml-4 cursor-pointer group"
                  role="button"
                  tabIndex={0}
                  onClick={(e) => toggleCollapse(main.session_id, e)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleCollapse(main.session_id, e); } }}
                  aria-label={`展开 ${subs.length} 个子会话`}
                >
                  {/* 3 层堆叠卡片 */}
                  <div className="relative h-6">
                    <div className="absolute inset-x-0 top-0 h-[20px] rounded-lg border border-indigo-500/15 bg-slate-800/60 z-30" />
                    <div className="absolute left-[3px] right-[3px] top-[2px] h-[18px] rounded-lg border border-indigo-500/10 bg-slate-800/40 z-20" />
                    <div className="absolute left-[6px] right-[6px] top-[4px] h-[16px] rounded-lg border border-indigo-500/5 bg-slate-800/20 z-10" />
                  </div>
                  {/* +N 徽章 */}
                  <Badge variant="outline" className="absolute -right-1 top-1/2 -translate-y-1/2 text-xs text-indigo-400 border-indigo-500/30 bg-slate-900/80">
                    +{subs.length}
                  </Badge>
                </div>
              )}

              {/* 展开的子会话 */}
              {!isCollapsed && subs.map(sub => {
                const subViewing = viewingSessionId === sub.session_id;
                const canKillSub = sub.status === "running" || sub.status === "waiting" || sub.status === "streaming";
                const subCanDelete = sub.status !== "running";
                return (
                  <SessionCard
                    key={sub.session_id}
                    session={sub}
                    isViewing={subViewing}
                    isSub={true}
                    canDelete={subCanDelete}
                    canKill={canKillSub}
                    onViewSession={onViewSession}
                    onDeleteSession={onDeleteSession}
                    onKillSession={onKillSession}
                  />
                );
              })}
            </div>
          );
        })}

        {sessions.length === 0 && (
          <div className="text-center text-muted-foreground text-sm py-4">暂无会话</div>
        )}
      </div>
    </ScrollArea>
  );
}
