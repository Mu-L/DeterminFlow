import assert from "node:assert/strict";
import test from "node:test";

import type { Message } from "../../types";
import { normalizeMessages } from "./normalizeMessages";
import {
  conversationReducer,
  createConversationState,
} from "./conversationReducer";
import { normalizeConversationEvent } from "./normalizeConversationEvent";

function dispatchWire(
  state: ReturnType<typeof createConversationState>,
  event: unknown,
) {
  const normalized = normalizeConversationEvent(event, state.sessionId);
  assert.ok(normalized, "wire event should normalize");
  return conversationReducer(state, { type: "server_event", event: normalized });
}

test("message normalization creates stable IDs and joins tool results by tool_call_id", () => {
  const messages: Message[] = [
    {
      id: "msg_00001",
      type: "assistant",
      content: "",
      tool_calls: [
        {
          id: "call_weather",
          type: "function",
          function: { name: "weather", arguments: '{"city":"上海"}' },
        },
      ],
    },
    {
      type: "tool",
      tool_call_id: "call_weather",
      content: "晴，28°C",
    },
    { role: "assistant", content: "完成" },
    { role: "assistant", content: "完成" },
  ];

  const first = normalizeMessages(messages);
  const second = normalizeMessages(structuredClone(messages));

  assert.equal(first[0].id, "msg_00001");
  assert.equal(first[0].tool_calls?.[0].function.result, "晴，28°C");
  assert.equal(first[1].type, "tool");
  assert.equal(first[2].type, "assistant");
  assert.notEqual(first[2].id, first[3].id);
  assert.deepEqual(first.map((message) => message.id), second.map((message) => message.id));
});

test("selecting a different session isolates old messages and accepts an authoritative empty snapshot", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 4,
    messages: [{ type: "assistant", content: "A 的历史" }],
    active_stream: null,
  });
  assert.equal(state.messages.length, 1);

  state = conversationReducer(state, {
    type: "select_session",
    sessionId: "session-b",
  });
  assert.equal(state.sessionId, "session-b");
  assert.deepEqual(state.messages, []);
  assert.equal(state.phase, "loading");

  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-b",
    status: "idle",
    revision: 0,
    messages: [],
    active_stream: null,
  });
  assert.deepEqual(state.messages, []);
  assert.equal(state.phase, "ready");

  const lateEvent = normalizeConversationEvent({
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 5,
    messages: [{ type: "assistant", content: "迟到的 A" }],
    active_stream: null,
  });
  assert.ok(lateEvent);
  const unchanged = conversationReducer(state, { type: "server_event", event: lateEvent });
  assert.deepEqual(unchanged, state);
});

test("late REST hydration cannot overwrite a canonical snapshot", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 2,
    messages: [{ type: "assistant", content: "权威消息" }],
    active_stream: null,
  });
  state = conversationReducer(state, {
    type: "replace_messages",
    sessionId: "session-a",
    messages: [{ type: "assistant", content: "迟到的 REST 消息" }],
  });
  assert.equal(state.messages[0].content, "权威消息");
});

test("REST hydration settles loading while preserving later snapshot authority", () => {
  let state = createConversationState("session-a");
  state = conversationReducer(state, {
    type: "replace_messages",
    sessionId: "session-a",
    messages: [],
  });

  assert.equal(state.phase, "ready");
  assert.deepEqual(state.messages, []);

  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 4,
    messages: [{ type: "assistant", content: "权威历史" }],
    active_stream: null,
  });
  assert.equal(state.messages[0].content, "权威历史");
  assert.equal(state.revision, 4);
});

test("an unavailable-session snapshot keeps its explicit error", () => {
  let state = createConversationState("missing-session");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "missing-session",
    status: "error",
    revision: 0,
    messages: [],
    active_stream: null,
    error: "未找到会话 missing-session",
  });
  assert.equal(state.phase, "error");
  assert.equal(state.error, "未找到会话 missing-session");
});

test("optimistic edit truncates later messages only after the command was accepted", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 3,
    messages: [
      { id: "user-1", type: "user", content: "旧问题" },
      { id: "assistant-1", type: "assistant", content: "旧回答" },
    ],
    active_stream: null,
  });
  state = conversationReducer(state, {
    type: "edit_optimistic_message",
    sessionId: "session-a",
    messageId: "user-1",
    content: "新问题",
  });
  assert.deepEqual(state.messages.map((message) => message.content), ["新问题"]);
});

