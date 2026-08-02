import { Brain, Plus, Trash2, Users } from "lucide-react";

export function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-300 font-mono">{value}</span>
    </div>
  );
}

export function SessionStatusBadge({ status }: { status: string }) {
  const configs: Record<string, { bg: string; text: string; label: string }> = {
    waiting: { bg: "bg-slate-500/20", text: "text-slate-400", label: "等待中" },
    discussing: { bg: "bg-green-500/20", text: "text-green-400", label: "讨论中" },
    paused: { bg: "bg-amber-500/20", text: "text-amber-400", label: "已暂停" },
    ended: { bg: "bg-blue-500/20", text: "text-blue-400", label: "已结束" },
  };
  const config = configs[status] || configs.waiting;
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${config.bg} ${config.text}`} role="status" aria-label={`状态: ${config.label}`}>
      {config.label}
    </span>
  );
}

export function StrategyBadge({ strategy }: { strategy: string }) {
  if (strategy === "moderator_decides") {
    return (
      <span className="text-xs px-2 py-0.5 rounded bg-amber-500/20 text-amber-400 flex items-center gap-1">
        <Brain size={12} aria-hidden="true" />
        智能主持
      </span>
    );
  }
  return <span className="text-xs px-2 py-0.5 rounded bg-slate-500/20 text-slate-400">轮询模式</span>;
}

interface RoundtableSummary {
  session_id: string;
  topic: string;
  status: string;
  seat_count: number;
  created_at: string;
  strategy: string;
}

interface EmptyStateProps {
  onShowCreate: () => void;
  roundtables: RoundtableSummary[];
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export function EmptyState({ onShowCreate, roundtables, onSelect, onDelete }: EmptyStateProps) {
  return (
    <div className="py-8">
      <div className="text-center mb-8">
        <div className="w-16 h-16 rounded-2xl bg-indigo-600 flex items-center justify-center mx-auto mb-4">
          <Users size={32} className="text-white" aria-hidden="true" />
        </div>
        <h2 className="text-lg font-semibold text-slate-200 mb-2">圆桌会议</h2>
        <p className="text-sm text-slate-400 mb-4">创建多角色 AI 讨论会议，让不同视角碰撞出火花</p>
        <button
          type="button"
          onClick={onShowCreate}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-500 transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500/30 cursor-pointer min-h-[44px]"
        >
          <Plus size={18} aria-hidden="true" />
          创建圆桌会议
        </button>
      </div>

      {roundtables.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-400 mb-3">历史会议</h3>
          <div className="space-y-2" role="list" aria-label="历史会议列表">
            {roundtables.map((roundtable) => (
              <div
                key={roundtable.session_id}
                className="bg-slate-800/80 border border-slate-700 rounded-lg p-3 flex items-center gap-3 cursor-pointer hover:bg-slate-700/50 transition-colors focus-visible:outline-2 focus-visible:outline-indigo-500 focus-visible:outline-offset-2"
                role="listitem"
                tabIndex={0}
                onClick={() => onSelect(roundtable.session_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(roundtable.session_id);
                  }
                }}
                aria-label={`打开历史会议: ${roundtable.topic}`}
              >
                <Users size={14} className="text-slate-500 flex-shrink-0" aria-hidden="true" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-slate-300 truncate">{roundtable.topic}</p>
                  <p className="text-xs text-slate-500">
                    {roundtable.seat_count} 个席位 · {roundtable.status}
                    {roundtable.strategy === "moderator_decides" && " · 智能主持"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(roundtable.session_id);
                  }}
                  aria-label={`删除会议: ${roundtable.topic}`}
                  className="text-slate-600 hover:text-red-400 transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
