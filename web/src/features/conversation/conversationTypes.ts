import type {
  Message,
  StreamingSegment,
  TokenUsage,
} from "../../types";

export type ConversationPhase =
  | "idle"
  | "loading"
  | "ready"
  | "streaming"
  | "reconnecting"
  | "error";

export interface NormalizedMessage extends Message {
  id: string;
  type: string;
}

export interface RevisionGap {
  type: "revision_gap";
  expected: number;
  received: number;
}

export interface ConversationState {
  sessionId: string | null;
  generationId: string | null;
  revision: number | null;
  status: string | null;
  phase: ConversationPhase;
  connected: boolean;
  messages: NormalizedMessage[];
  streamingSegments: StreamingSegment[];
  isStreaming: boolean;
  tokenUsage: TokenUsage | null;
  needsResync: boolean;
  syncIssue: RevisionGap | null;
  error: string | null;
  /** Internal correlation from a wire tool index to the current segment key. */
  toolCallKeysByIndex: Record<number, string>;
}

interface ConversationEventBase {
  sessionId: string;
  generationId: string | null;
  revision: number | null;
}

export interface ConversationSnapshotEvent extends ConversationEventBase {
  kind: "snapshot";
  messages: Message[];
  status: string | null;
  activeStream: {
    generationId: string;
    revision: number | null;
    segments: StreamingSegment[];
  } | null;
  tokenUsage: TokenUsage | null;
  error: string | null;
  legacy: boolean;
}

export interface ConversationStreamStartEvent extends ConversationEventBase {
  kind: "stream_start";
}

export interface ConversationTextDeltaEvent extends ConversationEventBase {
  kind: "text_delta" | "reasoning_delta";
  content: string;
}

export interface ConversationToolDeltaEvent extends ConversationEventBase {
  kind: "tool_delta";
  index: number;
  callId: string | null;
  name: string | null;
  argumentsDelta: string;
}

export interface ConversationToolStartEvent extends ConversationEventBase {
  kind: "tool_start";
  index: number | null;
  runId: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ConversationToolEndEvent extends ConversationEventBase {
  kind: "tool_end";
  runId: string;
  name: string;
  result: string;
  status: "completed" | "failed" | "cancelled";
}

export interface ConversationChainEndEvent extends ConversationEventBase {
  kind: "chain_end";
  messages: Message[];
  tokenUsage: TokenUsage | null;
}

export interface ConversationStreamEndEvent extends ConversationEventBase {
  kind: "stream_end";
}

export interface ConversationErrorEvent extends ConversationEventBase {
  kind: "error";
  message: string;
  terminal: boolean;
}

export interface ConversationUsageEvent extends ConversationEventBase {
  kind: "usage";
  tokenUsage: TokenUsage;
}

export type ConversationServerEvent =
  | ConversationSnapshotEvent
  | ConversationStreamStartEvent
  | ConversationTextDeltaEvent
  | ConversationToolDeltaEvent
  | ConversationToolStartEvent
  | ConversationToolEndEvent
  | ConversationChainEndEvent
  | ConversationStreamEndEvent
  | ConversationErrorEvent
  | ConversationUsageEvent;

export type ConversationAction =
  | { type: "select_session"; sessionId: string | null }
  | { type: "connection_changed"; connected: boolean }
  | { type: "server_event"; event: ConversationServerEvent }
  | { type: "replace_messages"; sessionId: string; messages: Message[] }
  | { type: "append_optimistic_message"; sessionId: string; message: Message }
  | {
      type: "edit_optimistic_message";
      sessionId: string;
      messageId: string;
      content: string;
    }
  | { type: "clear_error" };