test("an active snapshot restores a generation and authoritative draft segments", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 12,
    messages: [{ type: "user", content: "继续" }],
    active_stream: {
      generation_id: "generation-2",
      revision: 12,
      segments: [
        { type: "reasoning", content: "正在分析" },
        { type: "text", content: "当前草稿" },
      ],
    },
  });

  assert.equal(state.phase, "streaming");
  assert.equal(state.isStreaming, true);
  assert.equal(state.generationId, "generation-2");
  assert.equal(state.revision, 12);
  assert.deepEqual(state.streamingSegments, [
    { type: "reasoning", content: "正在分析" },
    { type: "text", content: "当前草稿" },
  ]);
});

test("an active snapshot preserves every terminal tool status", () => {
  // Given an authoritative active snapshot with completed, failed and cancelled tools.
  let state = createConversationState("session-a");

  // When the snapshot is normalized and reduced.
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 6,
    messages: [],
    active_stream: {
      generation_id: "generation-1",
      revision: 6,
      segments: [
        {
          type: "tool",
          tool: {
            run_id: "run-completed",
            name: "completed_tool",
            args: "{}",
            status: "completed",
          },
        },
        {
          type: "tool",
          tool: {
            run_id: "run-failed",
            name: "failed_tool",
            args: "{}",
            status: "failed",
          },
        },
        {
          type: "tool",
          tool: {
            run_id: "run-cancelled",
            name: "cancelled_tool",
            args: "{}",
            status: "cancelled",
          },
        },
      ],
    },
  });

  // Then reconnect recovery keeps the server's terminal outcomes unchanged.
  assert.deepEqual(
    state.streamingSegments.map((segment) =>
      segment.type === "tool" ? segment.tool.status : null,
    ),
    ["completed", "failed", "cancelled"],
  );
});

test("a streaming snapshot without an active draft stays in synchronization mode", () => {
  // Given a session the server still reports as generating.
  let state = createConversationState("session-a");

  // When its snapshot arrives before the active draft is available.
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 8,
    messages: [{ type: "user", content: "继续生成" }],
    active_stream: null,
  });

  // Then the client must not expose a ready/sendable conversation.
  assert.equal(state.phase, "reconnecting");
  assert.equal(state.isStreaming, true);
  assert.equal(state.status, "streaming");
  assert.equal(state.needsResync, true);
});

test("an active snapshot restores tool index correlation for later argument deltas", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 3,
    messages: [],
    active_stream: {
      generation_id: "generation-1",
      revision: 3,
      segments: [
        {
          type: "tool",
          tool: {
            id: "call-1",
            index: 0,
            name: "search",
            args: '{"q":',
            run_id: "call-1",
            status: "building",
          },
        },
      ],
    },
  });
  state = dispatchWire(state, {
    type: "tool_call_delta",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 4,
    index: 0,
    args_delta: '"DeterminFlow"}',
  });

  assert.equal(state.streamingSegments.length, 1);
  const segment = state.streamingSegments[0];
  assert.equal(segment.type, "tool");
  if (segment.type === "tool") {
    assert.equal(segment.tool.args, '{"q":"DeterminFlow"}');
    assert.equal(segment.tool.index, 0);
  }
});

test("a late tool call id upgrades the provisional indexed segment", () => {
  // Given a generation whose first tool delta has only an index.
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 1,
  });
  state = dispatchWire(state, {
    type: "tool_call_delta",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 2,
    index: 0,
    name: "search",
    args_delta: '{"q":',
  });

  // When a later delta reveals the stable call id for the same index.
  state = dispatchWire(state, {
    type: "tool_call_delta",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 3,
    index: 0,
    id: "call-1",
    args_delta: '"DeterminFlow"}',
  });

  // Then the provisional segment is upgraded instead of duplicated.
  assert.equal(state.streamingSegments.length, 1);
  const segment = state.streamingSegments[0];
  assert.equal(segment.type, "tool");
  if (segment.type === "tool") {
    assert.equal(segment.tool.id, "call-1");
    assert.equal(segment.tool.run_id, "call-1");
    assert.equal(segment.tool.args, '{"q":"DeterminFlow"}');
  }
});

