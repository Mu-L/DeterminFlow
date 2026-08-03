import type { Message } from "../../types";

export type ToolInvocationStatus =
  | "pending"
  | "building"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ToolInvocationModel {
  id: string;
  name: string;
  arguments: string;
  result?: string;
  error?: string;
  status: ToolInvocationStatus;
}

export type ConversationTimelineEntry =
  | {
      kind: "message";
      key: string;
      message: Message;
      streaming: boolean;
    }
  | {
      kind: "reasoning";
      key: string;
      content: string;
      streaming: boolean;
    }
  | {
      kind: "tool";
      key: string;
      invocation: ToolInvocationModel;
      streaming: boolean;
    };

export interface ToolStatusInput {
  status?: unknown;
  result?: string;
  error?: string;
  isError?: boolean;
  cancelled?: boolean;
}
