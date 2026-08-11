import assert from "node:assert/strict";
import test from "node:test";

import {
  THEME_STORAGE_KEY,
  nextTheme,
  normalizeTheme,
  readStoredTheme,
  writeStoredTheme,
} from "./theme";

test("theme defaults to dark and accepts only supported values", () => {
  assert.equal(normalizeTheme(null), "dark");
  assert.equal(normalizeTheme("system"), "dark");
  assert.equal(normalizeTheme("dark"), "dark");
  assert.equal(normalizeTheme("light"), "light");
});

test("theme preference uses the versioned storage key", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };

  assert.equal(readStoredTheme(storage), "dark");
  writeStoredTheme("light", storage);
  assert.equal(values.get(THEME_STORAGE_KEY), "light");
  assert.equal(readStoredTheme(storage), "light");
});

test("theme toggle stays binary", () => {
  assert.equal(nextTheme("dark"), "light");
  assert.equal(nextTheme("light"), "dark");
});
