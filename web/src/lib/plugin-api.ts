import type {
  InstallPluginRequest,
  PluginCatalogResponse,
  PluginListResponse,
  PluginMutationResponse,
  PluginSettings,
  PluginSourceListResponse,
  PluginSourceMutationResponse,
  PluginSourceRequest,
} from "@/extensions/plugin-types";

import { request } from "./http-client";

function pluginPath(pluginId: string): string {
  return `/plugins/${encodeURIComponent(pluginId)}`;
}

function pluginWriteHeaders(adminToken: string): HeadersInit | undefined {
  const token = adminToken.trim();
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

export function fetchPlugins(): Promise<PluginListResponse> {
  return request<PluginListResponse>("/plugins");
}

export function fetchPluginCatalog(refresh = false): Promise<PluginCatalogResponse> {
  return request<PluginCatalogResponse>(`/plugins/catalog${refresh ? "?refresh=true" : ""}`);
}

export function fetchPluginSources(): Promise<PluginSourceListResponse> {
  return request<PluginSourceListResponse>("/plugins/sources");
}

export function createPluginSource(
  payload: PluginSourceRequest,
  adminToken = "",
): Promise<PluginSourceMutationResponse> {
  return request<PluginSourceMutationResponse>("/plugins/sources", {
    method: "POST",
    headers: pluginWriteHeaders(adminToken),
    body: JSON.stringify(payload),
  });
}

export function updatePluginSource(
  sourceId: string,
  payload: PluginSourceRequest,
  adminToken = "",
): Promise<PluginSourceMutationResponse> {
  return request<PluginSourceMutationResponse>(
    `/plugins/sources/${encodeURIComponent(sourceId)}`,
    {
      method: "PUT",
      headers: pluginWriteHeaders(adminToken),
      body: JSON.stringify(payload),
    },
  );
}

export function deletePluginSource(
  sourceId: string,
  adminToken = "",
): Promise<PluginSourceMutationResponse> {
  return request<PluginSourceMutationResponse>(
    `/plugins/sources/${encodeURIComponent(sourceId)}`,
    {
      method: "DELETE",
      headers: pluginWriteHeaders(adminToken),
    },
  );
}

export function installPlugin(
  payload: InstallPluginRequest,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>("/plugins/install", {
    method: "POST",
    headers: pluginWriteHeaders(adminToken),
    body: JSON.stringify(payload),
  });
}

export function setPluginEnabled(
  pluginId: string,
  enabled: boolean,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>(`${pluginPath(pluginId)}/enabled`, {
    method: "PUT",
    headers: pluginWriteHeaders(adminToken),
    body: JSON.stringify({ enabled }),
  });
}

export function updatePlugin(
  pluginId: string,
  ref = "",
  adminToken = "",
): Promise<PluginMutationResponse> {
  const normalizedRef = ref.trim();
  return request<PluginMutationResponse>(`${pluginPath(pluginId)}/update`, {
    method: "POST",
    headers: pluginWriteHeaders(adminToken),
    body: normalizedRef ? JSON.stringify({ ref: normalizedRef }) : undefined,
  });
}

export function rollbackPlugin(
  pluginId: string,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>(`${pluginPath(pluginId)}/rollback`, {
    method: "POST",
    headers: pluginWriteHeaders(adminToken),
  });
}

export function uninstallPlugin(
  pluginId: string,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>(pluginPath(pluginId), {
    method: "DELETE",
    headers: pluginWriteHeaders(adminToken),
  });
}

export function savePluginConfig(
  pluginId: string,
  settings: PluginSettings,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>(`${pluginPath(pluginId)}/config`, {
    method: "PUT",
    headers: pluginWriteHeaders(adminToken),
    body: JSON.stringify({ settings }),
  });
}

export function resetPluginConfig(
  pluginId: string,
  adminToken = "",
): Promise<PluginMutationResponse> {
  return request<PluginMutationResponse>(`${pluginPath(pluginId)}/config`, {
    method: "DELETE",
    headers: pluginWriteHeaders(adminToken),
  });
}
