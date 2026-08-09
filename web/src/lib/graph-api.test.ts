import assert from "node:assert/strict";
import test from "node:test";

import { fetchGraphStructure } from "./api.ts";

test("reads the unified graph structure response", async (context) => {
  const graph = {
    name: "Agent Graph",
    nodes: [
      { id: "llm", label: "LLM", type: "llm", description: "调用模型" },
    ],
    edges: [
      { source: "__start__", target: "llm", label: "入口" },
    ],
  };

  context.mock.method(globalThis, "fetch", async () => {
    return new Response(JSON.stringify({ graph }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });

  assert.deepEqual(await fetchGraphStructure(), graph);
});
