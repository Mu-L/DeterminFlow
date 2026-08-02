/**
 * StreamingChatView — 通用流式对话视图组件
 *
 * 渲染消息列表 + 流式片段（文本/推理/工具调用），支持可选的输入框。
 * 可嵌入 TaskParamFill、NodeMessageDrawer、SessionsPanel 等任意面板。
 *
 * Props 设计原则：
 * - 消息/流式数据由父组件通过 Hook 管理（useStreamingSession / useChat）
 * - 本组件只负责渲染，不做状态管理
 */
import { useRef, useEffect, useMemo, type ReactNode } from "react";
import { Send, Square } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import ChatMessage from "./ChatMessage";
import StreamingMessage from "./StreamingMessage";
import ThinkingChain from "./ThinkingChain";
import ToolCallCard from "./ToolCallCard";
import type { Message, WSChatEvent } from "../types";
import type { StreamingSegment } from "../hooks/useStreamingSession";

// ============ Props ============

export interface StreamingChatViewProps {
  /** 已保存的消息列表 */
  messages: Message[];
  /** 流式片段列表（实时 token/推理/工具调用） */
  streamingSegments: StreamingSegment[];
  /** 是否正在流式输出 */
  isStreaming: boolean;
  /** 发送消息回调（可选，不提供则隐藏输入框） */
  onSendMessage?: (content: string) => void;
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
}: StreamingChatViewProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // 预合并工具结果：将连续的 assistant + tool 消息合并，使 ToolCallCard 统一显示 args + result
  const mergedMessages = useMemo(() => {
    const result: Message[] = [];
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];
      if (msg.type === "tool") {
        i++;
        continue;
      }
      if (msg.type === "assistant" && msg.tool_calls && msg.tool_calls.length > 0) {
        const toolResults: Record<string, string> = {};
        let j = i + 1;
        while (j < messages.length && messages[j].type === "tool") {
          const toolMsg = messages[j];
          if (toolMsg.tool_call_id) {
            toolResults[toolMsg.tool_call_id] = toolMsg.content || "";
          }
          j++;
        }
        const enhancedToolCalls = msg.tool_calls.map((tc) => ({
          ...tc,
          function: {
            ...tc.function,
            result: toolResults[tc.id] || tc.function.result,
          },
        }));
        result.push({ ...msg, tool_calls: enhancedToolCalls });
        i = j;
      } else {
        result.push(msg);
        i++;
      }
    }
    return result;
  }, [messages]);

  // 自动滚到底部（Radix ScrollArea 的可滚动元素是内部 viewport）
  useEffect(() => {
    const viewport = scrollAreaRef.current?.querySelector(
      "[data-radix-scroll-area-viewport]"
    ) as HTMLElement | null;
    if (viewport) {
      requestAnimationFrame(() => {
        viewport.scrollTop = viewport.scrollHeight;
      });
    }
  }, [messages.length, streamingSegments.length, isStreaming]);

  const showInput = inputEnabled && onSendMessage;

  const handleSend = () => {
    const content = textareaRef.current?.value.trim();
    if (!content || !onSendMessage) return;
    onSendMessage(content);
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

      {/* 消息区域 */}
      <ScrollArea ref={scrollAreaRef} className="flex-1">
        <div
          className="px-3 py-2 space-y-2"
          role="log"
          aria-label="聊天消息"
          aria-live="polite"
        >
          {/* 空状态 */}
          {mergedMessages.length === 0 && streamingSegments.length === 0 && (
            emptyState || (
              <div className="flex flex-col items-center justify-center py-8 text-slate-500" role="status" aria-label="暂无消息">
                <p className="text-sm">暂无消息</p>
              </div>
            )
          )}

          {/* 已保存消息 */}
          {mergedMessages.map((msg, i) => (
            <ChatMessage key={msg.id || `msg-${i}`} message={msg} />
          ))}

          {/* 流式片段 */}
          {streamingSegments.map((seg, i) => {
            const isLast = i === streamingSegments.length - 1;

            if (seg.type === "text") {
              return (
                <StreamingMessage
                  key={`seg-text-${i}`}
                  content={seg.content}
                  showCursor={isStreaming && isLast}
                />
              );
            }

            if (seg.type === "reasoning") {
              return (
                <div key={`seg-reasoning-${i}`} className="flex justify-start">
                  <div className="max-w-[85%]">
                    <ThinkingChain
                      content={seg.content}
                      isStreaming={isLast && isStreaming}
                    />
                  </div>
                </div>
              );
            }

            return (
              <ToolCallCard
                key={`seg-tool-${seg.tool.run_id}`}
                name={seg.tool.name}
                args={seg.tool.args}
                result={seg.tool.result}
                status={seg.tool.status}
              />
            );
          })}

        </div>
      </ScrollArea>

      {/* 输入框 */}
      {showInput && (
        <div className="p-3 border-t border-slate-700/50 bg-slate-900/50 shrink-0">
          <div className="flex gap-2">
            <textarea
              ref={textareaRef}
              rows={1}
              placeholder={inputPlaceholder}
              aria-label={inputPlaceholder}
              onKeyDown={handleKeyDown}
              onInput={handleInput}
              disabled={isStreaming}
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
                disabled={isStreaming}
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
