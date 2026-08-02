/**
 * useStreamingSession — 通用流式会话 Hook
 *
 * 封装 WebSocket 连接 + 会话级事件过滤 + 流式状态管理，
 * 可被任意组件复用（TaskParamFill、NodeMessageDrawer、SessionsPanel 等）。
 *
 * 与 useChat 的区别：
 * - 无子会话支持（单会话视图）
 * - 无 pendingNotifications / 通知队列
 * - 通过 sendMessage 直接向指定 session 发送消息（WS 通道）
 */
import { useState, useCallback, useRef } from "react";
import { useWebSocket } from "./useWebSocket";
import type { Message, WSChatEvent, ToolCallState, StreamingSegment } from "../types";

// 重新导出共享类型供外部使用
export type { StreamingSegment } from "../types";

// ============ Hook 参数 ============

interface UseStreamingSessionOptions {
  /** 目标会话 ID，null 时不连接 */
  sessionId: string | null;
  /** 是否自动连接（默认 true） */
  autoConnect?: boolean;
  /** 额外的 WS 事件处理（如 wf_variable_update） */
  onExtraEvent?: (event: Record<string, unknown>) => void;
}

// ============ Hook 返回值 ============

export interface UseStreamingSessionReturn {
  messages: Message[];
  streamingSegments: StreamingSegment[];
  isStreaming: boolean;
  connected: boolean;
  /** 发送消息（乐观追加 user 消息到 messages） */
  sendMessage: (content: string) => void;
  /** 设置消息列表（如注入欢迎消息） */
  setMessages: (msgs: Message[]) => void;
  /** 清空消息列表 */
  clearMessages: () => void;
  /** 中止当前流式输出 */
  abortStream: () => Promise<void>;
}

// ============ Hook 实现 ============

