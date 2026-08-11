import assert from "node:assert/strict";
import test from "node:test";

import { canDeleteMainSession } from "./sessionPolicy";
import type { Session } from "../types";

const historicalRunningMain = {
  session_id: "main-history",
  type: "main",
  status: "running",
} as Session;

test("historical Main sessions remain deletable while the active Main is protected", () => {
  assert.equal(canDeleteMainSession(historicalRunningMain, "main-current"), true);
  assert.equal(canDeleteMainSession(historicalRunningMain, "main-history"), false);
});
