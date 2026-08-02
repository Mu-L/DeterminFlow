/**
 * useChat — 全局 Chat WebSocket Hook
 *
 * 连接 /ws/chat，接收所有会话（main + sub）的流式事件。
 * 维护按 session_id 索引的多会话状态缓存，切换会话时切换展示目标，
 * 无需重建 WS 连接即可无缝衔接流式体验。
 *
 * 事件分发规则：
 * - 所有流式事件（token/tool_start/tool_end/chain_end 等）由后端统一
 *   发送到 chat 通道，通过 session_id 字段区隔。
 * - 本 Hook 无条件接收所有事件，按 session_id 分发到对应缓存。
 * - activeSessionId 仅用于切换展示，不参与事件过滤。
 */
import { useState, useCallback, useEffect, useRef } from "react";
import { useWebSocket } from "./useWebSocket";
import { Message, WSChatEvent, NotificationData, TokenUsage, ToolCallState, StreamingSegment } from "../types";
import { abortSession } from "../lib/api";

// ============ 调试时间追踪 ============

declare global {
  interface Window {
    DEBUG_TIMING?: boolean;
    FIRST_REASONING_TIME?: number;
    FIRST_TOKEN_TIME?: number;
  }
}

// 重新导出共享类型供外部使用
export type { StreamingSegment } from "../types";

// ============ 会话级状态 ============

interface PerSessionState {
  messages: Message[];
  streamingSegments: StreamingSegment[];
  isStreaming: boolean;
  streamingRef: string;
  toolDeltaRef: Record<number, ToolCallState>;
  hasStreamedThisCycle: boolean;
  tokenUsage: TokenUsage | null;
}

function createSessionState(): PerSessionState {
  return {
    messages: [],
    streamingSegments: [],
    isStreaming: false,
    streamingRef: "",
    toolDeltaRef: {},
    hasStreamedThisCycle: false,
    tokenUsage: null,
  };
}

// ============ 导出给 ChatPage 的流式状态快照 ============

export interface SessionStreamingSnapshot {
  isStreaming: boolean;
  streamingSegments: StreamingSegment[];
  hasStreamedThisCycle: boolean;
}

// ============ Hook ============