export function useStreamingSession({
  sessionId,
  autoConnect = true,
  onExtraEvent,
}: UseStreamingSessionOptions): UseStreamingSessionReturn {
  const [messages, _setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingSegments, setStreamingSegments] = useState<StreamingSegment[]>([]);

  // 内部 ref
  const streamingRef = useRef("");
  const toolDeltaRef = useRef<Record<number, ToolCallState>>({});
  const streamingSessionIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  sessionIdRef.current = sessionId;

  // ============ WS 事件处理 ============

  const handleMessage = useCallback((data: unknown) => {
    const event = data as WSChatEvent & { session_id?: string };
    const eventSessionId = event.session_id || null;

    // 只处理属于当前目标会话的事件
    const targetId = sessionIdRef.current;
    if (targetId && eventSessionId && eventSessionId !== targetId) {
      return;
    }

    // 兜底：如果事件无 session_id 且当前无目标，也接受（兼容历史消息推送）
    if (
      !eventSessionId &&
      targetId &&
      event.type !== "history" &&
      event.type !== "notification"
    ) {
      return;
    }

    switch (event.type) {
      case "stream_start": {
        setIsStreaming(true);
        streamingSessionIdRef.current = eventSessionId;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

      case "token": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        streamingRef.current += event.content;
        const newContent = streamingRef.current;
        setStreamingSegments((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.type === "text") {
            return [...prev.slice(0, -1), { type: "text", content: newContent }];
          }
          return [...prev, { type: "text", content: newContent }];
        });
        break;
      }

      case "reasoning_token": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        setStreamingSegments((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.type === "reasoning") {
            return [...prev.slice(0, -1), { type: "reasoning", content: last.content + event.content }];
          }
          return [...prev, { type: "reasoning", content: event.content }];
        });
        break;
      }

      case "tool_call_delta": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        const idx = event.index;
        const delta = event as WSChatEvent & { type: "tool_call_delta"; index: number; args_delta: string; id?: string; name?: string };
        const existing = toolDeltaRef.current[idx];
        const deltaId = delta.id || null;

        // 检测新一轮工具调用：id 变化、id 从有到无、name 变化（每个工具首个 chunk 携带 name）
        const isNewRound = existing && (
          (!!deltaId && !!existing.id && deltaId !== existing.id) ||
          (!deltaId && !!existing.id) ||
          (!!delta.name && !!existing.name && delta.name !== existing.name)
        );

        const name = delta.name || (isNewRound ? "" : existing?.name) || "";
        const rnId = deltaId || (isNewRound
          ? `delta_${idx}_${Date.now()}`
          : (existing?.run_id || `delta_${idx}`));
        const accumulatedArgs = (delta.name || isNewRound)
          ? delta.args_delta
          : (existing?.args || "") + delta.args_delta;

        toolDeltaRef.current[idx] = {
          id: deltaId,
          run_id: rnId,
          name,
          args: accumulatedArgs,
          status: "building",
        };

        setStreamingSegments((prev) => {
          const existingSegIdx = prev.findIndex(
            (seg) => seg.type === "tool" && seg.tool.status === "building" && seg.tool.run_id === rnId
          );
          if (existingSegIdx >= 0) {
            const updated = [...prev];
            updated[existingSegIdx] = { type: "tool" as const, tool: { ...toolDeltaRef.current[idx] } };
            return updated;
          }
          return [...prev, { type: "tool" as const, tool: { ...toolDeltaRef.current[idx] } }];
        });
        break;
      }

      case "tool_start": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        streamingRef.current = "";
        const startIdx = event.index ?? -1;
        const toolName = event.name || "";

        // 先保存 toolDeltaRef 中旧的 run_id，用于匹配 building 状态的 segment
        const oldDeltaRunId = (startIdx >= 0 && toolDeltaRef.current[startIdx])
          ? toolDeltaRef.current[startIdx].run_id
          : null;

        setStreamingSegments((prev) => {
          // 优先通过旧 run_id 匹配 building 状态的 segment（与 useChat.ts 一致）
          if (oldDeltaRunId) {
            const existingSegIdx = prev.findIndex(
              (seg) => seg.type === "tool" && seg.tool.status === "building" && seg.tool.run_id === oldDeltaRunId
            );
            if (existingSegIdx >= 0) {
              const updated = [...prev];
              updated[existingSegIdx] = {
                type: "tool" as const,
                tool: {
                  name: toolName,
                  run_id: event.run_id,
                  args: JSON.stringify(event.args),
                  status: "running",
                },
              };
              return updated;
            }
          }

          // 没有对应的 delta 关联，直接创建新的 running 工具段
          return [...prev, {
            type: "tool" as const,
            tool: { name: toolName, args: JSON.stringify(event.args), run_id: event.run_id, status: "running" },
          }];
        });

        // 同步更新 toolDeltaRef
        if (startIdx >= 0) {
          toolDeltaRef.current[startIdx] = {
            name: toolName,
            args: JSON.stringify(event.args),
            run_id: event.run_id,
            status: "running",
          };
        }
        break;
      }

      case "tool_end": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        setStreamingSegments((prev) =>
          prev.map((seg) =>
            seg.type === "tool" && seg.tool.run_id === event.run_id
              ? { ...seg, tool: { ...seg.tool, result: event.result, status: "completed" as const } }
              : seg
          )
        );
        break;
      }

      case "chain_end": {
        if (event.messages) {
          _setMessages(event.messages);
        }
        // 清空流式片段，避免与 messages 中的工具消息重复显示
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

      case "stream_end": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

      case "error": {
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        console.error("Chat error:", (event as { message: string }).message);
        break;
      }

      case "history": {
        // 按 session_id 过滤：如果 history 携带 session_id 且与目标不匹配，则跳过
        if (targetId && eventSessionId && eventSessionId !== targetId) {
          return;
        }
        // WebSocket 连接建立后推送的历史消息
        if (event.messages && event.messages.length > 0) {
          _setMessages(event.messages);
        }
        setStreamingSegments([]);
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        break;
      }

      // 转发其他事件类型给外部处理器
      default: {
        onExtraEvent?.(event as unknown as Record<string, unknown>);
        break;
      }
    }
  }, [onExtraEvent]);

  // ============ WebSocket 连接 ============

  // 携带 session_id 查询参数，后端据此推送正确的 session 历史
  const wsUrl = sessionId ? `/ws/chat?session_id=${encodeURIComponent(sessionId)}` : "/ws/chat";

  const { connected, send } = useWebSocket({
    url: wsUrl,
    autoConnect: autoConnect && !!sessionId,
    onMessage: handleMessage,
  });

  // ============ 对外方法 ============

  const sendMessage = useCallback(
    (content: string) => {
      // 乐观更新：立即添加用户消息
      _setMessages((prev) => [...prev, { type: "user", content }]);
      send({ type: "message", content, session_id: sessionId });
    },
    [send, sessionId]
  );

  const setMessages = useCallback((msgs: Message[]) => {
    _setMessages(msgs);
  }, []);

  const clearMessages = useCallback(() => {
    _setMessages([]);
    setStreamingSegments([]);
    setIsStreaming(false);
    streamingSessionIdRef.current = null;
    streamingRef.current = "";
    toolDeltaRef.current = {};
  }, []);

  const abortStream = useCallback(async () => {
    if (!sessionId) return;
    try {
      const { abortSession } = await import("../lib/api");
      await abortSession(sessionId);
    } catch (e) {
      console.error("中止会话失败:", e);
    }
    setIsStreaming(false);
    streamingSessionIdRef.current = null;
    streamingRef.current = "";
    toolDeltaRef.current = {};
    setStreamingSegments([]);
  }, [sessionId]);

  return {
    messages,
    streamingSegments,
    isStreaming,
    connected,
    sendMessage,
    setMessages,
    clearMessages,
    abortStream,
  };
}
