import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

/**
 * 统一的 Markdown 渲染组件
 * 使用相同的样式配置，确保整个应用中 markdown 显示一致
 */
function MarkdownRenderer({ content, className = "" }: MarkdownRendererProps) {
  return (
    <div
      className={`markdown-body prose prose-invert max-w-none
        prose-headings:text-slate-100 prose-headings:font-semibold
        prose-p:text-slate-300 prose-p:leading-relaxed
        prose-a:text-cyan-500 prose-a:no-underline hover:prose-a:underline
        prose-strong:text-slate-200 prose-strong:font-semibold
        prose-code:text-cyan-500 prose-code:text-sm prose-code:bg-slate-800/60 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
        prose-pre:bg-slate-900 prose-pre:border prose-pre:border-slate-700/50 prose-pre:rounded-lg
        prose-ul:text-slate-300 prose-ol:text-slate-300
        prose-li:text-slate-300 prose-li:my-1
        prose-blockquote:border-l-0 prose-blockquote:bg-indigo-500/5 prose-blockquote:border prose-blockquote:border-indigo-500/20 prose-blockquote:rounded-lg prose-blockquote:text-slate-400
        prose-hr:border-slate-700/50
        prose-table:text-slate-300
        prose-th:text-slate-200 prose-th:bg-slate-800/60
        prose-td:border-slate-700/50
        ${className}
      `}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownRenderer);
