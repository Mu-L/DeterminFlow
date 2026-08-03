import type {
  PluginCatalogEntry,
  PluginObjectSchema,
  PluginRecord,
  PluginSchemaParseResult,
  PluginSettings,
  PluginSettingsSchema,
  PluginSettingsSchemaNode,
  PluginStringArraySchema,
} from "./plugin-types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

export interface RuntimeStatusMeta {
  label: string;
  variant: BadgeVariant;
}

export interface PluginEnableAction {
  label: string;
  targetEnabled: boolean;
  kind: "enable" | "disable" | "undo";
  disabled: boolean;
  disabledReason?: string;
}

export interface PluginTargetState {
  label: string;
  description: string;
  pending: boolean;
}

export interface PluginCatalogUpdate {
  entry: PluginCatalogEntry;
  available: boolean;
}

export function getPluginCatalogUpdate(
  plugin: PluginRecord,
  catalog: PluginCatalogEntry[],
): PluginCatalogUpdate | null {
  const entry = catalog.find((candidate) => (
    candidate.id === plugin.id && candidate.source === plugin.source.url
  ));
  if (!entry) return null;
  const installedCommit = plugin.source.resolved_commit;
  const available = Boolean(
    installedCommit && entry.resolved_commit
      ? installedCommit !== entry.resolved_commit
      : plugin.desired_version
        && entry.version
        && plugin.desired_version !== entry.version,
  );
  return { entry, available };
}

const RUNTIME_STATUS_META: Record<string, RuntimeStatusMeta> = {
  disabled: { label: "未启用", variant: "outline" },
  discovered: { label: "已发现", variant: "secondary" },
  loaded: { label: "已加载", variant: "secondary" },
  starting: { label: "启动中", variant: "secondary" },
  running: { label: "运行中", variant: "default" },
  degraded: { label: "已降级", variant: "secondary" },
  blocked: { label: "已阻塞", variant: "destructive" },
  failed: { label: "失败", variant: "destructive" },
  pending: { label: "等待启动", variant: "outline" },
  stopped: { label: "已停止", variant: "outline" },
  exited: { label: "已退出", variant: "destructive" },
};

const BASE_KEYS = new Set(["type", "title", "description", "default"]);
const STRING_FORMATS = new Set(["password", "uri", "multiline"]);
const PLUGIN_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rejectUnknownKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  path: string,
): string | null {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  return unknown.length > 0 ? `${path} 包含不支持的字段: ${unknown.join(", ")}` : null;
}

function annotations(value: Record<string, unknown>): {
  title?: string;
  description?: string;
  default?: unknown;
} {
  return {
    ...(typeof value.title === "string" ? { title: value.title } : {}),
    ...(typeof value.description === "string" ? { description: value.description } : {}),
    ...("default" in value ? { default: value.default } : {}),
  };
}

