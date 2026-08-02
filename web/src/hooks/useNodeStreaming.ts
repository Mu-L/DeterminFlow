/**
 * useNodeStreaming — 工作流节点流式消息 Hook
 *
 * 连接 /ws/chat?session_id=xxx 获取节点 Agent 的实时 token 流式输出。
 * 与 useStreamingSession 的区别：
 * - 专注于只读场景（不支持 sendMessage）
 * - 合并历史消息 + 流式片段的去重逻辑
 * - 稳定的 callback 引用（通过 ref 避免不必要的 WS 重连）
 */
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useWebSocket } from "./useWebSocket";
import type { Message, WSChatEvent, ToolCallState, StreamingSegment } from "../types";

// 重新导出共享类型供外部使用（保持向后兼容）
export type { ToolCallState, StreamingSegment } from "../types";

// ============ Hook 参数 ============

interface UseNodeStreamingOptions {
  /** 节点的 session_id，null 时不连接 */
  sessionId: string | null;
  /** 是否自动连接（默认 true） */
  autoConnect?: boolean;
}

// ============ Hook 返回值 ============

export interface UseNodeStreamingReturn {
  /** 合并后的完整消息列表（base + streaming 去重） */
  messages: Message[];
  /** 当前流式片段 */
  streamingSegments: StreamingSegment[];
  /** 是否正在流式输出 */
  isStreaming: boolean;
  /** WebSocket 连接状态 */
  connected: boolean;
  /** 设置基础消息（从 REST API 加载的历史） */
  setBaseMessages: (msgs: Message[]) => void;
  /** 清空所有消息 */
  clearMessages: () => void;
}

// ============ Hook 实现 ============

export function useNodeStreaming({
  sessionId,
  autoConnect = true,
}: UseNodeStreamingOptions): UseNodeStreamingReturn {
  const [baseMessages, setBaseMessagesState] = useState<Message[]>([]);
  const [streamingSegments, setStreamingSegments] = useState<StreamingSegment[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  // Refs for stable callback
  const streamingRef = useRef("");
  const toolDeltaRef = useRef<Record<number, ToolCallState>>({});
  const streamingSessionIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  sessionIdRef.current = sessionId;
  const baseMessagesRef = useRef<Message[]>([]);
  baseMessagesRef.current = baseMessages;

  // ============ 消息管理 ============

  const setBaseMessages = useCallback((msgs: Message[]) => {
    setBaseMessagesState(msgs);
    baseMessagesRef.current = msgs;
  }, []);

  const clearMessages = useCallback(() => {
    setBaseMessagesState([]);
    baseMessagesRef.current = [];
    setStreamingSegments([]);
    setIsStreaming(false);
    streamingSessionIdRef.current = null;
    streamingRef.current = "";
    toolDeltaRef.current = {};
  }, []);

  // ============ 合并消息（base + streaming 去重） ============

  const messages: Message[] = useMemo(() => {
    const base = baseMessages;
    const baseIds = new Set(base.map((m) => m.id).filter(Boolean));

    // 将 streaming segments 转换为可显示的 Message 格式，跳过已在 base 中的
    const streamingMsgs: Message[] = [];
    for (const seg of streamingSegments) {
      if (seg.type === "text") {
        streamingMsgs.push({
          type: "assistant",
          content: seg.content,
          id: `streaming-text`,
        });
      } else if (seg.type === "reasoning") {
        streamingMsgs.push({
          type: "assistant",
          content: `> 🧠 思考中...\n\n${seg.content}`,
          id: `streaming-reasoning`,
        });
      } else if (seg.type === "tool") {
        const toolId = `streaming-tool-${seg.tool.run_id}`;
        if (!baseIds.has(toolId)) {
          const toolFn = {
            name: seg.tool.name || "",
            arguments: seg.tool.args || "",
            ...(seg.tool.status === "completed" ? { result: seg.tool.result } : {}),
          };
          streamingMsgs.push({
            type: "assistant",
            tool_calls: [{
              id: seg.tool.run_id,
              type: "function" as const,
              function: toolFn,
            }],
            id: toolId,
          });
        }
      }
    }

    return [...base, ...streamingMsgs];
  }, [baseMessages, streamingSegments]);

  // ============ WS 事件处理（稳定的 callback） ============

  const handleMessage = useCallback((data: unknown) => {
    const event = data as WSChatEvent & { session_id?: string };
    const eventSessionId = event.session_id || null;
    const targetId = sessionIdRef.current;

    // 只处理属于当前目标会话的事件
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
      case "chain_end": {
        if (event.messages && event.messages.length > 0) {
          // chain_end 携带完整 messages，替换 base
          setBaseMessagesState(event.messages);
          baseMessagesRef.current = event.messages;
        }
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

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

        const oldDeltaRunId = (startIdx >= 0 && toolDeltaRef.current[startIdx])
          ? toolDeltaRef.current[startIdx].run_id
          : null;

        setStreamingSegments((prev) => {
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

          return [...prev, {
            type: "tool" as const,
            tool: { name: toolName, args: JSON.stringify(event.args), run_id: event.run_id, status: "running" },
          }];
        });

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

      case "stream_end": {
        if (eventSessionId !== streamingSessionIdRef.current) return;
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

      case "history": {
        // WebSocket 连接建立后推送的历史消息
        if (targetId && eventSessionId && eventSessionId !== targetId) {
          return;
        }
        if (event.messages && event.messages.length > 0) {
          setBaseMessagesState(event.messages);
          baseMessagesRef.current = event.messages;
        }
        setStreamingSegments([]);
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        break;
      }

      case "error": {
        setIsStreaming(false);
        streamingSessionIdRef.current = null;
        streamingRef.current = "";
        toolDeltaRef.current = {};
        setStreamingSegments([]);
        break;
      }

      default:
        break;
    }
  }, []); // 稳定的 callback，不依赖任何 state

  // ============ WebSocket 连接 ============

  const wsUrl = sessionId ? `/ws/chat?session_id=${encodeURIComponent(sessionId)}` : "/ws/chat";

  const { connected } = useWebSocket({
    url: wsUrl,
    autoConnect: autoConnect && !!sessionId,
    onMessage: handleMessage,
  });

  // sessionId 变化时清空流式状态（但保留 base messages）
  useEffect(() => {
    setStreamingSegments([]);
    setIsStreaming(false);
    streamingSessionIdRef.current = null;
    streamingRef.current = "";
    toolDeltaRef.current = {};
  }, [sessionId]);

  return {
    messages,
    streamingSegments,
    isStreaming,
    connected,
    setBaseMessages,
    clearMessages,
  };
}
