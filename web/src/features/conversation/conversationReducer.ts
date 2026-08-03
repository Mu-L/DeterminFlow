import type { StreamingSegment, ToolCallState } from "../../types";
import { normalizeMessages } from "./normalizeMessages";
import type {
  ConversationAction,
  ConversationServerEvent,
  ConversationState,
  ConversationToolDeltaEvent,
  ConversationToolStartEvent,
} from "./conversationTypes";

export function createConversationState(
  sessionId: string | null = null,
): ConversationState {
  return {
    sessionId,
    generationId: null,
    revision: null,
    status: null,
    phase: sessionId ? "loading" : "idle",
    connected: false,
    messages: [],
    streamingSegments: [],
    isStreaming: false,
    tokenUsage: null,
    needsResync: false,
    syncIssue: null,
    error: null,
    toolCallKeysByIndex: {},
  };
}

function appendContentSegment(
  segments: StreamingSegment[],
  type: "text" | "reasoning",
  content: string,
): StreamingSegment[] {
  const last = segments[segments.length - 1];
  if (last?.type === type) {
    return [
      ...segments.slice(0, -1),
      { type, content: last.content + content },
    ];
  }
  return [...segments, { type, content }];
}

function findToolSegmentIndex(
  segments: StreamingSegment[],
  key: string | null,
  fallbackName: string | null = null,
): number {
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (segment.type !== "tool") continue;
    if (
      key &&
      (segment.tool.run_id === key || segment.tool.id === key)
    ) {
      return index;
    }
    if (
      !key &&
      segment.tool.status === "building" &&
      (!fallbackName || segment.tool.name === fallbackName)
    ) {
      return index;
    }
  }
  return -1;
}

function reduceToolDelta(
  state: ConversationState,
  event: ConversationToolDeltaEvent,
): ConversationState {
  const slotKey = state.toolCallKeysByIndex[event.index] || null;
  let segmentIndex = findToolSegmentIndex(
    state.streamingSegments,
    event.callId || slotKey,
  );
  if (segmentIndex < 0 && event.callId && slotKey) {
    segmentIndex = findToolSegmentIndex(state.streamingSegments, slotKey);
  }
  if (segmentIndex < 0 && !event.callId) {
    segmentIndex = findToolSegmentIndex(
      state.streamingSegments,
      null,
      event.name,
    );
  }

  const candidate = segmentIndex >= 0
    ? state.streamingSegments[segmentIndex]
    : undefined;
  const existing = candidate?.type === "tool" ? candidate.tool : null;
  const startsNewCall = Boolean(
    existing &&
      ((event.callId && existing.id && event.callId !== existing.id) ||
        (event.name && existing.name && event.name !== existing.name) ||
        existing.status !== "building"),
  );
  if (startsNewCall) {
    segmentIndex = -1;
  }

  const provisionalKey =
    event.callId ||
    (segmentIndex >= 0 && existing?.run_id) ||
    `pending:${event.generationId || "legacy"}:${event.index}:${event.revision ?? "x"}`;
  const tool: ToolCallState = {
    id: event.callId || (startsNewCall ? null : existing?.id) || null,
    index: event.index,
    run_id: provisionalKey,
    name: event.name || (startsNewCall ? "" : existing?.name) || "",
    args:
      segmentIndex >= 0 && !startsNewCall
        ? `${existing?.args || ""}${event.argumentsDelta}`
        : event.argumentsDelta,
    status: "building",
  };
  const nextSegment = { type: "tool" as const, tool };
  const streamingSegments = [...state.streamingSegments];
  if (segmentIndex >= 0) {
    streamingSegments[segmentIndex] = nextSegment;
  } else {
    streamingSegments.push(nextSegment);
  }
  return {
    ...state,
    streamingSegments,
    toolCallKeysByIndex: {
      ...state.toolCallKeysByIndex,
      [event.index]: provisionalKey,
    },
  };
}

