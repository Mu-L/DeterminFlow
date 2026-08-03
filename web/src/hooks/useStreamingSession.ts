/**
 * Backward-compatible single-session adapter over the canonical conversation
 * snapshot/revision protocol.
 */
import { useCallback } from "react";

import { useConversation } from "../features/conversation/useConversation";
import type {
  ConversationPhase,
} from "../features/conversation/conversationTypes";
import { abortSession } from "../lib/api";
import type { Message, StreamingSegment } from "../types";

export type { StreamingSegment } from "../types";

interface UseStreamingSessionOptions {
  sessionId: string | null;
  autoConnect?: boolean;
  onExtraEvent?: (event: unknown) => void;
}

export interface UseStreamingSessionReturn {
  messages: Message[];
  streamingSegments: StreamingSegment[];
  phase: ConversationPhase;
  isStreaming: boolean;
  connected: boolean;
  error: string | null;
  sendMessage: (content: string) => boolean;
  retry: () => boolean;
  abortStream: () => Promise<void>;
}

export function useStreamingSession({
  sessionId,
  autoConnect = true,
  onExtraEvent,
}: UseStreamingSessionOptions): UseStreamingSessionReturn {
  const conversation = useConversation({
    sessionId,
    autoConnect,
    onExtraEvent,
  });
  const { resync } = conversation;

  const abortStream = useCallback(async () => {
    if (!sessionId) return;
    try {
      await abortSession(sessionId);
    } catch {
      // Re-read authoritative state when the HTTP control request fails.
      resync();
    }
  }, [resync, sessionId]);

  return {
    messages: conversation.messages,
    streamingSegments: conversation.streamingSegments,
    phase: conversation.phase,
    isStreaming: conversation.isStreaming,
    connected: conversation.connected,
    error: conversation.error,
    sendMessage: conversation.sendMessage,
    retry: resync,
    abortStream,
  };
}
