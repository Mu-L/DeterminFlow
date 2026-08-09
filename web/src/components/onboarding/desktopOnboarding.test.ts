import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_ONBOARDING_COMPLETE_VALUE,
  DESKTOP_ONBOARDING_PENDING_VALUE,
} from "./firstRunOnboardingModel";
import {
  ensureDesktopOnboardingStatus,
  markDesktopOnboardingComplete,
} from "./desktopOnboarding";

test("a desktop upgrade without state is marked pending", async () => {
  const calls: Array<{ command: string; status?: unknown }> = [];
  const status = await ensureDesktopOnboardingStatus(async (command, args) => {
    calls.push({ command, status: args?.status });
    return null;
  });

  assert.equal(status, DESKTOP_ONBOARDING_PENDING_VALUE);
  assert.deepEqual(calls, [
    { command: "get_desktop_onboarding_status", status: undefined },
    { command: "set_desktop_onboarding_status", status: DESKTOP_ONBOARDING_PENDING_VALUE },
  ]);
});

test("a completed desktop install does not overwrite its state", async () => {
  const calls: string[] = [];
  const status = await ensureDesktopOnboardingStatus(async (command) => {
    calls.push(command);
    return DESKTOP_ONBOARDING_COMPLETE_VALUE;
  });

  assert.equal(status, DESKTOP_ONBOARDING_COMPLETE_VALUE);
  assert.deepEqual(calls, ["get_desktop_onboarding_status"]);
});

test("completion writes the durable desktop state", async () => {
  const calls: Array<{ command: string; status?: unknown }> = [];
  await markDesktopOnboardingComplete(async (command, args) => {
    calls.push({ command, status: args?.status });
    return undefined;
  });

  assert.deepEqual(calls, [
    { command: "set_desktop_onboarding_status", status: DESKTOP_ONBOARDING_COMPLETE_VALUE },
  ]);
});
