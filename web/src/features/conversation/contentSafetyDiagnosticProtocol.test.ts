import assert from "node:assert/strict";
import test from "node:test";

import {
  contentSafetyDiagnosticRequestReducer,
  initialContentSafetyDiagnosticRequestState,
  normalizeContentSafetyDiagnosticControlEvent,
} from "./contentSafetyDiagnosticProtocol";

test("diagnostic accepted and success result stay correlated to one request", () => {
  let state = contentSafetyDiagnosticRequestReducer(
    initialContentSafetyDiagnosticRequestState,
    { type: "sent", requestId: "request-a" },
  );
  const accepted = normalizeContentSafetyDiagnosticControlEvent({
    type: "content_safety_diagnostic_accepted",
    session_id: "session-a",
    request_id: "request-a",
  });
  assert.ok(accepted);
  state = contentSafetyDiagnosticRequestReducer(state, {
    type: "control_event",
    event: accepted,
  });
  assert.equal(state.phase, "accepted");

  const unrelated = normalizeContentSafetyDiagnosticControlEvent({
    type: "content_safety_diagnostic_result",
    session_id: "session-a",
    request_id: "request-b",
    success: true,
  });
  assert.ok(unrelated);
  assert.equal(
    contentSafetyDiagnosticRequestReducer(state, {
      type: "control_event",
      event: unrelated,
    }),
    state,
  );

  const completed = normalizeContentSafetyDiagnosticControlEvent({
    type: "content_safety_diagnostic_result",
    session_id: "session-a",
    request_id: "request-a",
    success: true,
    message: "诊断完成",
  });
  assert.ok(completed);
  state = contentSafetyDiagnosticRequestReducer(state, {
    type: "control_event",
    event: completed,
  });
  assert.deepEqual(state, {
    phase: "completed",
    requestId: "request-a",
    message: "诊断完成",
  });
});

test("failed result and correlated nonterminal error restore retry state", () => {
  for (const eventValue of [
    {
      type: "content_safety_diagnostic_result",
      session_id: "session-a",
      request_id: "request-a",
      success: false,
      message: "会话正在生成",
    },
    {
      type: "error",
      session_id: "session-a",
      request_id: "request-a",
      terminal: false,
      message: "诊断不可用",
    },
  ]) {
    let state = contentSafetyDiagnosticRequestReducer(
      initialContentSafetyDiagnosticRequestState,
      { type: "sent", requestId: "request-a" },
    );
    const event = normalizeContentSafetyDiagnosticControlEvent(eventValue);
    assert.ok(event);
    state = contentSafetyDiagnosticRequestReducer(state, {
      type: "control_event",
      event,
    });
    assert.equal(state.phase, "failed");
    assert.equal(state.requestId, null);
    assert.ok(state.message);
  }
});