test("revision duplicates and out-of-order events are ignored while a gap requests resync", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 1,
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 2,
    content: "Hel",
  });

  const duplicate = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 2,
    content: "Hel",
  });
  assert.deepEqual(duplicate.streamingSegments, [{ type: "text", content: "Hel" }]);

  state = dispatchWire(duplicate, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 4,
    content: "lo",
  });
  assert.equal(state.needsResync, true);
  assert.deepEqual(state.syncIssue, {
    type: "revision_gap",
    expected: 3,
    received: 4,
  });
  assert.deepEqual(state.streamingSegments, [{ type: "text", content: "Hel" }]);

  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 3,
    content: "ignored while waiting for snapshot",
  });
  assert.deepEqual(state.streamingSegments, [{ type: "text", content: "Hel" }]);

  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 4,
    messages: [],
    active_stream: {
      generation_id: "generation-1",
      revision: 4,
      segments: [{ type: "text", content: "Hello" }],
    },
  });
  assert.equal(state.needsResync, false);
  assert.equal(state.syncIssue, null);

  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 5,
    content: "!",
  });
  assert.deepEqual(state.streamingSegments, [{ type: "text", content: "Hello!" }]);
});

test("streaming text, reasoning and tool events share one ordered segment timeline", () => {
  let state = createConversationState("session-a");
  const events = [
    { type: "stream_start", generation_id: "generation-1", revision: 1 },
    { type: "reasoning_token", generation_id: "generation-1", revision: 2, content: "先分析" },
    { type: "token", generation_id: "generation-1", revision: 3, content: "我来查" },
    {
      type: "tool_call_delta",
      generation_id: "generation-1",
      revision: 4,
      index: 0,
      id: "call-1",
      name: "search",
      args_delta: '{"q":',
    },
    {
      type: "tool_call_delta",
      generation_id: "generation-1",
      revision: 5,
      index: 0,
      args_delta: '"DeterminFlow"}',
    },
    {
      type: "tool_start",
      generation_id: "generation-1",
      revision: 6,
      index: 0,
      run_id: "run-1",
      name: "search",
      args: { q: "DeterminFlow" },
    },
    {
      type: "tool_end",
      generation_id: "generation-1",
      revision: 7,
      run_id: "run-1",
      name: "search",
      result: "命中",
    },
    { type: "token", generation_id: "generation-1", revision: 8, content: "完成" },
  ];

  for (const event of events) {
    state = dispatchWire(state, { ...event, session_id: "session-a" });
  }

  assert.deepEqual(state.streamingSegments, [
    { type: "reasoning", content: "先分析" },
    { type: "text", content: "我来查" },
    {
      type: "tool",
      tool: {
        id: "call-1",
        index: 0,
        name: "search",
        args: '{"q":"DeterminFlow"}',
        run_id: "run-1",
        result: "命中",
        status: "completed",
      },
    },
    { type: "text", content: "完成" },
  ]);
});

test("tool_end preserves failed and cancelled outcomes", () => {
  // Given two running tools restored from the active generation snapshot.
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 2,
    messages: [],
    active_stream: {
      generation_id: "generation-1",
      revision: 2,
      segments: [
        {
          type: "tool",
          tool: {
            run_id: "run-failed",
            name: "failing_tool",
            args: "{}",
            status: "running",
          },
        },
        {
          type: "tool",
          tool: {
            run_id: "run-cancelled",
            name: "cancelled_tool",
            args: "{}",
            status: "running",
          },
        },
      ],
    },
  });

  // When the server finishes them with distinct non-success outcomes.
  state = dispatchWire(state, {
    type: "tool_end",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 3,
    run_id: "run-failed",
    name: "failing_tool",
    result: "boom",
    status: "failed",
  });
  state = dispatchWire(state, {
    type: "tool_end",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 4,
    run_id: "run-cancelled",
    name: "cancelled_tool",
    result: "stopped",
    status: "cancelled",
  });

  // Then the timeline exposes the exact terminal states.
  assert.deepEqual(
    state.streamingSegments.map((segment) =>
      segment.type === "tool" ? segment.tool.status : null,
    ),
    ["failed", "cancelled"],
  );
});