function reduceToolStart(
  state: ConversationState,
  event: ConversationToolStartEvent,
): ConversationState {
  const slotKey = event.index === null
    ? null
    : state.toolCallKeysByIndex[event.index] || null;
  let segmentIndex = findToolSegmentIndex(
    state.streamingSegments,
    slotKey,
    event.name,
  );
  if (segmentIndex < 0) {
    segmentIndex = findToolSegmentIndex(
      state.streamingSegments,
      null,
      event.name,
    );
  }
  const candidate = segmentIndex >= 0
    ? state.streamingSegments[segmentIndex]
    : undefined;
  const existing = candidate?.type === "tool" ? candidate.tool : null;
  const tool: ToolCallState = {
    id: existing?.id || null,
    ...(event.index === null && existing?.index === undefined
      ? {}
      : { index: event.index ?? existing?.index }),
    run_id: event.runId,
    name: event.name || existing?.name || "",
    args: JSON.stringify(event.arguments),
    status: "running",
  };
  const nextSegment = { type: "tool" as const, tool };
  const streamingSegments = [...state.streamingSegments];
  if (segmentIndex >= 0) {
    streamingSegments[segmentIndex] = nextSegment;
  } else {
    streamingSegments.push(nextSegment);
  }
  return {
    ...state,
    streamingSegments,
    toolCallKeysByIndex:
      event.index === null
        ? state.toolCallKeysByIndex
        : { ...state.toolCallKeysByIndex, [event.index]: event.runId },
  };
}

function eventHasWrongGeneration(
  state: ConversationState,
  event: ConversationServerEvent,
): boolean {
  return Boolean(
    state.generationId &&
      event.generationId &&
      state.generationId !== event.generationId &&
      event.kind !== "stream_start",
  );
}

function markRevisionGap(
  state: ConversationState,
  received: number,
): ConversationState {
  const expected = (state.revision || 0) + 1;
  return {
    ...state,
    needsResync: true,
    syncIssue: { type: "revision_gap", expected, received },
    phase: "reconnecting",
  };
}

function withAcceptedRevision(
  state: ConversationState,
  event: ConversationServerEvent,
): ConversationState {
  return event.revision === null
    ? state
    : { ...state, revision: event.revision };
}

