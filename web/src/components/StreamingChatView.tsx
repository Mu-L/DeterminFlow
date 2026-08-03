/**
 * StreamingChatView — 通用流式对话视图组件
 *
 * 渲染消息列表 + 流式片段（文本/推理/工具调用），支持可选的输入框。
 * 可嵌入 TaskParamFill、NodeMessageDrawer、SessionsPanel 等任意面板。
 *
 * Props 设计原则：
 * - 消息/流式数据由父组件通过 canonical conversation Hook 管理
 * - 本组件只负责渲染，不做状态管理
 */
import { useRef, type ReactNode } from "react";
import { Send, Square } from "lucide-react";
import type { Message, WSChatEvent } from "../types";
import type { StreamingSegment } from "../hooks/useStreamingSession";
import { ConversationTimeline } from "./conversation";

// ============ Props ============

export interface StreamingChatViewProps {
  /** 已保存的消息列表 */
  messages: Message[];
  /** 流式片段列表（实时 token/推理/工具调用） */
  streamingSegments: StreamingSegment[];
  /** 是否正在流式输出 */
  isStreaming: boolean;
  /** 发送消息回调（可选，不提供则隐藏输入框） */
  onSendMessage?: (content: string) => boolean | void;
  /** 中止流式回调（可选） */
  onAbort?: () => void;
  /** 头部区域，不提供则不渲染 */
  header?: ReactNode;
  /** 是否显示输入框（默认 true，但需要 onSendMessage） */
  inputEnabled?: boolean;
  /** 输入框 placeholder */
  inputPlaceholder?: string;
  /** 空状态占位内容 */
  emptyState?: ReactNode;
  /** 额外的流式事件处理（如 wf_variable_update） */
  onExtraEvent?: (event: WSChatEvent) => void;
  /** 自定义类名 */
  className?: string;
  /** 当前会话 ID，用于切换时重置自动跟随 */
  conversationId?: string | null;
  /** 实际 WebSocket 连接状态 */
  connected?: boolean;
  /** 历史消息是否正在加载 */
  loading?: boolean;
  /** 历史或流式错误 */
  error?: Error | string | null;
  /** 加载失败后的重试 */
  onRetry?: () => void;
}

// ============ 组件 ============

export default function StreamingChatView({
  messages,
  streamingSegments,
  isStreaming,
  onSendMessage,
  onAbort,
  header,
  inputEnabled = true,
  inputPlaceholder = "输入消息...",
  emptyState,
  className = "",
  conversationId,
  connected = true,
  loading = false,
  error = null,
  onRetry,
}: StreamingChatViewProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const showInput = inputEnabled && onSendMessage;

  const handleSend = () => {
    const content = textareaRef.current?.value.trim();
    if (!content || !onSendMessage) return;
    const sent = onSendMessage(content);
    if (sent === false) return;
    if (textareaRef.current) {
      textareaRef.current.value = "";
      // 重置高度
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /** textarea 自动增长高度 */
  const handleInput = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  return (
    <div className={`flex flex-col flex-1 min-h-0 ${className}`}>
      {/* 头部 */}
      {header && <div className="shrink-0">{header}</div>}

      <ConversationTimeline
        messages={messages}
        streamingSegments={streamingSegments}
        isStreaming={isStreaming}
        loading={loading}
        error={error}
        onRetry={onRetry}
        emptyState={emptyState}
        conversationId={conversationId}
        ariaLabel="聊天消息"
        contentClassName="px-3 py-2"
      />

      {/* 输入框 */}
      {showInput && (
        <div className="p-3 border-t border-slate-700/50 bg-slate-900/50 shrink-0">
          <div className="flex gap-2">
            <textarea
              ref={textareaRef}
              rows={1}
              placeholder={connected ? inputPlaceholder : "连接已断开，正在重连..."}
              aria-label={inputPlaceholder}
              onKeyDown={handleKeyDown}
              onInput={handleInput}
              disabled={isStreaming || !connected}
              className="flex-1 px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-200 text-sm placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 focus-visible:ring-2 focus-visible:ring-indigo-500/30 disabled:opacity-50 transition-colors duration-200 resize-y min-h-[44px] max-h-[200px]"
            />
            {isStreaming && onAbort ? (
              <button
                type="button"
                onClick={onAbort}
                aria-label="中止流式输出"
                className="px-4 py-2 rounded-lg bg-red-500 hover:bg-red-600 text-white transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
              >
                <Square size={16} aria-hidden="true" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSend}
                disabled={isStreaming || !connected}
                aria-label="发送消息"
                className="px-4 py-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
              >
                <Send size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
