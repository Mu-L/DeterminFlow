import type {
  StreamingSegment,
  TokenUsage,
  ToolCallState,
} from "../../types";
import type {
  ConversationServerEvent,
  ConversationToolEndEvent,
} from "./conversationTypes";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asRevision(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function asIndex(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function asTokenUsage(value: unknown): TokenUsage | null {
  return asRecord(value) as TokenUsage | null;
}

function normalizeToolState(value: unknown): ToolCallState | null {
  const tool = asRecord(value);
  if (!tool) return null;
  const runId = asString(tool.run_id);
  const name = asString(tool.name);
  const args = asString(tool.args);
  const rawStatus = asString(tool.status);
  if (!runId || name === null || args === null) return null;
  const status: ToolCallState["status"] =
    rawStatus === "running" ||
    rawStatus === "completed" ||
    rawStatus === "failed" ||
    rawStatus === "cancelled"
      ? rawStatus
      : "building";
  return {
    id: asString(tool.id),
    ...(asIndex(tool.index) === null ? {} : { index: asIndex(tool.index)! }),
    run_id: runId,
    name,
    args,
    status,
    ...(typeof tool.result === "string" ? { result: tool.result } : {}),
  };
}

function normalizeSegments(value: unknown): StreamingSegment[] {
  if (!Array.isArray(value)) return [];
  const segments: StreamingSegment[] = [];
  for (const rawSegment of value) {
    const segment = asRecord(rawSegment);
    if (!segment) continue;
    if (
      (segment.type === "text" || segment.type === "reasoning") &&
      typeof segment.content === "string"
    ) {
      segments.push({ type: segment.type, content: segment.content });
      continue;
    }
    if (segment.type === "tool") {
      const tool = normalizeToolState(segment.tool);
      if (tool) segments.push({ type: "tool", tool });
    }
  }
  return segments;
}

function normalizeToolEndStatus(
  value: unknown,
): ConversationToolEndEvent["status"] {
  return value === "failed" || value === "error"
    ? "failed"
    : value === "cancelled" || value === "canceled"
      ? "cancelled"
      : "completed";
}

/** Convert the canonical WS protocol and legacy history events to reducer events. */
export function normalizeConversationEvent(
  value: unknown,
  fallbackSessionId: string | null = null,
): ConversationServerEvent | null {
  const event = asRecord(value);
  if (!event) return null;
  const wireType = asString(event.type);
  const sessionId = asString(event.session_id) || fallbackSessionId;
  if (!wireType || !sessionId) return null;

  const generationId = asString(event.generation_id);
  const revision = asRevision(event.revision);
  const base = { sessionId, generationId, revision };

  if (wireType === "snapshot" || wireType === "history") {
    if (!Array.isArray(event.messages)) return null;
    const activeStream = asRecord(event.active_stream);
    const activeGenerationId = activeStream
      ? asString(activeStream.generation_id)
      : null;
    return {
      kind: "snapshot",
      ...base,
      generationId: activeGenerationId,
      messages: event.messages,
      status: asString(event.status),
      activeStream:
        activeStream && activeGenerationId
          ? {
              generationId: activeGenerationId,
              revision: asRevision(activeStream.revision),
              segments: normalizeSegments(activeStream.segments),
            }
          : null,
      tokenUsage: asTokenUsage(event.token_usage),
      error: asString(event.error),
      legacy: wireType === "history",
    };
  }

  switch (wireType) {
    case "stream_start":
      return { kind: "stream_start", ...base };
    case "token": {
      const content = asString(event.content);
      return content === null ? null : { kind: "text_delta", ...base, content };
    }
    case "reasoning_token": {
      const content = asString(event.content);
      return content === null
        ? null
        : { kind: "reasoning_delta", ...base, content };
    }
    case "tool_call_delta": {
      const index = asIndex(event.index);
      const argumentsDelta = asString(event.args_delta);
      if (index === null || argumentsDelta === null) return null;
      return {
        kind: "tool_delta",
        ...base,
        index,
        callId: asString(event.id),
        name: asString(event.name),
        argumentsDelta,
      };
    }
    case "tool_start": {
      const runId = asString(event.run_id);
      const name = asString(event.name);
      const args = asRecord(event.args);
      if (!runId || name === null || !args) return null;
      return {
        kind: "tool_start",
        ...base,
        index: asIndex(event.index),
        runId,
        name,
        arguments: args,
      };
    }
    case "tool_end": {
      const runId = asString(event.run_id);
      const name = asString(event.name);
      const result = asString(event.result);
      if (!runId || name === null || result === null) return null;
      return {
        kind: "tool_end",
        ...base,
        runId,
        name,
        result,
        status: normalizeToolEndStatus(event.status),
      };
    }
    case "chain_end":
      return Array.isArray(event.messages)
        ? {
            kind: "chain_end",
            ...base,
            messages: event.messages,
            tokenUsage: asTokenUsage(event.token_usage),
          }
        : null;
    case "stream_end":
      return { kind: "stream_end", ...base };
    case "error": {
      const message = asString(event.message);
      return message === null
        ? null
        : {
            kind: "error",
            ...base,
            message,
            terminal: event.terminal !== false,
          };
    }
    case "llm_usage": {
      const tokenUsage = asTokenUsage(event.data);
      return tokenUsage
        ? { kind: "usage", ...base, tokenUsage }
        : null;
    }
    default:
      return null;
  }
}

export { normalizeSegments };
