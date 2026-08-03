import assert from "node:assert/strict";
import test from "node:test";

import { distanceFromBottom, isNearBottom } from "./useAutoFollowOutput.ts";

test("auto-follow stays enabled only when the viewport is near the bottom", () => {
  assert.equal(distanceFromBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 200 }), 100);
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 200 }, 120), true);
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 200 }, 120), false);
});

test("distance clamps negative layout values to zero", () => {
  assert.equal(distanceFromBottom({ scrollHeight: 100, scrollTop: 20, clientHeight: 120 }), 0);
});
