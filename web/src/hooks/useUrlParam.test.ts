import assert from "node:assert/strict";
import test from "node:test";

import { patchSearchParams, readSearchParam } from "./useUrlParam";

test("patchSearchParams preserves unrelated state and encodes values", () => {
  const next = patchSearchParams("?tab=chat&workflow_id=wf-1", {
    tab: "workflow",
    task_id: "task / 2",
  });

  const params = new URLSearchParams(next);
  assert.equal(params.get("tab"), "workflow");
  assert.equal(params.get("workflow_id"), "wf-1");
  assert.equal(params.get("task_id"), "task / 2");
});

test("patchSearchParams removes empty values", () => {
  const next = patchSearchParams("?tab=chat&session_id=session-1", {
    session_id: null,
    node_id: "",
  });

  const params = new URLSearchParams(next);
  assert.equal(params.get("tab"), "chat");
  assert.equal(params.has("session_id"), false);
  assert.equal(params.has("node_id"), false);
});

test("readSearchParam treats missing and blank values as null", () => {
  assert.equal(readSearchParam("?session_id=session-1", "session_id"), "session-1");
  assert.equal(readSearchParam("?session_id=", "session_id"), null);
  assert.equal(readSearchParam("", "session_id"), null);
});
