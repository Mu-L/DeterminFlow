import { X, MessageCircle } from "lucide-react";
import { Seat } from "../types";
import { getSeatColor } from "../lib/seatColors";

interface SeatCardProps {
  seat: Seat;
  seatIndex: number;
  isDiscussing?: boolean;
  onRemove?: (seatId: string) => void;
  onNominate?: (seatId: string) => void;
}

export default function SeatCard({ seat, seatIndex, isDiscussing, onRemove, onNominate }: SeatCardProps) {
  const color = getSeatColor(seatIndex);
  const isSpeaking = seat.status === "speaking";
  const isThinking = seat.status === "thinking";
  const isDone = seat.status === "done";

  // 确定是否可以操作
  const canNominate = isDiscussing && !isSpeaking && !isThinking && onNominate && !seat.is_moderator;
  const canRemove = onRemove && !isSpeaking;

  return (
    <div
      className={`bg-slate-800/50 border rounded-lg p-3 transition-all duration-300 ${
        isSpeaking
          ? `${color.border} ${color.bg}`
          : isThinking
          ? "border-amber-400/30 bg-amber-400/5"
          : isDone
          ? "border-green-500/20 bg-green-500/5"
          : "border-slate-700/50"
      }`}
      role="article"
      aria-label={`${seat.role_name} - ${getStatusText(seat.status)}${seat.is_moderator ? " (主持人)" : ""}`}
    >
      <div className="flex items-center gap-2">
        {/* 状态指示器 - 使用更大的触摸区域 */}
        <div className="relative w-6 h-6 flex items-center justify-center" aria-hidden="true">
          <span className={`w-3 h-3 rounded-full block ${isThinking ? "bg-amber-400" : isDone ? "bg-green-400" : color.dot}`} />
          {(isSpeaking || isThinking) && (
            <span
              className={`absolute inset-0 w-6 h-6 rounded-full ${isThinking ? "bg-amber-400" : color.dot} animate-ping opacity-75`}
            />
          )}
        </div>

        {/* 角色名 */}
        <span className="text-sm font-medium text-slate-200 flex-1 truncate">
          {seat.role_name}
        </span>

        {/* 状态标签 */}
        <StatusBadge status={seat.status} />
      </div>

      {/* 角色信息 */}
      <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
        <span className="font-mono">T={seat.temperature}</span>
        {seat.is_moderator && (
          <span className="bg-amber-500/20 text-amber-400 px-1.5 py-0.5 rounded text-xs font-medium">
            主持人
          </span>
        )}
        <div className="flex-1" />
        {/* Phase 3: 点名按钮 - 增加触摸目标大小 */}
        {canNominate && (
          <button
            type="button"
            onClick={() => onNominate(seat.seat_id)}
            className="text-slate-500 hover:text-cyan-400 transition-colors cursor-pointer p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
            aria-label={`点名 ${seat.role_name} 发言`}
          >
            <MessageCircle size={14} aria-hidden="true" />
          </button>
        )}
        {/* Phase 3: 移除按钮 - 增加触摸目标大小 */}
        {canRemove && (
          <button
            type="button"
            onClick={() => onRemove(seat.seat_id)}
            className="text-slate-600 hover:text-red-400 transition-colors cursor-pointer p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
            aria-label={`移除 ${seat.role_name}`}
          >
            <X size={14} aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

function getStatusText(status: string): string {
  const map: Record<string, string> = {
    idle: "等待中",
    speaking: "发言中",
    thinking: "思考中",
    done: "已发言",
  };
  return map[status] || "等待中";
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { bg: string; text: string; label: string }> = {
    idle: { bg: "bg-slate-500/20", text: "text-slate-400", label: "等待" },
    speaking: { bg: "bg-green-500/20", text: "text-green-400", label: "发言中" },
    thinking: { bg: "bg-amber-500/20", text: "text-amber-400", label: "思考中" },
    done: { bg: "bg-blue-500/20", text: "text-blue-400", label: "已发言" },
  };

  const c = config[status] || config.idle;

  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded ${c.bg} ${c.text}`}
      role="status"
      aria-label={`状态: ${c.label}`}
      aria-live="polite"
    >
      {c.label}
    </span>
  );
}
