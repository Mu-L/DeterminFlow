import assert from "node:assert/strict";
import test from "node:test";

import {
  contiguousRoundtableReplay,
  eventsAfterRoundtableSnapshot,
  getRoundtableEventRevision,
  shouldBufferRoundtableEvent,
  shouldHandleRoundtableEvent,
} from "./roundtableStream";
import { restoreRoundtableHistory } from "../hooks/useRoundtable";

test("roundtable stream ignores scoped events for inactive meetings", () => {
  assert.equal(
    shouldHandleRoundtableEvent("rt-active", {
      type: "rt_token",
      roundtable_id: "rt-other",
    }),
    false,
  );
  assert.equal(
    shouldHandleRoundtableEvent("rt-active", {
      type: "rt_token",
      roundtable_id: "rt-active",
    }),
    true,
  );
});

test("roundtable stream ignores scoped events until a meeting is selected", () => {
  assert.equal(
    shouldHandleRoundtableEvent(null, {
      type: "rt_started",
      roundtable_id: "rt-1",
    }),
    false,
  );
});

test("unscoped command results remain visible", () => {
  assert.equal(
    shouldHandleRoundtableEvent(null, { type: "rt_start_result" }),
    true,
  );
});

test("scoped events are buffered only while their detail snapshot is loading", () => {
  assert.equal(
    shouldBufferRoundtableEvent("rt-1", {
      type: "rt_token",
      roundtable_id: "rt-1",
    }),
    true,
  );
  assert.equal(
    shouldBufferRoundtableEvent("rt-1", {
      type: "rt_token",
      roundtable_id: "rt-2",
    }),
    false,
  );
  assert.equal(
    shouldBufferRoundtableEvent(null, {
      type: "rt_token",
      roundtable_id: "rt-1",
    }),
    false,
  );
});

test("snapshot watermark removes buffered events already represented by REST", () => {
  const events = [
    { type: "rt_token", roundtable_id: "rt-1", roundtable_revision: 7 },
    { type: "rt_turn_end", roundtable_id: "rt-1", roundtable_revision: 8 },
    { type: "rt_round_end", roundtable_id: "rt-1", roundtable_revision: 9 },
  ];

  assert.deepEqual(eventsAfterRoundtableSnapshot(8, events), [events[2]]);
  assert.equal(getRoundtableEventRevision(events[2]), 9);
  assert.equal(getRoundtableEventRevision({ type: "legacy" }), null);
});

test("buffered replay rejects a hidden revision gap without advancing", () => {
  const event = {
    type: "rt_round_end",
    roundtable_id: "rt-1",
    roundtable_revision: 10,
  };

  assert.deepEqual(contiguousRoundtableReplay(8, [event]), {
    events: [],
    pending: [event],
    hasGap: true,
  });
});

test("buffered replay sorts and returns only a contiguous suffix", () => {
  const ninth = { type: "rt_turn_end", roundtable_revision: 9 };
  const tenth = { type: "rt_round_end", roundtable_revision: 10 };
  assert.deepEqual(contiguousRoundtableReplay(8, [tenth, ninth]), {
    events: [ninth, tenth],
    pending: [],
    hasGap: false,
  });
});

test("roundtable detail restores summaries and structured conclusion from history", () => {
  const structuredConclusion = {
    summary: "最终结论",
    consensus: ["共识"],
    disagreements: ["分歧"],
    pending_verification: ["待验证"],
    action_items: ["行动项"],
  };

  const restored = restoreRoundtableHistory({
    transcript: [
      {
        speaker_seat_id: "system",
        speaker_name: "主持人",
        content: "第一轮摘要",
        round_number: 1,
        timestamp: "2026-08-03T00:00:00Z",
        entry_type: "summary",
      },
      {
        speaker_seat_id: "system",
        speaker_name: "主持人",
        content: "会议结论",
        round_number: 2,
        timestamp: "2026-08-03T00:01:00Z",
        entry_type: "conclusion",
      },
    ],
    shared_memory: {
      conclusions: [],
      consensus: [],
      controversies: [],
      summaries: [],
      structured_conclusion: structuredConclusion,
    },
  });

  assert.deepEqual(restored.roundSummaries, [
    { round: 1, content: "第一轮摘要", source: "主持人" },
  ]);
  assert.deepEqual(restored.conclusion, {
    content: "会议结论",
    source: "主持人",
  });
  assert.deepEqual(restored.structuredConclusion, structuredConclusion);
});