function reduceServerEvent(
  state: ConversationState,
  event: ConversationServerEvent,
): ConversationState {
  if (state.sessionId !== event.sessionId) return state;

  if (event.kind === "snapshot") {
    if (event.legacy && state.revision !== null && state.isStreaming) {
      return state;
    }
    const activeStream = event.activeStream;
    const waitingForActiveStream = event.status === "streaming" && !activeStream;
    const toolCallKeysByIndex: Record<number, string> = {};
    for (const segment of activeStream?.segments || []) {
      if (segment.type === "tool" && segment.tool.index !== undefined) {
        toolCallKeysByIndex[segment.tool.index] = segment.tool.run_id;
      }
    }
    return {
      ...state,
      generationId: activeStream?.generationId || null,
      revision: event.revision ?? activeStream?.revision ?? null,
      status: event.status,
      phase:
        event.status === "error"
          ? "error"
          : activeStream
            ? "streaming"
            : waitingForActiveStream
              ? "reconnecting"
              : "ready",
      messages: normalizeMessages(event.messages),
      streamingSegments: activeStream?.segments || [],
      isStreaming: Boolean(activeStream) || waitingForActiveStream,
      tokenUsage: event.tokenUsage,
      needsResync: waitingForActiveStream,
      syncIssue: null,
      error: event.status === "error" ? event.error : null,
      toolCallKeysByIndex,
    };
  }

  if (
    state.phase === "error" &&
    event.kind !== "stream_start" &&
    event.kind !== "chain_end"
  ) {
    return state;
  }

  if (eventHasWrongGeneration(state, event)) return state;

  if (event.kind === "chain_end") {
    if (
      event.revision !== null &&
      state.revision !== null &&
      event.revision <= state.revision
    ) {
      return state;
    }
    return {
      ...state,
      generationId: null,
      revision: event.revision ?? state.revision,
      status: "completed",
      phase: "ready",
      messages: normalizeMessages(event.messages),
      streamingSegments: [],
      isStreaming: false,
      tokenUsage: event.tokenUsage || state.tokenUsage,
      needsResync: false,
      syncIssue: null,
      error: null,
      toolCallKeysByIndex: {},
    };
  }

  if (state.needsResync && event.kind !== "stream_start" && event.kind !== "error") {
    return state;
  }

  if (
    event.revision !== null &&
    state.revision !== null &&
    event.revision <= state.revision
  ) {
    return state;
  }
  if (
    event.revision !== null &&
    state.revision !== null &&
    event.revision > state.revision + 1 &&
    event.kind !== "stream_start" &&
    event.kind !== "error"
  ) {
    return markRevisionGap(state, event.revision);
  }

  const next = withAcceptedRevision(state, event);
  switch (event.kind) {
    case "stream_start":
      return {
        ...next,
        generationId: event.generationId,
        phase: "streaming",
        status: "streaming",
        streamingSegments: [],
        isStreaming: true,
        needsResync: false,
        syncIssue: null,
        error: null,
        toolCallKeysByIndex: {},
      };
    case "text_delta":
    case "reasoning_delta":
      return {
        ...next,
        phase: "streaming",
        isStreaming: true,
        streamingSegments: appendContentSegment(
          next.streamingSegments,
          event.kind === "text_delta" ? "text" : "reasoning",
          event.content,
        ),
      };
    case "tool_delta":
      return reduceToolDelta(
        { ...next, phase: "streaming", isStreaming: true },
        event,
      );
    case "tool_start":
      return reduceToolStart(
        { ...next, phase: "streaming", isStreaming: true },
        event,
      );
    case "tool_end":
      return {
        ...next,
        streamingSegments: next.streamingSegments.map((segment) =>
          segment.type === "tool" && segment.tool.run_id === event.runId
            ? {
                type: "tool" as const,
                tool: {
                  ...segment.tool,
                  result: event.result,
                  status: event.status,
                },
              }
            : segment,
        ),
      };
    case "stream_end":
      // chain_end follows shortly; retain the authoritative draft to avoid flicker.
      return next;
    case "usage":
      return { ...next, tokenUsage: event.tokenUsage };
    case "error":
      if (!event.terminal) {
        return {
          ...next,
          error: event.message,
          needsResync: true,
          syncIssue: null,
        };
      }
      return {
        ...next,
        generationId: null,
        phase: "error",
        status: "error",
        streamingSegments: [],
        isStreaming: false,
        error: event.message,
        needsResync: true,
        syncIssue: null,
        toolCallKeysByIndex: {},
      };
    default:
      return next;
  }
}

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  switch (action.type) {
    case "select_session":
      return action.sessionId === state.sessionId
        ? state
        : createConversationState(action.sessionId);
    case "connection_changed":
      return {
        ...state,
        connected: action.connected,
        phase:
          !action.connected && state.isStreaming
            ? "reconnecting"
            : state.phase,
      };
    case "server_event":
      return reduceServerEvent(state, action.event);
    case "replace_messages":
      return action.sessionId === state.sessionId && state.phase === "loading"
        ? {
            ...state,
            messages: normalizeMessages(action.messages),
            phase: "ready",
            error: null,
          }
        : state;
    case "append_optimistic_message":
      return action.sessionId === state.sessionId
        ? {
            ...state,
            messages: normalizeMessages([...state.messages, action.message]),
          }
        : state;
    case "edit_optimistic_message": {
      if (action.sessionId !== state.sessionId) return state;
      const targetIndex = state.messages.findIndex(
        (message) =>
          message.id === action.messageId && message.type === "user",
      );
      if (targetIndex < 0) return state;
      return {
        ...state,
        messages: normalizeMessages([
          ...state.messages.slice(0, targetIndex),
          { type: "user", content: action.content },
        ]),
      };
    }
    case "clear_error":
      return {
        ...state,
        error: null,
        phase: state.isStreaming ? "streaming" : "ready",
      };
    default:
      return state;
  }
}
