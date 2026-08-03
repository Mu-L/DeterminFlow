import assert from "node:assert/strict";
import test from "node:test";

import { resolveNodeSessionId } from "./resolveNodeSession";

test("REST node history replaces an old runtime attempt after retry", () => {
  assert.equal(
    resolveNodeSessionId("attempt-old", "attempt-new", "attempt-old"),
    "attempt-new",
  );
});

test("a runtime attempt changed after REST restore wins until the next restore", () => {
  assert.equal(
    resolveNodeSessionId("attempt-new", "attempt-old", "attempt-old"),
    "attempt-new",
  );
});

test("REST history remains available when runtime state has no session", () => {
  assert.equal(resolveNodeSessionId(null, "attempt-history", null), "attempt-history");
});