test("a non-terminal error preserves generation and requests optimistic rollback", () => {
  // Given an active generation with a valid partial response.
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 1,
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 2,
    content: "仍在",
  });

  // When a recoverable wire error is emitted for that generation.
  state = dispatchWire(state, {
    type: "error",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 3,
    message: "transient observer failure",
    terminal: false,
  });

  // Then the active generation remains visible while the client requests a snapshot.
  assert.equal(state.revision, 3);
  assert.equal(state.phase, "streaming");
  assert.equal(state.isStreaming, true);
  assert.equal(state.error, "transient observer failure");
  assert.equal(state.needsResync, true);
  assert.equal(state.generationId, "generation-1");

  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 4,
    content: "继续",
  });
  assert.deepEqual(state.streamingSegments, [
    { type: "text", content: "仍在" },
  ]);

  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "streaming",
    revision: 4,
    messages: [],
    active_stream: {
      generation_id: "generation-1",
      revision: 4,
      segments: [{ type: "text", content: "仍在继续" }],
    },
  });
  assert.equal(state.needsResync, false);
  assert.equal(state.error, null);
  assert.deepEqual(state.streamingSegments, [
    { type: "text", content: "仍在继续" },
  ]);
});

test("a terminal error discards partial draft and tools before authoritative resync", () => {
  // Given an active generation with partial text and a running tool.
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 1,
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 2,
    content: "partial",
  });
  state = dispatchWire(state, {
    type: "tool_start",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 3,
    run_id: "run-partial",
    name: "temporary_tool",
    args: {},
  });

  // When the server marks an error as terminal and a stale buffered token follows it.
  state = dispatchWire(state, {
    type: "error",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 4,
    message: "generation failed",
    terminal: true,
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 5,
    content: "must be ignored",
  });

  // Then no uncommitted output is retained and a snapshot is required.
  assert.equal(state.phase, "error");
  assert.equal(state.isStreaming, false);
  assert.equal(state.generationId, null);
  assert.equal(state.revision, 4);
  assert.equal(state.error, "generation failed");
  assert.equal(state.needsResync, true);
  assert.deepEqual(state.streamingSegments, []);
  assert.deepEqual(state.toolCallKeysByIndex, {});

  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "running",
    revision: 4,
    messages: [{ type: "user", content: "authoritative history" }],
    active_stream: null,
  });
  assert.equal(state.phase, "ready");
  assert.equal(state.needsResync, false);
  assert.equal(state.messages[0]?.content, "authoritative history");
});

test("chain_end is authoritative, reconciles a revision gap and rejects an old generation", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "session-a",
    generation_id: "generation-2",
    revision: 10,
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "session-a",
    generation_id: "generation-2",
    revision: 11,
    content: "草稿",
  });

  const oldGeneration = dispatchWire(state, {
    type: "chain_end",
    session_id: "session-a",
    generation_id: "generation-1",
    revision: 12,
    messages: [{ type: "assistant", content: "旧回复" }],
  });
  assert.deepEqual(oldGeneration, state);

  state = dispatchWire(state, {
    type: "chain_end",
    session_id: "session-a",
    generation_id: "generation-2",
    revision: 14,
    messages: [
      { type: "user", content: "问题" },
      { type: "assistant", content: "最终回复" },
    ],
  });
  assert.equal(state.phase, "ready");
  assert.equal(state.isStreaming, false);
  assert.equal(state.revision, 14);
  assert.equal(state.messages[state.messages.length - 1]?.content, "最终回复");
  assert.deepEqual(state.streamingSegments, []);
  assert.equal(state.needsResync, false);
});

test("a stale duplicate chain_end cannot overwrite a newer authoritative result", () => {
  let state = createConversationState("session-a");
  state = dispatchWire(state, {
    type: "snapshot",
    session_id: "session-a",
    status: "completed",
    revision: 20,
    messages: [{ type: "assistant", content: "较新的结果" }],
    active_stream: null,
  });
  state = dispatchWire(state, {
    type: "chain_end",
    session_id: "session-a",
    generation_id: "old-generation",
    revision: 19,
    messages: [{ type: "assistant", content: "迟到的旧结果" }],
  });
  assert.equal(state.messages[0].content, "较新的结果");
  assert.equal(state.revision, 20);
});

test("legacy history and unsequenced legacy streams remain supported", () => {
  let state = createConversationState("legacy-session");
  state = dispatchWire(state, {
    type: "history",
    session_id: "legacy-session",
    messages: [],
  });
  assert.equal(state.phase, "ready");
  assert.deepEqual(state.messages, []);

  state = dispatchWire(state, {
    type: "stream_start",
    session_id: "legacy-session",
  });
  state = dispatchWire(state, {
    type: "token",
    session_id: "legacy-session",
    content: "兼容旧事件",
  });
  assert.deepEqual(state.streamingSegments, [{ type: "text", content: "兼容旧事件" }]);
});
