import assert from "node:assert/strict";
import test from "node:test";

import { upsertWorkflowTask } from "./ChatWorkflowTasks";
import type { WorkflowTask } from "../../types";

function task(taskId: string, updatedAt: string): WorkflowTask {
  return {
    task_id: taskId,
    workflow_id: "wf_1",
    name: taskId,
    status: "running",
    current_node_id: null,
    run_id: null,
    created_at: updatedAt,
    updated_at: updatedAt,
    started_at: null,
    completed_at: null,
    node_states: {},
  };
}

test("upsertWorkflowTask updates one TaskRef without replacing sibling tasks", () => {
  const tasks = [
    task("task_a", "2026-08-03T01:00:00Z"),
    task("task_b", "2026-08-03T02:00:00Z"),
  ];

  const updated = upsertWorkflowTask(tasks, {
    workflow_id: "wf_1",
    task_id: "task_a",
    status: "completed",
    updated_at: "2026-08-03T03:00:00Z",
    progress: { completed: 3, total: 3 },
    main_takeover: true,
  });

  assert.deepEqual(updated.map((item) => item.task_id), ["task_a", "task_b"]);
  assert.equal(updated[0].status, "completed");
  assert.deepEqual(updated[0].progress, { completed: 3, total: 3 });
  assert.equal(updated[0].main_takeover, true);
  assert.equal(updated[1].status, "running");
});

test("upsertWorkflowTask inserts an event-only task with stable defaults", () => {
  const updated = upsertWorkflowTask([], {
    workflow_id: "wf_2",
    task_id: "task_c",
    status: "pre_running",
    name: "世界观规划",
    created_at: "2026-08-03T04:00:00Z",
  });

  assert.equal(updated[0].name, "世界观规划");
  assert.deepEqual(updated[0].node_states, {});
  assert.equal(updated[0].current_node_id, null);
});

test("upsertWorkflowTask ignores a stale snapshot", () => {
  const current = {
    ...task("task_a", "2026-08-03T03:00:00Z"),
    status: "completed" as const,
  };

  const updated = upsertWorkflowTask([current], {
    workflow_id: "wf_1",
    task_id: "task_a",
    status: "running",
    updated_at: "2026-08-03T02:59:00Z",
  });

  assert.equal(updated[0], current);
  assert.equal(updated[0].status, "completed");
});
