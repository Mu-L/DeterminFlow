import type { Message, ToolCall } from "../../types";
import type { NormalizedMessage } from "./conversationTypes";

const LEGACY_TYPE_MAP: Record<string, string> = {
  ai: "assistant",
  human: "user",
};

function normalizeMessageType(message: Message): string {
  const rawType = message.type || message.role || "unknown";
  return LEGACY_TYPE_MAP[rawType] || rawType;
}

function cloneToolCalls(toolCalls: ToolCall[] | undefined): ToolCall[] | undefined {
  return toolCalls?.map((toolCall) => ({
    ...toolCall,
    function: { ...toolCall.function },
  }));
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stableValue);
  }
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(object).sort()) {
      if (object[key] === undefined) {
        continue;
      }
      sorted[key] = stableValue(object[key]);
    }
    return sorted;
  }
  return value;
}

function hashString(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36).padStart(7, "0");
}

function uniqueId(desiredId: string, usedIds: Map<string, number>): string {
  const occurrence = (usedIds.get(desiredId) || 0) + 1;
  usedIds.set(desiredId, occurrence);
  return occurrence === 1 ? desiredId : `${desiredId}__${occurrence}`;
}

/**
 * Produces immutable, render-ready messages.
 *
 * Missing message IDs are derived from content rather than array position so an
 * append-only history keeps stable React keys. Tool results are copied onto the
 * matching assistant tool call while the original tool message is preserved.
 */
export function normalizeMessages(messages: readonly Message[]): NormalizedMessage[] {
  const toolResults = new Map<string, string>();
  for (const message of messages) {
    if (
      normalizeMessageType(message) === "tool" &&
      message.tool_call_id
    ) {
      toolResults.set(message.tool_call_id, message.content || "");
    }
  }

  const fingerprintOccurrences = new Map<string, number>();
  const usedIds = new Map<string, number>();

  return messages.map((message) => {
    const type = normalizeMessageType(message);
    const toolCalls = cloneToolCalls(message.tool_calls)?.map((toolCall) => {
      const result = toolResults.get(toolCall.id);
      if (result === undefined) {
        return toolCall;
      }
      return {
        ...toolCall,
        function: { ...toolCall.function, result },
      };
    });
    const fingerprintSource = {
      ...message,
      id: undefined,
      type,
      tool_calls: cloneToolCalls(message.tool_calls)?.map((toolCall) => ({
        ...toolCall,
        function: {
          name: toolCall.function.name,
          arguments: toolCall.function.arguments,
        },
      })),
    };
    const fingerprint = JSON.stringify(stableValue(fingerprintSource));
    const fingerprintOccurrence =
      (fingerprintOccurrences.get(fingerprint) || 0) + 1;
    fingerprintOccurrences.set(fingerprint, fingerprintOccurrence);
    const generatedId = `msg_auto_${hashString(fingerprint)}_${fingerprintOccurrence}`;
    const id = uniqueId(message.id?.trim() || generatedId, usedIds);

    return {
      ...message,
      id,
      type,
      ...(toolCalls ? { tool_calls: toolCalls } : {}),
    };
  });
}
