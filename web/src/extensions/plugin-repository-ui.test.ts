import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  buildCatalogInstallRequest,
  PluginInstallForm,
} from "../components/extensions/PluginInstallForm.tsx";
import { PluginLifecycleList } from "../components/extensions/PluginLifecycleList.tsx";
import { PluginRepositoryDialog } from "../components/extensions/PluginRepositoryDialog.tsx";
import { EXTENSION_ANNOUNCEMENT_DIALOG_CLASS_NAME } from "./ExtensionAnnouncementDialog.tsx";
import type {
  PluginCatalogEntry,
  PluginCatalogSource,
  PluginRecord,
  PluginSourceRequest,
} from "./plugin-types.ts";

const sources: PluginCatalogSource[] = [
  {
    id: "official",
    name: "DeterminFlow 官方插件",
    url: "ssh://git@example.com/official.git",
    selected_url: "ssh://git@example.com/official.git",
    mirrors: [],
    ref: "main",
    kind: "official",
    builtin: true,
    resolved_commit: "1234567890abcdef",
    plugin_count: 1,
    error: "",
  },
  {
    id: "team",
    name: "团队工具仓库",
    url: "ssh://git@example.com/team.git",
    selected_url: "ssh://git@example.com/team.git",
    mirrors: [],
    ref: "stable",
    kind: "custom",
    builtin: false,
    resolved_commit: "abcdef1234567890",
    plugin_count: 2,
    error: "",
  },
];

const catalog: PluginCatalogEntry[] = [{
  id: "demo-plugin",
  name: "示例插件",
  version: "1.0.0",
  description: "用于验证安装侧栏。",
  source_id: "official",
  source_name: "DeterminFlow 官方插件",
  source: sources[0].url,
  source_kind: "official",
  ref: "main",
  resolved_commit: "1234567890abcdef",
  subdirectory: "plugins/demo-plugin",
}];

const noop = () => undefined;
const succeed = async () => true;
const save: (
  source: PluginCatalogSource | null,
  request: PluginSourceRequest,
) => Promise<boolean> = async () => true;

const installedPlugin: PluginRecord = {
  id: "public-api",
  name: "笔枢公益模型",
  description: "由笔枢写作免费提供的模型体验服务。",
  resource_prefix: "public-api",
  runtime_status: "running",
  error: "",
  active_enabled: true,
  desired_enabled: true,
  active_version: "0.1.19",
  desired_version: "0.1.19",
  restart_required: false,
  pending_action: null,
  dependencies: [],
  capabilities: ["api.routes", "model.providers"],
  source: {
    url: "ssh://git@example.com/official.git",
    ref: "main",
    subdirectory: "plugins/public-api",
    trust: "official",
    resolved_commit: "1234567890abcdef",
    content_sha256: "abcdef1234567890",
  },
  settings_schema: null,
  settings: {},
  config_present: true,
  page_url: null,
  processes: [],
};

test("install drawer exposes repository controls without a search field", () => {
  const markup = renderToStaticMarkup(createElement(PluginInstallForm, {
    busy: false,
    readOnly: false,
    catalog,
    sources,
    catalogError: "",
    installedIds: new Set<string>(),
    onAddSource: noop,
    onManageSource: noop,
    onDeleteSource: noop,
    onInstall: succeed,
  }));

  assert.doesNotMatch(markup, /搜索名称或插件 ID/);
  assert.match(markup, /添加仓库/);
  assert.match(markup, /管理/);
  assert.match(markup, /删除/);
  assert.match(markup, /官方/);
  assert.match(markup, /第三方/);
});

test("repository dialog keeps add, manage, and delete as distinct actions", () => {
  const common = {
    busyAction: "",
    onClose: noop,
    onSave: save,
    onDelete: succeed,
    onRefresh: succeed,
  };
  const addMarkup = renderToStaticMarkup(createElement(PluginRepositoryDialog, {
    ...common,
    source: null,
  }));
  assert.match(addMarkup, /添加插件仓库/);
  assert.match(addMarkup, /保存并拉取/);
  assert.match(addMarkup, /不在这里保存访问令牌/);

  const manageMarkup = renderToStaticMarkup(createElement(PluginRepositoryDialog, {
    ...common,
    source: sources[1],
  }));
  assert.match(manageMarkup, /管理插件仓库/);
  assert.match(manageMarkup, /重新拉取/);
  assert.match(manageMarkup, /保存修改/);

  const deleteMarkup = renderToStaticMarkup(createElement(PluginRepositoryDialog, {
    ...common,
    source: sources[1],
    initialView: "delete" as const,
  }));
  assert.match(deleteMarkup, /仅删除仓库记录，不卸载已经安装的插件/);
  assert.match(deleteMarkup, /确认删除仓库/);
});

test("catalog installs pin the commit shown to the user", () => {
  assert.deepEqual(buildCatalogInstallRequest(catalog[0], " demo ", false), {
    plugin_id: "demo-plugin",
    source: sources[0].url,
    ref: "1234567890abcdef",
    subdirectory: "plugins/demo-plugin",
    resource_prefix: "demo",
    acknowledge_risk: false,
  });
});

test("installed plugins expose one dedicated description column", () => {
  const markup = renderToStaticMarkup(createElement(PluginLifecycleList, {
    plugins: [installedPlugin],
    catalog: [],
    busyAction: "",
    onDetails: noop,
    onSetEnabled: succeed,
    onUpdate: succeed,
  }));

  assert.match(markup, />说明</);
  assert.match(markup, /由笔枢写作免费提供的模型体验服务/);
});

test("announcement dialog is wide on desktop and bounded on small screens", () => {
  const classes = new Set(EXTENSION_ANNOUNCEMENT_DIALOG_CLASS_NAME.split(/\s+/));

  assert.equal(classes.has("w-full"), true);
  assert.equal(classes.has("max-w-2xl"), true);
  assert.equal(classes.has("max-w-lg"), false);
  assert.equal(classes.has("max-h-[calc(100dvh-2rem)]"), true);
  assert.equal(classes.has("overflow-hidden"), true);
});
