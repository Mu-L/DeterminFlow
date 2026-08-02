import { useState, useEffect, useRef } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";
import MarkdownRenderer from "./MarkdownRenderer";

interface ThinkingChainProps {
  content: string;
  isStreaming?: boolean;
  autoCollapse?: boolean;
}

export default function ThinkingChain({
  content,
  isStreaming = false,
  autoCollapse = true
}: ThinkingChainProps) {
  const [isExpanded, setIsExpanded] = useState(isStreaming);
  const hasCollapsedRef = useRef(false);
  const contentId = useRef(`thinking-content-${Math.random().toString(36).slice(2)}`);

  // 当思考结束时自动折叠（只折叠一次）
  useEffect(() => {
    if (!isStreaming && autoCollapse && content && !hasCollapsedRef.current) {
      const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const delay = prefersReducedMotion ? 0 : 500;
      const timer = setTimeout(() => {
        setIsExpanded(false);
        hasCollapsedRef.current = true;
      }, delay); // reduced-motion 下立即折叠
      return () => clearTimeout(timer);
    }
  }, [isStreaming, autoCollapse, content]);

  if (!content) return null;

  return (
    <div className="mb-3">
      <div className="bg-slate-800/80 border border-purple-500/20 rounded-lg overflow-hidden">
        {/* 标题栏 */}
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={isExpanded}
          aria-controls={contentId.current}
          aria-label={isExpanded ? "折叠思考过程" : "展开思考过程"}
          className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-purple-500/10 focus-visible:ring-2 focus-visible:ring-purple-500/30 transition-colors duration-200 cursor-pointer min-h-[44px]"
        >
          <Brain size={16} className="text-purple-500 flex-shrink-0" aria-hidden="true" />
          <span className="text-sm font-medium text-purple-400">思考过程</span>
          {isStreaming && (
            <span className="text-xs text-purple-400/60 animate-pulse motion-reduce:animate-none" role="status" aria-label="正在思考中">
              思考中...
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted-foreground tabular-nums">
              {content.length} 字符
            </span>
            {isExpanded ? (
              <ChevronDown size={16} className="text-muted-foreground" aria-hidden="true" />
            ) : (
              <ChevronRight size={16} className="text-muted-foreground" aria-hidden="true" />
            )}
          </div>
        </button>

        {/* 思考内容 */}
        {isExpanded && (
          <div
            id={contentId.current}
            role="region"
            aria-label="思考内容"
            className="px-3 py-2 border-t border-purple-500/20 bg-purple-500/5"
          >
            <div className="prose prose-invert prose-sm max-w-none
              prose-headings:text-purple-400 prose-headings:text-xs prose-headings:font-semibold prose-headings:mt-2 prose-headings:mb-1
              prose-p:text-xs prose-p:text-slate-400 prose-p:leading-relaxed prose-p:my-1
              prose-li:text-xs prose-li:text-slate-400 prose-li:my-0.5
              prose-strong:text-amber-500 prose-strong:font-semibold
              prose-code:text-cyan-400 prose-code:text-xs prose-code:bg-slate-800/60 prose-code:px-1 prose-code:py-0.5 prose-code:rounded
              prose-hr:border-border/50 prose-hr:my-1.5
              prose-ul:my-1 prose-ol:my-1">
              <MarkdownRenderer content={content} />
              {isStreaming && (
                <span className="inline-block w-1.5 h-3 bg-purple-500/70 animate-pulse motion-reduce:animate-none ml-0.5 align-middle rounded-sm" aria-hidden="true" />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
