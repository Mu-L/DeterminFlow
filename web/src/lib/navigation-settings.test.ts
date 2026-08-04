import assert from "node:assert/strict";
import test from "node:test";

import {
  SYSTEM_PROMPT_TAB_CONFIG_KEY,
  readSystemPromptTabVisibility,
} from "./navigation-settings";

test("system prompt tab stays hidden when the setting is absent or disabled", () => {
  assert.equal(readSystemPromptTabVisibility({}), false);
  assert.equal(readSystemPromptTabVisibility({ [SYSTEM_PROMPT_TAB_CONFIG_KEY]: false }), false);
  assert.equal(readSystemPromptTabVisibility({ [SYSTEM_PROMPT_TAB_CONFIG_KEY]: "false" }), false);
});

test("system prompt tab accepts persisted boolean-compatible values", () => {
  assert.equal(readSystemPromptTabVisibility({ [SYSTEM_PROMPT_TAB_CONFIG_KEY]: true }), true);
  assert.equal(readSystemPromptTabVisibility({ [SYSTEM_PROMPT_TAB_CONFIG_KEY]: "true" }), true);
  assert.equal(readSystemPromptTabVisibility({ [SYSTEM_PROMPT_TAB_CONFIG_KEY]: 1 }), true);
});
