import MarkdownRenderer from "./MarkdownRenderer";

interface StreamingMessageProps {
  content: string;
  showCursor?: boolean;
  isReasoning?: boolean;
}

export default function StreamingMessage({ content, showCursor = true, isReasoning = false }: StreamingMessageProps) {
  if (!content) return null;

  return (
    <div
      className="flex justify-start mb-4"
      role="article"
      aria-label={isReasoning ? "推理过程" : "流式消息"}
    >
      <div className={`max-w-[85%] px-4 py-3 rounded-2xl rounded-bl-md ${isReasoning ? "bg-amber-500/5 border border-amber-500/20" : "bg-slate-800/50 border border-slate-700/50"}`}>
        {isReasoning && (
          <div className="text-xs text-amber-500 font-medium mb-1">推理过程</div>
        )}
        <div className="markdown-body text-sm">
          <MarkdownRenderer content={content} />
          {showCursor && (
            <span
              className="inline-block w-2 h-4 bg-indigo-500/70 animate-pulse motion-reduce:animate-none ml-0.5 align-middle rounded-sm"
              aria-hidden="true"
            />
          )}
        </div>
      </div>
    </div>
  );
}
