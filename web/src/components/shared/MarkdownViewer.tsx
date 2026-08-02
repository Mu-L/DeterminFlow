/**
 * MarkdownViewer — 可复用内联 Markdown 查看器
 *
 * 功能：
 * - MD 渲染（react-markdown + remark-gfm）
 * - 渲染/原文切换
 * - 字号大小调整（localStorage 持久化）
 *
 * Props:
 * - content: Markdown 内容字符串
 * - fileName?: 文件名（可选，标题栏显示）
 * - height?: 容器高度（默认 400px）
 */
import { useState, useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Eye, Code, Plus, Minus } from "lucide-react";

const FONT_SIZE_STORAGE_KEY = "md-viewer-font-size";
const DEFAULT_FONT_SIZE = 14;
const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 24;

function loadFontSize(): number {
  try {
    const saved = localStorage.getItem(FONT_SIZE_STORAGE_KEY);
    if (saved) {
      const n = parseInt(saved, 10);
      if (n >= MIN_FONT_SIZE && n <= MAX_FONT_SIZE) return n;
    }
  } catch { /* noop */ }
  return DEFAULT_FONT_SIZE;
}

function saveFontSize(size: number) {
  try {
    localStorage.setItem(FONT_SIZE_STORAGE_KEY, String(size));
  } catch { /* noop */ }
}

interface MarkdownViewerProps {
  content: string;
  fileName?: string;
  height?: string;
}

export default function MarkdownViewer({
  content,
  fileName,
  height = "400px",
}: MarkdownViewerProps) {
  const [previewMode, setPreviewMode] = useState<"rendered" | "raw">("rendered");
  const [fontSize, setFontSize] = useState(loadFontSize);

  const increaseFontSize = useCallback(() => {
    setFontSize((prev) => {
      const next = Math.min(prev + 2, MAX_FONT_SIZE);
      saveFontSize(next);
      return next;
    });
  }, []);

  const decreaseFontSize = useCallback(() => {
    setFontSize((prev) => {
      const next = Math.max(prev - 2, MIN_FONT_SIZE);
      saveFontSize(next);
      return next;
    });
  }, []);

  const markdownBody = useMemo(() => {
    if (previewMode === "raw") {
      return (
        <pre
          className="font-mono text-foreground whitespace-pre-wrap break-words leading-relaxed p-0 m-0 select-text"
          style={{ fontSize }}
        >
          {content}
        </pre>
      );
    }
    return (
      <div
        className="prose prose-invert prose-sm max-w-none
          prose-headings:text-foreground prose-headings:font-semibold
          prose-p:text-muted-foreground prose-p:leading-relaxed
          prose-code:text-purple-400 prose-code:bg-muted/60 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[0.9em]
          prose-pre:bg-background prose-pre:border prose-pre:border-border/10 prose-pre:rounded-lg
          prose-blockquote:border-l-primary/60 prose-blockquote:text-muted-foreground prose-blockquote:bg-primary/5 prose-blockquote:py-1 prose-blockquote:px-3 prose-blockquote:rounded-r
          prose-li:text-muted-foreground prose-li:leading-relaxed
          prose-table:border-collapse prose-th:bg-muted prose-th:text-foreground prose-th:px-3 prose-th:py-2 prose-th:border prose-th:border-border/10 prose-th:text-xs prose-td:border prose-td:border-border/10 prose-td:px-3 prose-td:py-2 prose-td:text-sm prose-td:text-muted-foreground
          prose-a:text-indigo-400 prose-a:no-underline hover:prose-a:underline
          prose-strong:text-foreground
          prose-hr:border-border/20
        "
        style={{ fontSize }}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  }, [content, previewMode, fontSize]);

  return (
    <div className="flex flex-col rounded-lg border border-border/10 bg-background overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border/10 bg-muted/50">
        <span className="text-xs text-muted-foreground truncate max-w-[60%]">
          {fileName || "Markdown 查看器"}
        </span>
        <div className="flex items-center gap-1">
          {/* 字号调整 */}
          <button
            onClick={decreaseFontSize}
            disabled={fontSize <= MIN_FONT_SIZE}
            title="缩小字号"
            aria-label="缩小字号"
            className="p-1 rounded hover:bg-primary/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Minus className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
          <span
            className="text-[10px] text-muted-foreground w-8 text-center select-none"
            aria-label={`当前字号: ${fontSize}px`}
          >
            {fontSize}px
          </span>
          <button
            onClick={increaseFontSize}
            disabled={fontSize >= MAX_FONT_SIZE}
            title="放大字号"
            aria-label="放大字号"
            className="p-1 rounded hover:bg-primary/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Plus className="w-3.5 h-3.5 text-muted-foreground" />
          </button>

          <div className="w-px h-4 bg-border/10 mx-1" />

          {/* 渲染/原文切换 */}
          <button
            onClick={() =>
              setPreviewMode(previewMode === "rendered" ? "raw" : "rendered")
            }
            title={previewMode === "rendered" ? "查看原文" : "渲染预览"}
            aria-label={previewMode === "rendered" ? "查看原文" : "渲染预览"}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-muted-foreground hover:bg-primary/10 hover:text-foreground transition-colors"
          >
            {previewMode === "rendered" ? (
              <>
                <Code className="w-3 h-3" />
                <span>原文</span>
              </>
            ) : (
              <>
                <Eye className="w-3 h-3" />
                <span>渲染</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Content */}
      <div
        className="overflow-y-auto p-4"
        style={{ height, maxHeight: height }}
        role="document"
        aria-label="Markdown 内容"
      >
        {content ? (
          markdownBody
        ) : (
          <p className="text-sm text-muted-foreground italic">暂无内容</p>
        )}
      </div>
    </div>
  );
}
