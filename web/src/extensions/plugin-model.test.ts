import assert from "node:assert/strict";
import test from "node:test";

import {
  coerceSettingsFieldValue,
  getPluginEnableAction,
  getPluginCatalogUpdate,
  getPluginTargetState,
  getRuntimeStatusMeta,
  getSettingsValue,
  isValidPluginId,
  parsePluginSettingsSchema,
  setSettingsValue,
  validatePluginSettings,
} from "./plugin-model.ts";
import type { PluginRecord } from "./plugin-types.ts";

function plugin(overrides: Partial<PluginRecord> = {}): PluginRecord {
  return {
    id: "demo-plugin",
    name: "Demo Plugin",
    description: "",
    resource_prefix: "demo",
    runtime_status: "running",
    error: "",
    active_enabled: true,
    desired_enabled: true,
    active_version: "1.0.0",
    desired_version: "1.0.0",
    restart_required: false,
    pending_action: null,
    dependencies: [],
    capabilities: [],
    source: {
      url: "bundled",
      ref: "",
      subdirectory: "",
      trust: "official",
      resolved_commit: "",
      content_sha256: "",
    },
    settings_schema: null,
    settings: {},
    config_present: false,
    page_url: null,
    processes: [],
    ...overrides,
  };
}

test("accepts only lowercase kebab-case plugin IDs", () => {
  for (const pluginId of ["novel-api", "memory", "plugin-2"]) {
    assert.equal(isValidPluginId(pluginId), true);
  }
  for (const pluginId of ["", "Novel-API", "novel_api", "novel.api", "-novel", "novel-"]) {
    assert.equal(isValidPluginId(pluginId), false);
  }
});


test("maps every host runtime status and safely falls back for unknown values", () => {
  const expectedLabels: Record<string, string> = {
    disabled: "未启用",
    discovered: "已发现",
    loaded: "已加载",
    starting: "启动中",
    running: "运行中",
    degraded: "已降级",
    blocked: "已阻塞",
    failed: "失败",
  };

  for (const [status, label] of Object.entries(expectedLabels)) {
    assert.equal(getRuntimeStatusMeta(status).label, label);
  }
  assert.deepEqual(getRuntimeStatusMeta("future-state"), {
    label: "未知（future-state）",
    variant: "outline",
  });
  assert.deepEqual(getRuntimeStatusMeta(""), {
    label: "未知",
    variant: "outline",
  });
  assert.deepEqual(getRuntimeStatusMeta("toString"), {
    label: "未知（toString）",
    variant: "outline",
  });
});

test("derives one reversible enable action from current and desired state", () => {
  assert.deepEqual(getPluginEnableAction(plugin()), {
    label: "停用",
    targetEnabled: false,
    kind: "disable",
    disabled: false,
  });
  assert.deepEqual(getPluginEnableAction(plugin({
    desired_enabled: false,
    restart_required: true,
  })), {
    label: "撤销停用",
    targetEnabled: true,
    kind: "undo",
    disabled: false,
  });
  assert.equal(getPluginEnableAction(plugin({
    pending_action: "remove",
    restart_required: true,
  })).disabled, true);
});

test("describes restart target without confusing it with current runtime", () => {
  assert.deepEqual(getPluginTargetState(plugin()), {
    label: "启用",
    description: "",
    pending: false,
  });
  assert.deepEqual(getPluginTargetState(plugin({
    desired_enabled: false,
    restart_required: true,
  })), {
    label: "停用",
    description: "等待停用",
    pending: true,
  });
  assert.deepEqual(getPluginTargetState(plugin({
    pending_action: "remove",
    desired_enabled: false,
    desired_version: null,
    restart_required: true,
  })), {
    label: "卸载",
    description: "配置与数据保留",
    pending: true,
  });
});

test("detects updates only from the installed plugin repository", () => {
  const installed = plugin({
    desired_version: "1.0.0",
    source: {
      ...plugin().source,
      url: "https://example.invalid/plugins.git",
      resolved_commit: "old-commit",
    },
  });
  const update = getPluginCatalogUpdate(installed, [{
    id: "demo-plugin",
    name: "Demo Plugin",
    version: "1.1.0",
    description: "",
    source_id: "official-demo",
    source_name: "Official",
    source: "https://example.invalid/plugins.git",
    source_kind: "official",
    ref: "main",
    resolved_commit: "new-commit",
    subdirectory: "plugins/demo-plugin",
  }]);

  assert.equal(update?.available, true);
  assert.equal(update?.entry.version, "1.1.0");
  assert.equal(getPluginCatalogUpdate(installed, []), null);
});


test("accepts the supported nested settings schema subset", () => {
  const result = parsePluginSettingsSchema({
    type: "object",
    title: "Demo",
    required: ["endpoint"],
    properties: {
      endpoint: { type: "string", title: "地址", format: "uri" },
      retries: { type: "integer", minimum: 0, maximum: 10, default: 3 },
      mode: { type: "string", enum: ["safe", "fast"] },
      enabled: { type: "boolean", default: true },
      tags: { type: "array", items: { type: "string" } },
      nested: {
        type: "object",
        properties: {
          note: { type: "string", format: "multiline" },
        },
      },
    },
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(Object.keys(result.schema.properties), [
    "endpoint",
    "retries",
    "mode",
    "enabled",
    "tags",
    "nested",
  ]);
});


test("fails closed for unsupported schema constructs and array item types", () => {
  const unsupported = [
    { type: "object", properties: {}, oneOf: [] },
    { type: "object", properties: {}, additionalProperties: false },
    {
      type: "object",
      properties: {
        values: { type: "array", items: { type: "number" } },
      },
    },
    {
      type: "object",
      properties: {
        mode: { type: "string", enum: ["safe", 1] },
      },
    },
  ];

  for (const schema of unsupported) {
    const result = parsePluginSettingsSchema(schema);
    assert.equal(result.ok, false);
    if (!result.ok) assert.match(result.error, /不支持|只支持|必须/);
  }
});


test("reads and immutably updates nested settings values", () => {
  const original = { api: { endpoint: "http://old", retries: 1 } };
  const updated = setSettingsValue(original, ["api", "endpoint"], "http://new");

  assert.equal(getSettingsValue(updated, ["api", "endpoint"]), "http://new");
  assert.equal(getSettingsValue(original, ["api", "endpoint"]), "http://old");
  assert.notEqual(updated, original);
  assert.notEqual(updated.api, original.api);
});


test("coerces supported field inputs and validates required and numeric constraints", () => {
  assert.equal(
    coerceSettingsFieldValue({ type: "integer" }, "4"),
    4,
  );
  assert.deepEqual(
    coerceSettingsFieldValue({ type: "array", items: { type: "string" } }, "alpha\nbeta\n\n"),
    ["alpha", "beta"],
  );

  const parsed = parsePluginSettingsSchema({
    type: "object",
    required: ["name"],
    properties: {
      name: { type: "string" },
      retries: { type: "integer", minimum: 0, maximum: 3 },
    },
  });
  assert.equal(parsed.ok, true);
  if (!parsed.ok) return;

  assert.deepEqual(validatePluginSettings(parsed.schema, { name: "", retries: 4 }), {
    name: "此项为必填项",
    retries: "不能大于 3",
  });
  assert.deepEqual(validatePluginSettings(parsed.schema, { name: "demo", retries: 2 }), {});
});
