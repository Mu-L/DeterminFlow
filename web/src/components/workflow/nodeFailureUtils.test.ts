import assert from "node:assert/strict";
import test from "node:test";

import {
  formatAttemptTrigger,
  formatRetryCountdown,
  getAttemptCount,
  getControlAttemptCount,
  getNodeFailureActions,
  secondsUntilRetry,
} from "./nodeFailureUtils";

test("attempt history uses the Core auto_retry trigger name", () => {
  assert.equal(formatAttemptTrigger("auto_retry"), "自动重试");
  assert.equal(formatAttemptTrigger("manual_retry"), "人工重试");
});

test("backend available_actions is authoritative, including an explicit empty list", () => {
  assert.deepEqual(
    getNodeFailureActions({ status: "retry_waiting", available_actions: ["retry", "skip", "unknown"] }),
    ["retry", "skip"],
  );
  assert.deepEqual(
    getNodeFailureActions({ status: "failed", available_actions: [] }),
    [],
  );
});

test("legacy failed nodes expose retry and skip when available_actions is absent", () => {
  assert.deepEqual(getNodeFailureActions({ status: "failed" }), ["retry", "skip"]);
  assert.deepEqual(getNodeFailureActions({ status: "retry_waiting" }), []);
  assert.equal(getAttemptCount({ status: "failed" }), 1);
  assert.equal(getControlAttemptCount({ attempt_count: 0 }), 0);
  assert.equal(getControlAttemptCount({}), 0);
});

test("retry countdown uses the persisted absolute retry time", () => {
  const now = Date.parse("2026-07-22T10:00:00Z");
  assert.equal(secondsUntilRetry("2026-07-22T10:01:05Z", now), 65);
  assert.equal(formatRetryCountdown(65), "1 分 5 秒");
  assert.equal(secondsUntilRetry("2026-07-22T09:59:00Z", now), 0);
  assert.equal(secondsUntilRetry("not-a-date", now), null);
});
