import { useState } from "react";
import { CompressionEventData } from "../types";
import MarkdownRenderer from "./MarkdownRenderer";
import { Package, Wrench, Trash2, Pin, ChevronUp, ChevronDown, type LucideProps } from "lucide-react";

interface CompressionDividerProps {
  event: CompressionEventData;
  strategy?: string;  // 消息级别的 strategy 字段（"full" / "micro" / "reactive"）
}

const LABELS: Record<CompressionEventData["type"], string> = {
  full: "以上消息已被压缩",
  micro: "工具结果已被压缩",
  reactive: "早期消息已被压缩",
};

const ICONS: Record<CompressionEventData["type"], React.ComponentType<LucideProps>> = {
  full: Package,
  micro: Wrench,
  reactive: Trash2,
};

export default function CompressionDivider({ event, strategy }: CompressionDividerProps) {
  const [expanded, setExpanded] = useState(false);
  const { summary, original_count, compressed_count } = event;
  const compressionType = (strategy || event.type || "") as CompressionEventData["type"];
  const hasSummary = compressionType === "full" && summary;
  const removed = original_count - compressed_count;

  return (
    <div className="flex items-center gap-2 my-4" role="separator" aria-label="压缩分隔线">
      {/* 分割线 */}
      <div className="flex-1 h-px bg-slate-700/60" />

      {/* 提示卡片 */}
      <div className="flex-shrink-0 max-w-[85%]">
        <div className="bg-slate-800/80 border border-slate-700/50 rounded-lg px-4 py-2">
          <div className="flex items-center gap-2">
            {(() => {
              const IconComponent = ICONS[compressionType] || Pin;
              return <IconComponent size={16} className="text-slate-400" aria-hidden="true" />;
            })()}
            <span className="text-xs text-slate-400">
              {LABELS[compressionType] || "上下文已压缩"}
            </span>
            {removed > 0 && (
              <span className="text-xs text-slate-500">
                (−{removed} 条消息)
              </span>
            )}
            {hasSummary && (
              <button
                type="button"
                onClick={() => setExpanded(!expanded)}
                className="ml-1 flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-400 transition-colors cursor-pointer min-h-[44px]"
                aria-expanded={expanded}
                aria-label={expanded ? "收起摘要" : "查看摘要"}
              >
                {expanded ? (
                  <>收起摘要 <ChevronUp size={12} aria-hidden="true" /></>
                ) : (
                  <>查看摘要 <ChevronDown size={12} aria-hidden="true" /></>
                )}
              </button>
            )}
          </div>

          {/* 展开的摘要内容 */}
          {expanded && hasSummary && (
            <div className="mt-3 pt-3 border-t border-slate-700/40">
              <div className="text-xs text-slate-500 mb-1">
                Compressor Agent 生成的摘要（已注入到主 Agent 上下文）：
              </div>
              <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                <MarkdownRenderer content={summary} className="text-xs" />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 分割线 */}
      <div className="flex-1 h-px bg-slate-700/60" />
    </div>
  );
}
