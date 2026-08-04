export type PluginTrust = "official" | "third_party";

export type PluginRuntimeStatus =
  | "disabled"
  | "discovered"
  | "loaded"
  | "starting"
  | "running"
  | "degraded"
  | "blocked"
  | "failed"
  | string;

export interface PluginSource {
  url: string;
  ref: string;
  subdirectory: string;
  trust: PluginTrust;
  resolved_commit: string;
  content_sha256: string;
}

export interface PluginProcess {
  owner?: string;
  process_id?: string;
  id?: string;
  name?: string;
  status?: string;
  error?: string;
  pid?: number | null;
  returncode?: number | null;
  was_killed?: boolean;
}

interface PluginSchemaAnnotations {
  title?: string;
  description?: string;
  default?: unknown;
}

export interface PluginObjectSchema extends PluginSchemaAnnotations {
  type: "object";
  properties: Record<string, PluginSettingsSchemaNode>;
  required?: string[];
}

export interface PluginStringSchema extends PluginSchemaAnnotations {
  type: "string";
  enum?: string[];
  format?: "password" | "uri" | "multiline";
}

export interface PluginNumberSchema extends PluginSchemaAnnotations {
  type: "number" | "integer";
  enum?: number[];
  minimum?: number;
  maximum?: number;
}

export interface PluginBooleanSchema extends PluginSchemaAnnotations {
  type: "boolean";
}

export interface PluginStringArraySchema extends PluginSchemaAnnotations {
  type: "array";
  items: PluginStringSchema;
}

export type PluginSettingsSchemaNode =
  | PluginObjectSchema
  | PluginStringSchema
  | PluginNumberSchema
  | PluginBooleanSchema
  | PluginStringArraySchema;

export type PluginSettingsSchema = PluginObjectSchema;
export type PluginSettings = Record<string, unknown>;

export interface PluginRecord {
  id: string;
  name: string;
  description: string;
  resource_prefix: string;
  runtime_status: PluginRuntimeStatus;
  error: string;
  active_enabled: boolean;
  desired_enabled: boolean;
  active_version: string | null;
  desired_version: string | null;
  restart_required: boolean;
  pending_action: string | null;
  dependencies: string[];
  capabilities: string[];
  source: PluginSource;
  settings_schema: unknown | null;
  settings: PluginSettings;
  config_present: boolean;
  page_url: string | null;
  processes: PluginProcess[];
}

export interface PluginListResponse {
  plugins: PluginRecord[];
  restart_required: boolean;
  package_management_read_only: boolean;
}

export interface PluginCatalogEntry {
  id: string;
  name: string;
  version: string;
  description: string;
  source_id: string;
  source_name: string;
  source: string;
  source_kind: "official" | "custom";
  ref: string;
  resolved_commit: string;
  subdirectory: string;
}

export interface PluginRepositorySource {
  id: string;
  name: string;
  url: string;
  ref: string;
  kind: "official" | "custom";
  builtin: boolean;
  mirrors: string[];
}

export interface PluginCatalogSource extends PluginRepositorySource {
  resolved_commit: string;
  plugin_count: number;
  error: string;
  selected_url: string;
}

export interface PluginCatalogResponse {
  sources: PluginCatalogSource[];
  plugins: PluginCatalogEntry[];
  package_management_read_only?: boolean;
}

export interface PluginMutationResponse {
  plugin?: PluginRecord;
  restart_required: boolean;
  message?: string;
}

export interface PluginSourceListResponse {
  sources: PluginRepositorySource[];
  package_management_read_only: boolean;
}

export interface PluginSourceRequest {
  name: string;
  url: string;
  ref: string;
}

export interface PluginSourceMutationResponse {
  source: PluginRepositorySource;
  catalog?: PluginCatalogResponse;
}

export interface InstallPluginRequest {
  plugin_id: string;
  source: string;
  ref?: string;
  subdirectory?: string;
  resource_prefix?: string;
  acknowledge_risk: boolean;
}

export type PluginSchemaParseResult =
  | { ok: true; schema: PluginSettingsSchema }
  | { ok: false; error: string };
