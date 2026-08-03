import { useCallback, useEffect, useReducer } from "react";

import { useWebSocket } from "../../hooks/useWebSocket";
import type { Message } from "../../types";
import {
  conversationReducer,
  createConversationState,
} from "./conversationReducer";
import { normalizeConversationEvent } from "./normalizeConversationEvent";
import type { ConversationState } from "./conversationTypes";
import { publishContentSafetyDiagnosticControlEvent } from "./contentSafetyDiagnosticProtocol";

export interface UseConversationOptions {
  sessionId: string | null;
  autoConnect?: boolean;
  onExtraEvent?: (event: unknown) => void;
}

export interface UseConversationResult extends ConversationState {
  /** Returns false without mutating local history when the socket is unavailable. */
  sendMessage: (content: string) => boolean;
  sendCommand: (payload: { type: string; [key: string]: unknown }) => boolean;
  editMessageAndResend: (messageId: string, content: string) => boolean;
  resync: () => boolean;
  replaceMessages: (messages: Message[]) => void;
  clearError: () => void;
}

/** A single-session view over the canonical snapshot + revision WS protocol. */
export function useConversation({
  sessionId,
  autoConnect = true,
  onExtraEvent,
}: UseConversationOptions): UseConversationResult {
  const [state, dispatch] = useReducer(
    conversationReducer,
    sessionId,
    createConversationState,
  );

  useEffect(() => {
    dispatch({ type: "select_session", sessionId });
  }, [sessionId]);

  const handleMessage = useCallback(
    (rawEvent: unknown) => {
      if (publishContentSafetyDiagnosticControlEvent(rawEvent)) return;
      const event = normalizeConversationEvent(rawEvent, sessionId);
      if (event) {
        dispatch({ type: "server_event", event });
      } else {
        onExtraEvent?.(rawEvent);
      }
    },
    [onExtraEvent, sessionId],
  );

  const wsUrl = sessionId
    ? `/ws/chat?session_id=${encodeURIComponent(sessionId)}`
    : "/ws/chat";
  const { connected, send } = useWebSocket({
    url: wsUrl,
    autoConnect: autoConnect && Boolean(sessionId),
    onMessage: handleMessage,
  });

  useEffect(() => {
    dispatch({ type: "connection_changed", connected });
  }, [connected]);

  const sendCommand = useCallback(
    (payload: { type: string; [key: string]: unknown }): boolean => {
      if (!connected || !sessionId) return false;
      return send({ ...payload, session_id: sessionId });
    },
    [connected, send, sessionId],
  );

  const resync = useCallback(
    (): boolean => sendCommand({ type: "resync" }),
    [sendCommand],
  );

  useEffect(() => {
    if (
      !connected ||
      !sessionId ||
      (!state.needsResync && state.phase !== "reconnecting")
    ) return;
    resync();
  }, [
    connected,
    resync,
    sessionId,
    state.needsResync,
    state.phase,
    state.syncIssue?.received,
  ]);

  const sendMessage = useCallback(
    (content: string): boolean => {
      const normalizedContent = content.trim();
      if (
        !sessionId ||
        state.sessionId !== sessionId ||
        state.phase !== "ready" ||
        !normalizedContent
      ) {
        return false;
      }
      const sent = sendCommand({
        type: "message",
        content: normalizedContent,
      });
      if (!sent) return false;
      dispatch({
        type: "append_optimistic_message",
        sessionId,
        message: { type: "user", content: normalizedContent },
      });
      return true;
    },
    [sendCommand, sessionId, state.phase, state.sessionId],
  );

  const editMessageAndResend = useCallback(
    (messageId: string, content: string): boolean => {
      const normalizedContent = content.trim();
      if (
        !sessionId ||
        state.sessionId !== sessionId ||
        state.phase !== "ready" ||
        !normalizedContent ||
        !state.messages.some(
          (message) => message.id === messageId && message.type === "user",
        )
      ) {
        return false;
      }
      const sent = sendCommand({
        type: "edit_message",
        message_id: messageId,
        content: normalizedContent,
      });
      if (!sent) return false;
      dispatch({
        type: "edit_optimistic_message",
        sessionId,
        messageId,
        content: normalizedContent,
      });
      return true;
    },
    [sendCommand, sessionId, state.messages, state.phase, state.sessionId],
  );

  const replaceMessages = useCallback(
    (messages: Message[]) => {
      if (!sessionId) return;
      dispatch({ type: "replace_messages", sessionId, messages });
    },
    [sessionId],
  );

  const clearError = useCallback(() => {
    dispatch({ type: "clear_error" });
  }, []);

  // Effects run after render; never expose the previous session for one frame.
  const visibleState =
    state.sessionId === sessionId ? state : createConversationState(sessionId);

  return {
    ...visibleState,
    connected,
    sendMessage,
    sendCommand,
    editMessageAndResend,
    resync,
    replaceMessages,
    clearError,
  };
}
