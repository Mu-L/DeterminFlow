import type { Message } from "../../types";
import MessageRenderer from "../MessageRenderer";
import MarkdownContent from "./MarkdownContent";
import ReasoningDisclosure from "./ReasoningDisclosure";

export interface MessageBubbleProps {
  message: Message;
  streaming?: boolean;
  editable?: boolean;
  readonly?: boolean;
  onEdit?: (messageId: string, newContent: string) => void;
  onCommand?: (payload: { type: string; [key: string]: unknown }) => boolean;
  className?: string;
}

function effectiveType(message: Message): string {
  return message.type || message.role || "";
}

export default function MessageBubble({
  message,
  streaming = false,
  editable = false,
  readonly = false,
  onEdit,
  onCommand,
  className = "",
}: MessageBubbleProps) {
  const type = effectiveType(message);

  if (type === "user") {
    return (
      <MessageRenderer
        message={message}
        onEdit={onEdit}
        onCommand={onCommand}
        editable={editable}
        streaming={streaming}
        readonly={readonly}
      />
    );
  }

  if (type === "assistant") {
    return (
      <article className={`flex justify-start ${className}`} aria-label="助手消息">
        <div className="max-w-[85%] space-y-2">
          {message.reasoning_content && (
            <ReasoningDisclosure content={message.reasoning_content} streaming={streaming && !message.content} />
          )}
          {message.content && (
            <div className="rounded-2xl rounded-bl-md border border-slate-700/50 bg-slate-800/50 px-4 py-3">
              <MarkdownContent content={message.content} className="text-sm" />
              {streaming && (
                <span className="ml-1 inline-block h-4 w-2 animate-pulse bg-indigo-400 align-middle motion-reduce:animate-none" aria-hidden="true" />
              )}
            </div>
          )}
        </div>
      </article>
    );
  }

  return <MessageRenderer message={message} readonly={readonly} onCommand={onCommand} />;
}