function parseSchemaNode(
  value: unknown,
  path: string,
  root = false,
): { ok: true; schema: PluginSettingsSchemaNode } | { ok: false; error: string } {
  if (!isRecord(value) || typeof value.type !== "string") {
    return { ok: false, error: `${path} 必须是带 type 的对象` };
  }
  if (value.title !== undefined && typeof value.title !== "string") {
    return { ok: false, error: `${path}.title 必须是字符串` };
  }
  if (value.description !== undefined && typeof value.description !== "string") {
    return { ok: false, error: `${path}.description 必须是字符串` };
  }

  if (value.type === "object") {
    const allowed = new Set([
      ...BASE_KEYS,
      "properties",
      "required",
      ...(root ? ["$schema"] : []),
    ]);
    const unknownError = rejectUnknownKeys(value, allowed, path);
    if (unknownError) return { ok: false, error: unknownError };
    if (!isRecord(value.properties)) {
      return { ok: false, error: `${path}.properties 必须是对象` };
    }
    const required = value.required;
    if (
      required !== undefined
      && (
        !Array.isArray(required)
        || required.some((item) => typeof item !== "string" || !item)
      )
    ) {
      return { ok: false, error: `${path}.required 必须是非空字符串数组` };
    }

    const properties: Record<string, PluginSettingsSchemaNode> = {};
    for (const [key, child] of Object.entries(value.properties)) {
      if (!key) return { ok: false, error: `${path}.properties 不能包含空字段名` };
      const parsed = parseSchemaNode(child, `${path}.${key}`);
      if (!parsed.ok) return parsed;
      properties[key] = parsed.schema;
    }
    if (
      Array.isArray(required)
      && required.some((key) => !(key in properties))
    ) {
      return { ok: false, error: `${path}.required 必须引用已声明字段` };
    }

    return {
      ok: true,
      schema: {
        type: "object",
        properties,
        ...(Array.isArray(required) ? { required: [...required] } : {}),
        ...annotations(value),
      },
    };
  }

  if (value.type === "string") {
    const allowed = new Set([...BASE_KEYS, "enum", "format"]);
    const unknownError = rejectUnknownKeys(value, allowed, path);
    if (unknownError) return { ok: false, error: unknownError };
    if (
      value.enum !== undefined
      && (
        !Array.isArray(value.enum)
        || value.enum.length === 0
        || value.enum.some((item) => typeof item !== "string")
      )
    ) {
      return { ok: false, error: `${path}.enum 必须是非空字符串数组` };
    }
    if (
      value.format !== undefined
      && (typeof value.format !== "string" || !STRING_FORMATS.has(value.format))
    ) {
      return { ok: false, error: `${path}.format 不受支持` };
    }
    return {
      ok: true,
      schema: {
        type: "string",
        ...(Array.isArray(value.enum) ? { enum: [...value.enum] as string[] } : {}),
        ...(typeof value.format === "string"
          ? { format: value.format as "password" | "uri" | "multiline" }
          : {}),
        ...annotations(value),
      },
    };
  }

  if (value.type === "number" || value.type === "integer") {
    const allowed = new Set([...BASE_KEYS, "enum", "minimum", "maximum"]);
    const unknownError = rejectUnknownKeys(value, allowed, path);
    if (unknownError) return { ok: false, error: unknownError };
    if (
      value.enum !== undefined
      && (
        !Array.isArray(value.enum)
        || value.enum.length === 0
        || value.enum.some((item) => typeof item !== "number" || !Number.isFinite(item))
      )
    ) {
      return { ok: false, error: `${path}.enum 必须是非空数字数组` };
    }
    if (
      value.minimum !== undefined
      && (typeof value.minimum !== "number" || !Number.isFinite(value.minimum))
    ) {
      return { ok: false, error: `${path}.minimum 必须是数字` };
    }
    if (
      value.maximum !== undefined
      && (typeof value.maximum !== "number" || !Number.isFinite(value.maximum))
    ) {
      return { ok: false, error: `${path}.maximum 必须是数字` };
    }
    if (
      typeof value.minimum === "number"
      && typeof value.maximum === "number"
      && value.minimum > value.maximum
    ) {
      return { ok: false, error: `${path}.minimum 不能大于 maximum` };
    }
    return {
      ok: true,
      schema: {
        type: value.type,
        ...(Array.isArray(value.enum) ? { enum: [...value.enum] as number[] } : {}),
        ...(typeof value.minimum === "number" ? { minimum: value.minimum } : {}),
        ...(typeof value.maximum === "number" ? { maximum: value.maximum } : {}),
        ...annotations(value),
      },
    };
  }

  if (value.type === "boolean") {
    const unknownError = rejectUnknownKeys(value, BASE_KEYS, path);
    if (unknownError) return { ok: false, error: unknownError };
    return { ok: true, schema: { type: "boolean", ...annotations(value) } };
  }

  if (value.type === "array") {
    const allowed = new Set([...BASE_KEYS, "items"]);
    const unknownError = rejectUnknownKeys(value, allowed, path);
    if (unknownError) return { ok: false, error: unknownError };
    const parsedItems = parseSchemaNode(value.items, `${path}.items`);
    if (!parsedItems.ok) return parsedItems;
    if (parsedItems.schema.type !== "string") {
      return { ok: false, error: `${path} 只支持 array<string>` };
    }
    return {
      ok: true,
      schema: {
        type: "array",
        items: parsedItems.schema,
        ...annotations(value),
      },
    };
  }

  return { ok: false, error: `${path}.type "${value.type}" 不支持` };
}

function isMissing(value: unknown): boolean {
  return value === undefined || value === null || value === "";
}

function validateNode(
  schema: PluginSettingsSchemaNode,
  value: unknown,
  path: string[],
  errors: Record<string, string>,
): void {
  const key = path.join(".");
  if (value === undefined || value === null) return;

  if (schema.type === "object") {
    if (!isRecord(value)) {
      if (key) errors[key] = "必须是对象";
      return;
    }
    for (const [name, childSchema] of Object.entries(schema.properties)) {
      const childValue = value[name];
      const childKey = [...path, name].join(".");
      if (schema.required?.includes(name) && isMissing(childValue)) {
        errors[childKey] = "此项为必填项";
        continue;
      }
      validateNode(childSchema, childValue, [...path, name], errors);
    }
    return;
  }

  if (schema.type === "string") {
    if (typeof value !== "string") {
      errors[key] = "必须是字符串";
      return;
    }
    if (schema.enum && !schema.enum.includes(value)) {
      errors[key] = "值不在允许范围内";
      return;
    }
    if (schema.format === "uri" && value) {
      try {
        new URL(value);
      } catch {
        errors[key] = "必须是有效 URI";
      }
    }
    return;
  }

  if (schema.type === "number" || schema.type === "integer") {
    if (
      typeof value !== "number"
      || !Number.isFinite(value)
      || (schema.type === "integer" && !Number.isInteger(value))
    ) {
      errors[key] = schema.type === "integer" ? "必须是整数" : "必须是数字";
      return;
    }
    if (schema.enum && !schema.enum.includes(value)) {
      errors[key] = "值不在允许范围内";
    } else if (schema.minimum !== undefined && value < schema.minimum) {
      errors[key] = `不能小于 ${schema.minimum}`;
    } else if (schema.maximum !== undefined && value > schema.maximum) {
      errors[key] = `不能大于 ${schema.maximum}`;
    }
    return;
  }

  if (schema.type === "boolean") {
    if (typeof value !== "boolean") errors[key] = "必须是布尔值";
    return;
  }

  if (
    !Array.isArray(value)
    || value.some((item) => typeof item !== "string")
  ) {
    errors[key] = "必须是字符串数组";
  }
}

