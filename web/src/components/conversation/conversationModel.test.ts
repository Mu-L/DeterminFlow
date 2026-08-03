import assert from "node:assert/strict";
import test from "node:test";

import type { Message, StreamingSegment } from "../../types";
import {
  formatTechnicalValue,
  getTimelineContentVersion,
  isLongTechnicalValue,
  normalizeConversationTimeline,
  resolveToolStatus,
} from "./conversationModel.ts";

test("matches tool results by tool_call_id instead of message adjacency", () => {
  const messages: Message[] = [
    {
      id: "assistant-1",
      type: "assistant",
      content: "我会依次检查并保存。",
      tool_calls: [
        {
          id: "call-check",
          type: "function",
          function: { name: "check", arguments: '{"path":"/tmp/a"}' },
        },
        {
          id: "call-save",
          type: "function",
          function: { name: "save", arguments: '{"path":"/tmp/a"}' },
        },
      ],
    },
    { id: "tool-save", type: "tool", tool_call_id: "call-save", content: "[错误] 无写入权限" },
    { id: "assistant-2", type: "assistant", content: "检查仍可继续。" },
    { id: "tool-check", type: "tool", tool_call_id: "call-check", content: '{"exists":true}' },
  ];

  const entries = normalizeConversationTimeline(messages);
  const tools = entries.filter((entry) => entry.kind === "tool");

  assert.equal(tools.length, 2);
  assert.deepEqual(tools.map((entry) => entry.invocation.id), ["call-check", "call-save"]);
  assert.equal(tools[0].invocation.result, '{"exists":true}');
  assert.equal(tools[0].invocation.status, "succeeded");
  assert.equal(tools[1].invocation.result, "[错误] 无写入权限");
  assert.equal(tools[1].invocation.status, "failed");
  assert.equal(entries.some((entry) => entry.kind === "message" && entry.message.type === "tool"), false);
});

test("preserves unmatched tool results instead of hiding them", () => {
  const entries = normalizeConversationTimeline([
    { id: "tool-only", type: "tool", tool_call_id: "orphan-call", name: "lookup", content: "done" },
  ]);

  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind, "tool");
  if (entries[0].kind !== "tool") return;
  assert.equal(entries[0].invocation.id, "orphan-call");
  assert.equal(entries[0].invocation.name, "lookup");
  assert.equal(entries[0].invocation.status, "succeeded");
});

test("uses explicit failure and cancellation metadata before content heuristics", () => {
  assert.equal(resolveToolStatus({ status: "cancelled", result: "partial output" }), "cancelled");
  assert.equal(resolveToolStatus({ isError: true, result: "plain text" }), "failed");
  assert.equal(resolveToolStatus({ status: "completed", result: "[错误] denied" }), "failed");
  assert.equal(resolveToolStatus({ status: "completed", result: "ok" }), "succeeded");
  assert.equal(resolveToolStatus({}), "pending");
});

test("live tool state updates the matching persisted invocation without duplication", () => {
  const messages: Message[] = [{
    type: "assistant",
    tool_calls: [{
      id: "call-1",
      type: "function",
      function: { name: "read_file", arguments: '{"path":"a.txt"}' },
    }],
  }];
  const liveSegments: StreamingSegment[] = [{
    type: "tool",
    tool: {
      id: "call-1",
      run_id: "call-1",
      name: "read_file",
      args: '{"path":"a.txt"}',
      result: "hello",
      status: "completed",
    },
  }];

  const entries = normalizeConversationTimeline(messages, liveSegments);
  const tools = entries.filter((entry) => entry.kind === "tool");

  assert.equal(tools.length, 1);
  assert.equal(tools[0].invocation.status, "succeeded");
  assert.equal(tools[0].invocation.result, "hello");
  assert.equal(tools[0].streaming, true);
});

test("keeps live text, reasoning, and tools in event order", () => {
  const liveSegments: StreamingSegment[] = [
    { type: "reasoning", content: "先检查输入" },
    { type: "text", content: "正在处理" },
    {
      type: "tool",
      tool: { run_id: "call-live", name: "search", args: "{}", status: "running" },
    },
  ];

  const entries = normalizeConversationTimeline([], liveSegments);
  assert.deepEqual(entries.map((entry) => entry.kind), ["reasoning", "message", "tool"]);
  assert.equal(entries[2].kind === "tool" && entries[2].invocation.status, "running");
});

test("formats JSON without treating short values as expandable", () => {
  assert.equal(formatTechnicalValue('{"b":2,"a":1}'), '{\n  "b": 2,\n  "a": 1\n}');
  assert.equal(formatTechnicalValue("plain text"), "plain text");
  assert.equal(isLongTechnicalValue("short"), false);
  assert.equal(isLongTechnicalValue("line\n".repeat(20)), true);
});

test("timeline content version changes when streamed content changes", () => {
  const first = normalizeConversationTimeline([], [{ type: "text", content: "a" }]);
  const second = normalizeConversationTimeline([], [{ type: "text", content: "ab" }]);

  assert.notEqual(getTimelineContentVersion(first), getTimelineContentVersion(second));
});
