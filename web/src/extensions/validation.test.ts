import assert from "node:assert/strict";
import test from "node:test";

import type { ExtensionPage, ExtensionStatus, FrontendExtension } from "./types.ts";
import {
  isRunningFrontendExtension,
  parseLoadedFrontendExtension,
  validateFrontendExtensions,
} from "./validation.ts";

const component = () => null;
const icon = component as unknown as ExtensionPage["icon"];

function createStatus(overrides: Partial<ExtensionStatus> = {}): ExtensionStatus {
  return {
    id: "demo",
    name: "Demo",
    version: "1.0.0",
    description: "",
    enabled: true,
    status: "running",
    error: "",
    dependencies: [],
    capabilities: [],
    frontend: "demo",
    ...overrides,
  };
}

function createExtension(overrides: Partial<FrontendExtension> = {}): FrontendExtension {
  return {
    id: "demo",
    pages: [{
      id: "demo-page",
      label: "Demo",
      icon,
      activeClass: "",
      component,
    }],
    ...overrides,
  };
}

test("only enables frontend modules for running backend extensions", () => {
  assert.equal(isRunningFrontendExtension(createStatus()), true);
  assert.equal(isRunningFrontendExtension(createStatus({ status: "degraded" })), false);
  assert.equal(isRunningFrontendExtension(createStatus({ enabled: false })), false);
  assert.equal(isRunningFrontendExtension(createStatus({ frontend: "" })), false);
});

test("accepts a matching frontend module", () => {
  const result = validateFrontendExtensions(
    [{ status: createStatus(), extension: createExtension() }],
    ["chat", "workflow"],
  );

  assert.deepEqual(result.extensions.map((extension) => extension.id), ["demo"]);
  assert.deepEqual(result.errors, []);
});

test("isolates invalid module default exports", () => {
  const invalidValues = [undefined, null, "demo", {}, { id: "demo", pages: [null] }];
  for (const value of invalidValues) {
    const result = parseLoadedFrontendExtension(createStatus(), value);
    assert.ok("error" in result);
    assert.match(result.error.message, /default export/);
  }

  const invalidResult = parseLoadedFrontendExtension(createStatus(), undefined);
  const validStatus = createStatus({ id: "other", frontend: "other" });
  const validResult = parseLoadedFrontendExtension(
    validStatus,
    createExtension({ id: "other", pages: [{ id: "other-page", label: "Other", icon, activeClass: "", component }] }),
  );
  assert.ok("error" in invalidResult);
  assert.ok("entry" in validResult);

  const validation = validateFrontendExtensions([validResult.entry], []);
  assert.deepEqual(validation.extensions.map((extension) => extension.id), ["other"]);
});

test("rejects manifest and frontend module id mismatches", () => {
  const result = validateFrontendExtensions(
    [{
      status: createStatus({ frontend: "manifest-frontend" }),
      extension: createExtension({ id: "module-frontend" }),
    }],
    [],
  );

  assert.deepEqual(result.extensions, []);
  assert.equal(result.errors.length, 2);
});

test("rejects core and extension page id collisions", () => {
  const first = createExtension({
    pages: [{ id: "chat", label: "Core collision", icon, activeClass: "", component }],
  });
  const secondStatus = createStatus({ id: "other", frontend: "other" });
  const second = createExtension({
    id: "other",
    pages: [{ id: "demo-page", label: "Duplicate", icon, activeClass: "", component }],
  });
  const result = validateFrontendExtensions(
    [
      { status: createStatus(), extension: first },
      { status: secondStatus, extension: second },
    ],
    ["chat"],
  );

  assert.deepEqual(result.extensions.map((extension) => extension.id), ["other"]);
  assert.match(result.errors[0]?.message ?? "", /核心 Tab 冲突/);
});

test("rejects duplicate page ids across active extensions", () => {
  const secondStatus = createStatus({ id: "other", frontend: "other" });
  const second = createExtension({ id: "other" });
  const result = validateFrontendExtensions(
    [
      { status: createStatus(), extension: createExtension() },
      { status: secondStatus, extension: second },
    ],
    [],
  );

  assert.deepEqual(result.extensions.map((extension) => extension.id), ["demo"]);
  assert.match(result.errors[0]?.message ?? "", /已被扩展/);
});
