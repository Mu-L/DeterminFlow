import type { Message, StreamingSegment } from "../../types";
import type {
  ConversationTimelineEntry,
  ToolInvocationModel,
  ToolInvocationStatus,
  ToolStatusInput,
} from "./conversationTypes";

type MessageWithToolMetadata = Message & {
  status?: unknown;
  tool_status?: unknown;
  is_error?: unknown;
  error?: unknown;
  cancelled?: unknown;
  canceled?: unknown;
};

const FAILURE_STATUSES = new Set(["error", "failed", "failure"]);
const CANCELLED_STATUSES = new Set(["cancelled", "canceled", "aborted", "stopped"]);
const SUCCEEDED_STATUSES = new Set(["completed", "complete", "success", "succeeded"]);

function normalizedStatus(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function hasFailureMarker(value?: string): boolean {
  if (!value) return false;
  return /^\s*(?:\[错误\]|\[error\]|error\s*:|tool\s+error\s*:)/i.test(value);
}

function hasCancellationMarker(value?: string): boolean {
  if (!value) return false;
  return /^\s*(?:\[(?:已取消|取消|cancelled|canceled|aborted)\]|(?:cancelled|canceled|aborted)\s*:)/i.test(value);
}

export function resolveToolStatus(input: ToolStatusInput): ToolInvocationStatus {
  const status = normalizedStatus(input.status);

  if (input.cancelled || CANCELLED_STATUSES.has(status) || hasCancellationMarker(input.error) || hasCancellationMarker(input.result)) {
    return "cancelled";
  }
  if (input.isError || FAILURE_STATUSES.has(status) || !!input.error || hasFailureMarker(input.result)) {
    return "failed";
  }
  if (status === "building") return "building";
  if (status === "running") return "running";
  if (status === "pending" || status === "queued") return "pending";
  if (SUCCEEDED_STATUSES.has(status)) return "succeeded";
  if (input.result !== undefined) return "succeeded";
  return "pending";
}

function messageType(message: Message): string {
  return message.type || message.role || "";
}

function messageToolMetadata(message: MessageWithToolMetadata): ToolStatusInput {
  const error = typeof message.error === "string" && message.error.trim() ? message.error : undefined;
  return {
    status: message.tool_status ?? message.status,
    result: message.content,
    error,
    isError: message.is_error === true,
    cancelled: message.cancelled === true || message.canceled === true,
  };
}

function resultByToolCallId(messages: Message[]): Map<string, MessageWithToolMetadata> {
  const results = new Map<string, MessageWithToolMetadata>();
  for (const message of messages) {
    if (messageType(message) === "tool" && message.tool_call_id) {
      results.set(message.tool_call_id, message as MessageWithToolMetadata);
    }
  }
  return results;
}

function referencedToolCallIds(messages: Message[]): Set<string> {
  const ids = new Set<string>();
  for (const message of messages) {
    for (const toolCall of message.tool_calls || []) {
      if (toolCall.id) ids.add(toolCall.id);
    }
  }
  return ids;
}

function invocationFromHistory(
  toolCall: NonNullable<Message["tool_calls"]>[number],
  resultMessage?: MessageWithToolMetadata,
): ToolInvocationModel {
  const embeddedResult = toolCall.function.result;
  const result = resultMessage?.content ?? embeddedResult;
  const metadata = resultMessage ? messageToolMetadata(resultMessage) : { result };
  const status = resolveToolStatus({ ...metadata, result });
  return {
    id: toolCall.id,
    name: toolCall.function.name || "工具调用",
    arguments: toolCall.function.arguments || "",
    result,
    error: metadata.error,
    status,
  };
}

function invocationFromOrphanResult(message: MessageWithToolMetadata, index: number): ToolInvocationModel {
  const metadata = messageToolMetadata(message);
  return {
    id: message.tool_call_id || message.id || `orphan-tool-${index}`,
    name: message.name || "工具调用",
    arguments: "",
    result: message.content,
    error: metadata.error,
    status: resolveToolStatus(metadata),
  };
}

function invocationFromLiveSegment(segment: Extract<StreamingSegment, { type: "tool" }>): ToolInvocationModel {
  const tool = segment.tool;
  return {
    id: tool.id || tool.run_id,
    name: tool.name || "工具调用",
    arguments: tool.args || "",
    result: tool.result,
    status: resolveToolStatus({ status: tool.status, result: tool.result }),
  };
}

function shouldRenderMessage(message: Message): boolean {
  const type = messageType(message);
  if (type === "system" || type === "system_prompt" || type === "tool") return false;
  if (type !== "assistant") return true;
  return !!message.content || !!message.reasoning_content || !message.tool_calls?.length;
}

export function normalizeConversationTimeline(
  messages: Message[],
  liveSegments: StreamingSegment[] = [],
): ConversationTimelineEntry[] {
  const entries: ConversationTimelineEntry[] = [];
  const results = resultByToolCallId(messages);
  const referencedIds = referencedToolCallIds(messages);
  const toolEntryById = new Map<string, number>();

  messages.forEach((message, messageIndex) => {
    const type = messageType(message);

    if (type === "tool") {
      if (message.tool_call_id && referencedIds.has(message.tool_call_id)) return;
      const invocation = invocationFromOrphanResult(message as MessageWithToolMetadata, messageIndex);
      const entryIndex = entries.length;
      entries.push({
        kind: "tool",
        key: `orphan-tool-${invocation.id}-${messageIndex}`,
        invocation,
        streaming: false,
      });
      toolEntryById.set(invocation.id, entryIndex);
      return;
    }

    if (shouldRenderMessage(message)) {
      entries.push({
        kind: "message",
        key: message.id || `message-${messageIndex}`,
        message,
        streaming: false,
      });
    }

    for (const [toolIndex, toolCall] of (message.tool_calls || []).entries()) {
      const invocation = invocationFromHistory(toolCall, results.get(toolCall.id));
      const entryIndex = entries.length;
      entries.push({
        kind: "tool",
        key: `tool-${toolCall.id}-${messageIndex}-${toolIndex}`,
        invocation,
        streaming: false,
      });
      toolEntryById.set(invocation.id, entryIndex);
    }
  });

  liveSegments.forEach((segment, segmentIndex) => {
    if (segment.type === "reasoning") {
      entries.push({
        kind: "reasoning",
        key: `live-reasoning-${segmentIndex}`,
        content: segment.content,
        streaming: true,
      });
      return;
    }
    if (segment.type === "text") {
      entries.push({
        kind: "message",
        key: `live-message-${segmentIndex}`,
        message: { type: "assistant", content: segment.content },
        streaming: true,
      });
      return;
    }

    const invocation = invocationFromLiveSegment(segment);
    const existingIndex = toolEntryById.get(invocation.id);
    if (existingIndex !== undefined) {
      const existing = entries[existingIndex];
      if (existing.kind === "tool") {
        entries[existingIndex] = {
          ...existing,
          invocation: {
            ...existing.invocation,
            ...invocation,
            name: invocation.name || existing.invocation.name,
            arguments: invocation.arguments || existing.invocation.arguments,
            result: invocation.result ?? existing.invocation.result,
          },
          streaming: true,
        };
      }
      return;
    }

    toolEntryById.set(invocation.id, entries.length);
    entries.push({
      kind: "tool",
      key: `live-tool-${invocation.id}-${segmentIndex}`,
      invocation,
      streaming: true,
    });
  });

  return entries;
}

export function formatTechnicalValue(value: string): string {
  if (!value) return "";
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed === "object" && parsed !== null) {
      return JSON.stringify(parsed, null, 2);
    }
  } catch {
    // 流式 JSON 或普通文本保持原样。
  }
  return value;
}

export function isLongTechnicalValue(value: string, maxCharacters = 420, maxLines = 12): boolean {
  if (value.length > maxCharacters) return true;
  return value.split("\n").length > maxLines;
}

export function getTimelineContentVersion(entries: ConversationTimelineEntry[]): string {
  return entries.map((entry) => {
    if (entry.kind === "message") {
      return `${entry.key}:m:${entry.message.content?.length || 0}:${entry.message.reasoning_content?.length || 0}`;
    }
    if (entry.kind === "reasoning") return `${entry.key}:r:${entry.content.length}`;
    return `${entry.key}:t:${entry.invocation.status}:${entry.invocation.arguments.length}:${entry.invocation.result?.length || 0}`;
  }).join("|");
}
