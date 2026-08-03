export type ContentSafetyDiagnosticControlEvent =
  | {
      kind: "accepted";
      sessionId: string;
      requestId: string;
    }
  | {
      kind: "result";
      sessionId: string;
      requestId: string;
      success: boolean;
      message: string | null;
    };

export type ContentSafetyDiagnosticRequestState =
  | { phase: "idle"; requestId: null; message: null }
  | { phase: "submitting" | "accepted"; requestId: string; message: null }
  | { phase: "completed"; requestId: string; message: string | null }
  | { phase: "failed"; requestId: string | null; message: string };

type DiagnosticRequestAction =
  | { type: "reset" }
  | { type: "sent"; requestId: string }
  | { type: "send_failed"; message: string }
  | { type: "control_event"; event: ContentSafetyDiagnosticControlEvent };

const listeners = new Set<(event: ContentSafetyDiagnosticControlEvent) => void>();

export const initialContentSafetyDiagnosticRequestState: ContentSafetyDiagnosticRequestState = {
  phase: "idle",
  requestId: null,
  message: null,
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

export function normalizeContentSafetyDiagnosticControlEvent(
  value: unknown,
): ContentSafetyDiagnosticControlEvent | null {
  const event = asRecord(value);
  if (!event) return null;
  const sessionId = typeof event.session_id === "string" ? event.session_id : "";
  const requestId = typeof event.request_id === "string" ? event.request_id : "";
  if (!sessionId || !requestId) return null;

  if (event.type === "content_safety_diagnostic_accepted") {
    return { kind: "accepted", sessionId, requestId };
  }
  if (event.type === "content_safety_diagnostic_result") {
    if (typeof event.success !== "boolean") return null;
    return {
      kind: "result",
      sessionId,
      requestId,
      success: event.success,
      message: typeof event.message === "string" ? event.message : null,
    };
  }
  // Compatibility for servers that correlate a recoverable command error.
  if (event.type === "error" && event.terminal === false) {
    return {
      kind: "result",
      sessionId,
      requestId,
      success: false,
      message: typeof event.message === "string" ? event.message : "诊断请求失败",
    };
  }
  return null;
}

export function publishContentSafetyDiagnosticControlEvent(value: unknown): boolean {
  const event = normalizeContentSafetyDiagnosticControlEvent(value);
  if (!event) return false;
  for (const listener of listeners) listener(event);
  return true;
}

export function subscribeContentSafetyDiagnosticControlEvent(
  listener: (event: ContentSafetyDiagnosticControlEvent) => void,
): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function contentSafetyDiagnosticRequestReducer(
  state: ContentSafetyDiagnosticRequestState,
  action: DiagnosticRequestAction,
): ContentSafetyDiagnosticRequestState {
  if (action.type === "reset") return initialContentSafetyDiagnosticRequestState;
  if (action.type === "sent") {
    return { phase: "submitting", requestId: action.requestId, message: null };
  }
  if (action.type === "send_failed") {
    return { phase: "failed", requestId: null, message: action.message };
  }

  if (!state.requestId || action.event.requestId !== state.requestId) return state;
  if (action.event.kind === "accepted") {
    if (state.phase === "completed") return state;
    return { phase: "accepted", requestId: state.requestId, message: null };
  }
  if (action.event.success) {
    return {
      phase: "completed",
      requestId: state.requestId,
      message: action.event.message,
    };
  }
  return {
    phase: "failed",
    requestId: null,
    message: action.event.message || "诊断请求失败，请重试",
  };
}

export function createContentSafetyDiagnosticRequestId(): string {
  return globalThis.crypto.randomUUID();
}
