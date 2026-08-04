import assert from "node:assert/strict";
import test from "node:test";

import {
  mergeUniqueModels,
  shouldShowModelSwitcher,
} from "./model-options";

test("discovered models fill the list without replacing manual order", () => {
  assert.deepEqual(
    mergeUniqueModels(
      ["manual-model", "shared-model"],
      ["shared-model", "discovered-model"],
    ),
    ["manual-model", "shared-model", "discovered-model"],
  );
});

test("model names are trimmed and empty values are ignored", () => {
  assert.deepEqual(
    mergeUniqueModels([], ["  model-a  ", "", "model-a"]),
    ["model-a"],
  );
});

test("model switcher is limited to interactive Main sessions", () => {
  assert.equal(
    shouldShowModelSwitcher({ type: "main", runtime_scope: "interactive" }),
    true,
  );
  assert.equal(
    shouldShowModelSwitcher({ type: "sub", runtime_scope: "interactive" }),
    false,
  );
  assert.equal(
    shouldShowModelSwitcher({ type: "main", runtime_scope: "workflow" }),
    false,
  );
  assert.equal(shouldShowModelSwitcher(null), false);
});