export function useChat() {
  // ---- 核心状态：多会话缓存（ref 供回调使用，state 供 React 渲染） ----
  const sessionStatesRef = useRef<Record<string, PerSessionState>>({});

  // React state 用于驱动 UI 重渲染；高频流式事件按动画帧合并。
  const [, setTick] = useState(0);
  const renderFrameRef = useRef<number | null>(null);

  const activeSessionIdRef = useRef<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const mainSessionIdRef = useRef<string | null>(null);

  const scheduleRender = useCallback((sessionId?: string | null) => {
    const visibleSessionId = activeSessionIdRef.current || mainSessionIdRef.current;
    // 后台会话继续更新缓存，但不驱动当前聊天页重渲染。
    if (sessionId && visibleSessionId && sessionId !== visibleSessionId) {
      return;
    }
    if (renderFrameRef.current !== null) {
      return;
    }
    renderFrameRef.current = requestAnimationFrame(() => {
      renderFrameRef.current = null;
      setTick((n) => n + 1);
    });
  }, []);

  useEffect(() => {
    return () => {
      if (renderFrameRef.current !== null) {
        cancelAnimationFrame(renderFrameRef.current);
        renderFrameRef.current = null;
      }
    };
  }, []);

  // ---- 辅助：确保会话状态存在（通过 ref 操作，不触发渲染） ----
  const ensureSession = useCallback((sessionId: string): PerSessionState => {
    if (!sessionStatesRef.current[sessionId]) {
      sessionStatesRef.current[sessionId] = createSessionState();
    }
    return sessionStatesRef.current[sessionId];
  }, []);

  /**
   * 重置指定会话的流式字段（保留 messages）。
   */
  const resetSessionStreaming = useCallback(
    (sessionId: string) => {
      const state = ensureSession(sessionId);
      state.isStreaming = false;
      state.streamingSegments = [];
      state.streamingRef = "";
      state.toolDeltaRef = {};
      scheduleRender(sessionId);
    },
    [ensureSession, scheduleRender],
  );

  // ============ WS 事件处理（全部事件无条件接收，按 session_id 分发） ============

  const handleMessage = useCallback(
    (data: unknown) => {
      const event = data as WSChatEvent & {
        session_id?: string;
        token_usage?: TokenUsage;
        data?: TokenUsage;
        index?: number;
        args_delta?: string;
        id?: string;
        name?: string;
      };
      const sid = event.session_id || null;

      switch (event.type) {
        // ---- stream_start：初始化流式状态 ----
        case "stream_start": {
          if (!sid) return;

          if (window.DEBUG_TIMING) {
            window.FIRST_REASONING_TIME = undefined;
            window.FIRST_TOKEN_TIME = undefined;
          }

          // 学习主会话 ID（来自 chat WS 首条历史事件中的主会话）
          if (!mainSessionIdRef.current) {
            mainSessionIdRef.current = sid;
          }

          // 重置当前会话的流式字段，保留已有 messages
          const s = ensureSession(sid);
          s.isStreaming = true;
          s.hasStreamedThisCycle = true;
          s.streamingSegments = [];
          s.streamingRef = "";
          s.toolDeltaRef = {};
          scheduleRender(sid);
          break;
        }

        // ---- token：累积流式文本 ----
        case "token": {
          if (!sid) return;
          const s = ensureSession(sid);
          if (!s.isStreaming) return;

          if (window.DEBUG_TIMING) {
            if (!window.FIRST_TOKEN_TIME) {
              window.FIRST_TOKEN_TIME = performance.now();
            }
          }

          s.streamingRef += event.content;
          const newContent = s.streamingRef;
          const prev = s.streamingSegments;
          const last = prev[prev.length - 1];
          if (last && last.type === "text") {
            s.streamingSegments = [...prev.slice(0, -1), { type: "text", content: newContent }];
          } else {
            s.streamingSegments = [...prev, { type: "text", content: newContent }];
          }
          scheduleRender(sid);
          break;
        }

        // ---- reasoning_token：思维链 ----
        case "reasoning_token": {
          if (!sid) return;
          const s = ensureSession(sid);
          if (!s.isStreaming) return;

          if (window.DEBUG_TIMING && !window.FIRST_REASONING_TIME) {
            window.FIRST_REASONING_TIME = performance.now();
          }

          const prev = s.streamingSegments;
          const last = prev[prev.length - 1];
          if (last && last.type === "reasoning") {
            s.streamingSegments = [
              ...prev.slice(0, -1),
              { type: "reasoning", content: last.content + event.content },
            ];
          } else {
            s.streamingSegments = [...prev, { type: "reasoning", content: event.content }];
          }
          scheduleRender(sid);
          break;
        }

        // ---- tool_call_delta：工具调用参数流式累积 ----
        case "tool_call_delta": {
          if (!sid) return;
          const s = ensureSession(sid);
          if (!s.isStreaming) return;

          const idx = event.index!;
          const deltaId = event.id || null;
          const existing = s.toolDeltaRef[idx];

          // 新一轮检测：id 变化、id 从有到无、name 变化（每个工具首个 chunk 携带 name）
          const isNewRound =
            existing &&
            ((!!deltaId && !!existing.id && deltaId !== existing.id) ||
              (!deltaId && !!existing.id) ||
              (!!event.name && !!existing.name && event.name !== existing.name));

          const name = event.name || (isNewRound ? "" : existing?.name) || "";
          const rnId =
            deltaId ||
            (isNewRound
              ? `delta_${idx}_${Date.now()}`
              : existing?.run_id || `delta_${idx}`);
          const accumulatedArgs =
            event.name || isNewRound
              ? event.args_delta
              : (existing?.args || "") + event.args_delta;

          s.toolDeltaRef[idx] = {
            id: deltaId,
            run_id: rnId,
            name,
            args: accumulatedArgs,
            status: "building",
          };

          const prev = s.streamingSegments;
          const existingSegIdx = prev.findIndex(
            (seg) =>
              seg.type === "tool" &&
              seg.tool.status === "building" &&
              seg.tool.run_id === rnId,
          );
          if (existingSegIdx >= 0) {
            const updated = [...prev];
            updated[existingSegIdx] = {
              type: "tool" as const,
              tool: { ...s.toolDeltaRef[idx] },
            };
            s.streamingSegments = updated;
          } else {
            s.streamingSegments = [
              ...prev,
              { type: "tool" as const, tool: { ...s.toolDeltaRef[idx] } },
            ];
          }
          scheduleRender(sid);
          break;
        }

        // ---- tool_start：工具开始执行 ----
        case "tool_start": {
          if (!sid) return;
          const s = ensureSession(sid);
          if (!s.isStreaming) return;

          s.streamingRef = "";
          const startIdx = event.index ?? -1;

          if (startIdx >= 0 && s.toolDeltaRef[startIdx]) {
            const deltaRunId = s.toolDeltaRef[startIdx].run_id;
            const updated: ToolCallState = {
              ...s.toolDeltaRef[startIdx],
              run_id: event.run_id,
              name: s.toolDeltaRef[startIdx].name || event.name,
              args: JSON.stringify(event.args),
              status: "running",
            };
            s.toolDeltaRef[startIdx] = updated;
            s.streamingSegments = s.streamingSegments.map((seg) =>
              seg.type === "tool" && seg.tool.status === "building" && seg.tool.run_id === deltaRunId
                ? { type: "tool" as const, tool: { ...updated } }
                : seg,
            );
          } else {
            s.streamingSegments = [
              ...s.streamingSegments,
              {
                type: "tool" as const,
                tool: {
                  name: event.name,
                  args: JSON.stringify(event.args),
                  run_id: event.run_id,
                  status: "running",
                },
              },
            ];
          }
          scheduleRender(sid);
          break;
        }

        // ---- tool_end：工具执行完成 ----
        case "tool_end": {
          if (!sid) return;
          const s = ensureSession(sid);
          s.streamingSegments = s.streamingSegments.map((seg) =>
            seg.type === "tool" && seg.tool.run_id === event.run_id
              ? {
                  ...seg,
                  tool: {
                    ...seg.tool,
                    result: event.result,
                    status: "completed" as const,
                  },
                }
              : seg,
          );
          scheduleRender(sid);
          break;
        }

        // ---- chain_end：一轮对话完成，全量消息快照 ----
        case "chain_end": {
          const ceEvent = event as WSChatEvent & {
            type: "chain_end";
            token_usage?: TokenUsage;
          };
          const targetId = sid || mainSessionIdRef.current;
          if (!targetId || !ceEvent.messages) return;

          // 学习主会话 ID
          if (sid && !mainSessionIdRef.current) {
            mainSessionIdRef.current = sid;
          }

          const s = ensureSession(targetId);
          s.messages = ceEvent.messages;
          s.isStreaming = false;
          s.streamingSegments = [];
          s.streamingRef = "";
          s.toolDeltaRef = {};
          if (ceEvent.token_usage) {
            s.tokenUsage = ceEvent.token_usage;
          }
          scheduleRender(targetId);
          break;
        }

        // ---- history：WS 连接后推送的历史消息 ----
        case "history": {
          const histEvent = event as WSChatEvent & {
            type: "history";
            token_usage?: TokenUsage;
          };
          const targetId = sid || mainSessionIdRef.current;
          if (!targetId || !histEvent.messages?.length) return;

          if (sid && !mainSessionIdRef.current) {
            mainSessionIdRef.current = sid;
          }

          const s = ensureSession(targetId);
          s.messages = histEvent.messages;
          s.isStreaming = false;
          s.streamingSegments = [];
          s.streamingRef = "";
          s.toolDeltaRef = {};
          if (histEvent.token_usage) {
            s.tokenUsage = histEvent.token_usage;
          }
          scheduleRender(targetId);
          break;
        }

        // ---- stream_end：流式结束（不含全量消息，chain_end 单独发送） ----
        case "stream_end": {
          if (!sid) return;
          resetSessionStreaming(sid);
          break;
        }

        // ---- llm_usage：Token 用量统计 ----
        case "llm_usage": {
          const usageEvent = event as WSChatEvent & {
            type: "llm_usage";
            data: TokenUsage;
          };
          const targetId = sid || mainSessionIdRef.current;
          if (!targetId || !usageEvent.data) return;
          ensureSession(targetId).tokenUsage = usageEvent.data;
          scheduleRender(targetId);
          break;
        }

        // ---- error：错误处理 ----
        case "error": {
          const targetId = sid || mainSessionIdRef.current;
          if (targetId) {
            resetSessionStreaming(targetId);
          }
          console.error("Chat error:", event.message);
          break;
        }

        // ---- notification：子会话通知 ----
        case "notification": {
          // 路由到主会话（子会话通知的上下文属于主会话）
          const targetId = mainSessionIdRef.current || activeSessionIdRef.current;
          if (!targetId) return;

          const notif = (event as { type: "notification"; data: NotificationData }).data;
          const notifContent =
            `**[子会话通知]** 来自 \`${notif.from}\`` +
            `${notif.task ? ` (${notif.task})` : ""}` +
            `${notif.status ? ` — 状态: ${notif.status}` : ""}` +
            `\n\n${notif.content}`;
          const notifMsg: Message = { type: "assistant", content: notifContent };

          const s = ensureSession(targetId);
          // 仅在主会话未在流式输出时追加（流式中的会被 chain_end 全量覆盖）
          if (!s.isStreaming) {
            s.messages = [...s.messages, notifMsg];
          }
          scheduleRender(targetId);
          break;
        }
      }
    },
    [ensureSession, resetSessionStreaming, scheduleRender],
  );

  // ---- WebSocket 连接 ----

  const {
    connected,
    send,
    disconnect: disconnectChat,
    connect: connectChat,
  } = useWebSocket({ url: "/ws/chat", onMessage: handleMessage });

  const reconnectChat = useCallback(() => {
    disconnectChat();
    setTimeout(() => connectChat(), 0);
  }, [disconnectChat, connectChat]);

  // ---- 对外方法 ----

  /** 发送消息到主会话 */
  const sendMessage = useCallback(
    (content: string) => {
      const mainId = mainSessionIdRef.current;
      if (mainId) {
        const s = ensureSession(mainId);
        s.messages = [...s.messages, { type: "user" as const, content }];
        scheduleRender(mainId);
      }
      send({ type: "message", content });
    },
    [send, ensureSession, scheduleRender],
  );

  /** 发送消息到指定会话（子会话或主会话） */
  const sendMessageToSession = useCallback(
    (sessionId: string, content: string) => {
      const s = ensureSession(sessionId);
      s.messages = [...s.messages, { type: "user" as const, content }];
      scheduleRender(sessionId);
      send({ type: "message", content, session_id: sessionId });
    },
    [send, ensureSession, scheduleRender],
  );

  /** 编辑消息并重新发送 */
  const editMessageAndResend = useCallback(
    (sessionId: string, messageId: string, newContent: string) => {
      const s = ensureSession(sessionId);
      // 检查目标会话是否正在流式输出
      if (s.isStreaming) return;

      // 乐观更新：截断到目标消息之前，追加编辑后的用户消息
      const targetIdx = s.messages.findIndex(
        (m) => m.type === "user" && m.id === messageId
      );
      if (targetIdx >= 0) {
        s.messages = [
          ...s.messages.slice(0, targetIdx),
          { type: "user" as const, content: newContent },
        ];
      }
      scheduleRender(sessionId);
      send({ type: "edit_message", message_id: messageId, content: newContent, session_id: sessionId });
    },
    [send, ensureSession, scheduleRender],
  );

  /** 切换活跃会话视图 */
  const switchToSession = useCallback((sessionId: string | null) => {
    activeSessionIdRef.current = sessionId;
    setActiveSessionId(sessionId);
  }, []);

  /** 从 REST API 加载历史消息到指定会话缓存 */
  const loadSessionHistory = useCallback(
    (sessionId: string, msgs: Message[], usage?: TokenUsage | null) => {
      const s = ensureSession(sessionId);
      // 守卫：若会话正在流式输出，不覆盖消息和流式状态，仅更新 tokenUsage
      if (s.isStreaming) {
        if (usage) s.tokenUsage = usage;
        return;
      }
      s.messages = msgs;
      s.isStreaming = false;
      s.streamingSegments = [];
      s.streamingRef = "";
      s.toolDeltaRef = {};
      if (usage) s.tokenUsage = usage;
      scheduleRender(sessionId);
    },
    [ensureSession, scheduleRender],
  );

  /** 获取指定会话的消息列表 */
  const getSessionMessages = useCallback(
    (sessionId: string | null): Message[] => {
      const id = sessionId || mainSessionIdRef.current;
      if (!id) return [];
      return sessionStatesRef.current[id]?.messages || [];
    },
    [],
  );

  /** 获取指定会话的流式状态快照 */
  const getSessionStreaming = useCallback(
    (sessionId: string | null): SessionStreamingSnapshot => {
      const id = sessionId || mainSessionIdRef.current;
      if (!id) return { isStreaming: false, streamingSegments: [], hasStreamedThisCycle: false };
      const s = sessionStatesRef.current[id];
      if (!s) return { isStreaming: false, streamingSegments: [], hasStreamedThisCycle: false };
      return {
        isStreaming: s.isStreaming,
        streamingSegments: s.streamingSegments,
        hasStreamedThisCycle: s.hasStreamedThisCycle,
      };
    },
    [],
  );

  /** 获取指定会话的 Token 用量 */
  const getSessionTokenUsage = useCallback(
    (sessionId: string | null): TokenUsage | null => {
      const id = sessionId || mainSessionIdRef.current;
      if (!id) return null;
      return sessionStatesRef.current[id]?.tokenUsage || null;
    },
    [],
  );

  /** 获取主会话 ID */
  const getMainSessionId = useCallback((): string | null => {
    return mainSessionIdRef.current;
  }, []);

  /** 设置主会话 ID（ChatPage 在 mainSessionId 变更时调用） */
  const setMainSessionId = useCallback((sessionId: string | null) => {
    mainSessionIdRef.current = sessionId;
  }, []);

  /** 中止指定会话的流式输出 */
  const abortStream = useCallback(
    async (sessionId: string | null) => {
      if (!sessionId) return;
      try {
        await abortSession(sessionId);
      } catch (e) {
        console.error("中止会话失败:", e);
      }
      resetSessionStreaming(sessionId);
    },
    [resetSessionStreaming],
  );

  return {
    // WS 状态
    connected,
    reconnectChat,

    // 消息发送
    sendMessage,
    sendMessageToSession,
    editMessageAndResend,

    // 会话切换 & 历史加载
    activeSessionId,
    switchToSession,
    loadSessionHistory,

    // 数据查询
    getSessionMessages,
    getSessionStreaming,
    getSessionTokenUsage,

    // 主会话 ID 管理
    getMainSessionId,
    setMainSessionId,

    // 流式中止
    abortStream,
  };
}
