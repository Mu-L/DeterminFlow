import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import type { Message, StreamingSegment } from "../../types";
import ConversationAsyncState from "./ConversationAsyncState";
import MessageBubble from "./MessageBubble";
import ReasoningDisclosure from "./ReasoningDisclosure";
import ToolInvocation from "./ToolInvocation";
import {
  getTimelineContentVersion,
  normalizeConversationTimeline,
} from "./conversationModel";
import { useAutoFollowOutput } from "./useAutoFollowOutput";

export interface ConversationTimelineProps {
  messages: Message[];
  streamingSegments?: StreamingSegment[];
  isStreaming?: boolean;
  loading?: boolean;
  error?: Error | string | null;
  onRetry?: () => void;
  emptyState?: ReactNode;
  conversationId?: string | null;
  ariaLabel?: string;
  className?: string;
  contentClassName?: string;
  followThreshold?: number;
  readonly?: boolean;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onCommand?: (payload: { type: string; [key: string]: unknown }) => boolean;
  isMessageEditable?: (message: Message) => boolean;
}

function errorMessage(error: Error | string): string {
  if (typeof error === "string") return error;
  return error.message || "消息加载失败";
}

export default function ConversationTimeline({
  messages,
  streamingSegments = [],
  isStreaming = false,
  loading = false,
  error = null,
  onRetry,
  emptyState,
  conversationId,
  ariaLabel = "会话消息",
  className = "",
  contentClassName = "",
  followThreshold = 160,
  readonly = false,
  onEditMessage,
  onCommand,
  isMessageEditable,
}: ConversationTimelineProps) {
  const entries = useMemo(
    () => normalizeConversationTimeline(messages, streamingSegments),
    [messages, streamingSegments],
  );
  const contentVersion = useMemo(() => getTimelineContentVersion(entries), [entries]);
  const viewportRef = useRef<HTMLDivElement>(null);
  const { scrollToBottom, resetAutoFollow } = useAutoFollowOutput(viewportRef, {
    threshold: followThreshold,
  });

  useEffect(() => {
    scrollToBottom();
  }, [contentVersion, isStreaming, scrollToBottom]);

  useEffect(() => {
    resetAutoFollow();
  }, [conversationId, resetAutoFollow]);

  const isInitialLoading = loading && entries.length === 0;
  const isInitialError = !!error && entries.length === 0;
  const isEmpty = !loading && !error && entries.length === 0;

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${className}`} aria-busy={loading || isStreaming}>
      <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {isInitialLoading && <ConversationAsyncState kind="loading" />}
        {isInitialError && (
          <ConversationAsyncState kind="error" message={errorMessage(error)} onRetry={onRetry} />
        )}
        {isEmpty && (emptyState || <ConversationAsyncState kind="empty" />)}

        {entries.length > 0 && (
          <div
            className={`space-y-3 px-4 py-3 ${contentClassName}`}
            role="log"
            aria-label={ariaLabel}
            aria-live="polite"
            aria-relevant="additions text"
          >
            {error && (
              <ConversationAsyncState
                kind="error"
                message={errorMessage(error)}
                onRetry={onRetry}
                className="min-h-0 rounded-lg border border-red-500/15 bg-red-500/5 py-4"
              />
            )}
            {entries.map((entry) => {
              if (entry.kind === "reasoning") {
                return (
                  <ReasoningDisclosure
                    key={entry.key}
                    content={entry.content}
                    streaming={entry.streaming && isStreaming}
                  />
                );
              }
              if (entry.kind === "tool") {
                return <ToolInvocation key={entry.key} invocation={entry.invocation} />;
              }
              return (
                <MessageBubble
                  key={entry.key}
                  message={entry.message}
                  streaming={entry.streaming && isStreaming}
                  readonly={readonly}
                  editable={isMessageEditable?.(entry.message) || false}
                  onEdit={onEditMessage}
                  onCommand={onCommand}
                />
              );
            })}
            {isStreaming && streamingSegments.length === 0 && (
              <div className="flex items-center gap-2 py-1 text-xs text-slate-500" role="status">
                <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                正在生成
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
