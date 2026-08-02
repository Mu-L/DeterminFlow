import assert from "node:assert/strict";
import test from "node:test";

import type { WorkflowDefinition } from "../../types";
import {
  buildWorkflowGraph,
  buildWorkflowSavePayload,
  getVariableReferences,
  resolveNodeExecutionInfo,
} from "./workflowCanvasUtils";

function createDefinition(nodeOverrides: Record<string, unknown>): WorkflowDefinition {
  return {
    workflow_id: "wf-test",
    name: "Test workflow",
    version: 1,
    created_at: "",
    updated_at: "",
    nodes: [{
      id: "node-1",
      label: "Process {{label_var}}",
      node_type: "script",
      agent_type: "default",
      system_prompt_template: "",
      first_message: "",
      position: { x: 0, y: 0 },
      ...nodeOverrides,
    }],
    edges: [],
  } as unknown as WorkflowDefinition;
}

test("collects placeholders while ignoring non-string node parameters", () => {
  const definition = createDefinition({
    node_params: {
      script_args: "--book-id {{book_id}}",
      timeout: 30,
      enable_reject_upstream: true,
      template_values: { style: "{{nested_value}}" },
      empty: null,
    },
  });

  assert.deepEqual(getVariableReferences(definition), {
    label_var: ["node-1"],
    book_id: ["node-1"],
  });
});

test("ignores malformed bindings and deduplicates valid references", () => {
  const definition = createDefinition({
    first_message: "Use {{shared_var}} twice: {{shared_var}}",
    var_bindings: {
      prompt: { original_value: "", var_key: "binding_var" },
      missing: null,
      malformed: 42,
    },
  });

  assert.deepEqual(getVariableReferences(definition), {
    label_var: ["node-1"],
    shared_var: ["node-1"],
    binding_var: ["node-1"],
  });
});

test("workflow save keeps generic node failure policy and model override", () => {
  const definition = createDefinition({
    model_override: "openai:test-model",
    auto_retry_count: 3,
    auto_retry_interval_seconds: 45,
    fail_auto_skip: true,
  });
  const nodes = [{
    id: "node-1",
    type: "workflowNode",
    position: { x: 10, y: 20 },
    data: {
      label: "Process",
      node_type: "script",
      agent_type: "default",
    },
  }];

  const payload = buildWorkflowSavePayload(definition, nodes, [], []);
  const saved = payload.nodes[0];
  assert.equal(saved.model_override, "openai:test-model");
  assert.equal(saved.auto_retry_count, 3);
  assert.equal(saved.auto_retry_interval_seconds, 45);
  assert.equal(saved.fail_auto_skip, true);
});

test("task graph keeps skipped nodes and original edges visible", () => {
  const definition = createDefinition({});
  definition.edges = [
    { id: "edge-start", source: "__start__", target: "node-1" },
    { id: "edge-end", source: "node-1", target: "__end__" },
  ];
  const graph = buildWorkflowGraph(
    definition,
    {
      "node-1": {
        node_id: "node-1",
        status: "skipped",
        session_id: "",
        summary: "",
        error: "failed before skip",
      },
    },
    true,
    true,
    false,
    false,
  );

  assert.ok(graph.nodes.some((node) => node.id === "node-1"));
  assert.deepEqual(
    graph.edges.map((edge) => [edge.source, edge.target]),
    [["__start__", "node-1"], ["node-1", "__end__"]],
  );
});

test("partial live state preserves persisted retry metadata", () => {
  const persisted = {
    node_id: "node-1",
    status: "failed" as const,
    session_id: "session-old",
    summary: "old summary",
    attempt_count: 2,
    attempt_history: [{ attempt_number: 1, status: "failed" }],
    input_snapshot: { prompt: "frozen" },
    available_actions: ["retry", "skip"],
  };

  const resolved = resolveNodeExecutionInfo(
    "node-1",
    { status: "retry_waiting", next_retry_at: "2026-07-22T10:01:00Z" },
    persisted,
  );

  assert.equal(resolved?.status, "retry_waiting");
  assert.equal(resolved?.session_id, "session-old");
  assert.equal(resolved?.attempt_count, 2);
  assert.deepEqual(resolved?.input_snapshot, { prompt: "frozen" });
  assert.deepEqual(resolved?.available_actions, ["retry", "skip"]);
});
