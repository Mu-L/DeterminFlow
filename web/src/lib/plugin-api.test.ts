import assert from "node:assert/strict";
import test from "node:test";

import {
  createPluginSource,
  deletePluginSource,
  fetchPluginCatalog,
  fetchPluginSources,
  fetchPlugins,
  installPlugin,
  resetPluginConfig,
  rollbackPlugin,
  savePluginConfig,
  setPluginEnabled,
  uninstallPlugin,
  updatePlugin,
  updatePluginSource,
} from "./plugin-api.ts";


test("uses the plugin management endpoint contract", async (context) => {
  const requests: Array<{
    url: string;
    method: string;
    body: unknown;
    authorization: string | null;
    contentType: string | null;
  }> = [];
  context.mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    requests.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      authorization: headers.get("Authorization"),
      contentType: headers.get("Content-Type"),
    });
    return new Response(JSON.stringify({ plugins: [], restart_required: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });

  await fetchPlugins();
  await fetchPluginCatalog();
  await fetchPluginCatalog(true);
  await fetchPluginSources();
  const adminToken = "admin-secret";
  await createPluginSource({
    name: "Team Plugins",
    url: "ssh://git@example.invalid/team/plugins.git",
    ref: "main",
  }, adminToken);
  await updatePluginSource("team/plugins", {
    name: "Team Stable",
    url: "ssh://git@example.invalid/team/plugins.git",
    ref: "stable",
  }, adminToken);
  await deletePluginSource("team/plugins", adminToken);
  await installPlugin({
    plugin_id: "demo-plugin",
    source: "/tmp/demo",
    ref: "main",
    subdirectory: "plugin",
    resource_prefix: "demo",
    acknowledge_risk: true,
  }, adminToken);
  await setPluginEnabled("demo/plugin", true, adminToken);
  await updatePlugin("demo/plugin", "release-v2", adminToken);
  await rollbackPlugin("demo/plugin", adminToken);
  await uninstallPlugin("demo/plugin", adminToken);
  await savePluginConfig(
    "demo/plugin",
    { endpoint: "http://localhost" },
    adminToken,
  );
  await resetPluginConfig("demo/plugin", adminToken);

  assert.deepEqual(requests, [
    {
      url: "/api/plugins",
      method: "GET",
      body: undefined,
      authorization: null,
      contentType: null,
    },
    {
      url: "/api/plugins/catalog",
      method: "GET",
      body: undefined,
      authorization: null,
      contentType: null,
    },
    {
      url: "/api/plugins/catalog?refresh=true",
      method: "GET",
      body: undefined,
      authorization: null,
      contentType: null,
    },
    {
      url: "/api/plugins/sources",
      method: "GET",
      body: undefined,
      authorization: null,
      contentType: null,
    },
    {
      url: "/api/plugins/sources",
      method: "POST",
      body: {
        name: "Team Plugins",
        url: "ssh://git@example.invalid/team/plugins.git",
        ref: "main",
      },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/sources/team%2Fplugins",
      method: "PUT",
      body: {
        name: "Team Stable",
        url: "ssh://git@example.invalid/team/plugins.git",
        ref: "stable",
      },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/sources/team%2Fplugins",
      method: "DELETE",
      body: undefined,
      authorization: "Bearer admin-secret",
      contentType: null,
    },
    {
      url: "/api/plugins/install",
      method: "POST",
      body: {
        plugin_id: "demo-plugin",
        source: "/tmp/demo",
        ref: "main",
        subdirectory: "plugin",
        resource_prefix: "demo",
        acknowledge_risk: true,
      },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/demo%2Fplugin/enabled",
      method: "PUT",
      body: { enabled: true },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/demo%2Fplugin/update",
      method: "POST",
      body: { ref: "release-v2" },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/demo%2Fplugin/rollback",
      method: "POST",
      body: undefined,
      authorization: "Bearer admin-secret",
      contentType: null,
    },
    {
      url: "/api/plugins/demo%2Fplugin",
      method: "DELETE",
      body: undefined,
      authorization: "Bearer admin-secret",
      contentType: null,
    },
    {
      url: "/api/plugins/demo%2Fplugin/config",
      method: "PUT",
      body: { settings: { endpoint: "http://localhost" } },
      authorization: "Bearer admin-secret",
      contentType: "application/json",
    },
    {
      url: "/api/plugins/demo%2Fplugin/config",
      method: "DELETE",
      body: undefined,
      authorization: "Bearer admin-secret",
      contentType: null,
    },
  ]);
});