export function getRuntimeStatusMeta(status: string): RuntimeStatusMeta {
  if (Object.prototype.hasOwnProperty.call(RUNTIME_STATUS_META, status)) {
    return RUNTIME_STATUS_META[status];
  }
  return {
    label: status ? `未知（${status}）` : "未知",
    variant: "outline",
  };
}

export function getPluginEnableAction(plugin: PluginRecord): PluginEnableAction {
  if (plugin.pending_action === "remove") {
    return {
      label: "等待卸载",
      targetEnabled: false,
      kind: "disable",
      disabled: true,
      disabledReason: "插件已安排在重启后卸载",
    };
  }
  if (plugin.active_enabled !== plugin.desired_enabled) {
    return {
      label: plugin.desired_enabled ? "撤销启用" : "撤销停用",
      targetEnabled: plugin.active_enabled,
      kind: "undo",
      disabled: false,
    };
  }
  return {
    label: plugin.desired_enabled ? "停用" : "启用",
    targetEnabled: !plugin.desired_enabled,
    kind: plugin.desired_enabled ? "disable" : "enable",
    disabled: false,
  };
}

export function getPluginTargetState(plugin: PluginRecord): PluginTargetState {
  const pendingActionLabels: Record<string, string> = {
    install: "等待安装",
    update: "等待更新",
    rollback: "等待回退",
    remove: "等待卸载",
  };
  if (plugin.pending_action === "remove") {
    return { label: "卸载", description: "配置与数据保留", pending: true };
  }
  const enabledLabel = plugin.desired_enabled ? "启用" : "停用";
  if (!plugin.restart_required) {
    return { label: enabledLabel, description: "", pending: false };
  }
  const pendingLabel = plugin.pending_action
    ? pendingActionLabels[plugin.pending_action] || "等待变更"
    : plugin.active_enabled !== plugin.desired_enabled
      ? `等待${enabledLabel}`
      : "等待重启";
  return { label: enabledLabel, description: pendingLabel, pending: true };
}

export function isValidPluginId(pluginId: string): boolean {
  return PLUGIN_ID_PATTERN.test(pluginId);
}

export function parsePluginSettingsSchema(value: unknown): PluginSchemaParseResult {
  const parsed = parseSchemaNode(value, "settings_schema", true);
  if (!parsed.ok) return parsed;
  if (parsed.schema.type !== "object") {
    return { ok: false, error: "settings_schema 顶层必须是 object" };
  }
  return { ok: true, schema: parsed.schema };
}

export function getSettingsValue(settings: PluginSettings, path: string[]): unknown {
  let current: unknown = settings;
  for (const segment of path) {
    if (!isRecord(current)) return undefined;
    current = current[segment];
  }
  return current;
}

export function setSettingsValue(
  settings: PluginSettings,
  path: string[],
  value: unknown,
): PluginSettings {
  if (path.length === 0) return settings;
  const [head, ...tail] = path;
  const current = settings[head];
  return {
    ...settings,
    [head]: tail.length === 0
      ? value
      : setSettingsValue(isRecord(current) ? current : {}, tail, value),
  };
}

export function coerceSettingsFieldValue(
  schema: Exclude<PluginSettingsSchemaNode, PluginObjectSchema>,
  value: string | boolean,
): unknown {
  if (schema.type === "boolean") return Boolean(value);
  if (schema.type === "number" || schema.type === "integer") {
    if (value === "") return undefined;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (schema.type === "array") {
    return String(value)
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return String(value);
}

export function settingsFieldDisplayValue(
  schema: Exclude<PluginSettingsSchemaNode, PluginObjectSchema>,
  value: unknown,
): string | boolean {
  if (schema.type === "boolean") return typeof value === "boolean" ? value : false;
  if (schema.type === "array") {
    return Array.isArray(value) ? value.filter((item) => typeof item === "string").join("\n") : "";
  }
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

export function validatePluginSettings(
  schema: PluginSettingsSchema,
  settings: PluginSettings,
): Record<string, string> {
  const errors: Record<string, string> = {};
  validateNode(schema, settings, [], errors);
  return errors;
}

export function schemaHasConfigurableFields(schema: PluginSettingsSchema): boolean {
  return Object.keys(schema.properties).length > 0;
}

export function isStringArraySchema(
  schema: PluginSettingsSchemaNode,
): schema is PluginStringArraySchema {
  return schema.type === "array";
}
