import assert from "node:assert/strict";
import test from "node:test";

import { resolveWorkflowTaskViewState } from "../../pages/WorkflowPage";

test("task restore blocks the canvas while loading or failed", () => {
  assert.equal(resolveWorkflowTaskViewState(false, null, false), "loading");
  assert.equal(resolveWorkflowTaskViewState(true, null, true), "loading");
  assert.equal(resolveWorkflowTaskViewState(true, "late error", true), "loading");
  assert.equal(resolveWorkflowTaskViewState(false, "load failed", true), "error");
  assert.equal(resolveWorkflowTaskViewState(false, null, true), "ready");
});
