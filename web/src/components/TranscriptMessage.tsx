import { TranscriptEntry } from "../types";
import MarkdownRenderer from "./MarkdownRenderer";
import { FileText, CircleCheck } from "lucide-react";
import { getSeatColor } from "../lib/seatColors";

interface TranscriptMessageProps {
  entry: TranscriptEntry;
  seatIndex: number;
  showRoundHeader?: boolean;
}

export default function TranscriptMessage({
  entry,
  seatIndex,
  showRoundHeader,
}: TranscriptMessageProps) {
  const color = getSeatColor(seatIndex);
  const isSummary = entry.entry_type === "summary";
  const isConclusion = entry.entry_type === "conclusion";

  return (
    <div className="mb-4">
      {showRoundHeader && (
        <div className="flex items-center gap-3 my-4" aria-hidden="true">
          <div className="flex-1 h-px bg-slate-700/60" />
          <span className="text-xs text-slate-500 font-medium px-2">
            第 {entry.round_number} 轮
          </span>
          <div className="flex-1 h-px bg-slate-700/60" />
        </div>
      )}

      <div className={`bg-slate-800/50 border border-slate-700/50 rounded-lg p-4 ${
        isConclusion
          ? "border-emerald-500/30 bg-emerald-500/5"
          : isSummary
          ? "border-amber-400/30 bg-amber-400/5"
          : `${color.border} ${color.bg} hover:bg-slate-700/50`
      } transition-colors`} role="article" aria-label={`${entry.speaker_name} 第${entry.round_number}轮发言`}>
        <div className="flex items-center gap-2 mb-2">
          <span className={`w-2 h-2 rounded-full ${
            isConclusion ? "bg-emerald-500" : isSummary ? "bg-amber-400" : color.dot
          }`} aria-hidden="true" />
          <span className={`text-sm font-semibold ${
            isConclusion ? "text-emerald-400" : isSummary ? "text-amber-400" : color.text
          }`}>
            {entry.speaker_name}
          </span>
          <span className="text-xs text-slate-500">
            R{entry.round_number}
          </span>
          {entry.entry_type === "moderator_note" && (
            <span className="text-xs bg-amber-500/20 text-amber-400 px-1.5 py-0.5 rounded">
              主持人
            </span>
          )}
          {isSummary && (
            <span className="text-xs bg-amber-500/20 text-amber-400 px-1.5 py-0.5 rounded flex items-center gap-1">
              <FileText size={12} aria-hidden="true" />
              阶段摘要
            </span>
          )}
          {isConclusion && (
            <span className="text-xs bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded flex items-center gap-1">
              <CircleCheck size={12} aria-hidden="true" />
              会议结论
            </span>
          )}
        </div>

        <MarkdownRenderer content={entry.content} className="prose-sm" />
      </div>
    </div>
  );
}
