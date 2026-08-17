import type { ExtensionStatus } from "./types";

export type HeaderStatusTone = "normal" | "attention" | "critical" | "stale";

export interface HeaderStatusMetric {
  label: string;
  value: string;
}

export interface HeaderStatusAction {
  id: string;
  label: string;
  kind: "manage" | "link" | "page" | "request";
  href?: string;
  endpoint?: string;
  method?: "POST" | "DELETE";
}

export type ExtensionAnnouncementLevel = "info" | "maintenance" | "warning";

export interface ExtensionAnnouncement {
  id: string;
  title: string;
  body: string;
  level: ExtensionAnnouncementLevel;
  published_at: string;
  expires_at?: string;
}

export interface HeaderStatusPayload {
  visible: true;
  label: string;
  value: string;
  title: string;
  summary: string;
  summary_href?: string;
  tone: HeaderStatusTone;
  metrics: HeaderStatusMetric[];
  metadata: HeaderStatusMetric[];
  actions: HeaderStatusAction[];
  announcements: ExtensionAnnouncement[];
  refresh_after_ms?: number;
  updated_at: string;
}

const ANNOUNCEMENT_LEVELS = new Set<ExtensionAnnouncementLevel>([
  "info",
  "maintenance",
  "warning",
]);

function parseAnnouncements(value: unknown): ExtensionAnnouncement[] {
  if (!Array.isArray(value)) return [];
  const result: ExtensionAnnouncement[] = [];
  const ids = new Set<string>();
  for (const item of value.slice(0, 10)) {
    if (!isObject(item)) continue;
    const id = readText(item.id, 64);
    const title = readText(item.title, 255);
    const body = readText(item.body, 4000);
    const publishedAt = readText(item.published_at, 80);
    const expiresAt = item.expires_at == null
      ? undefined
      : readText(item.expires_at, 80) ?? undefined;
    if (
      !id
      || ids.has(id)
      || !title
      || !body
      || !publishedAt
      || Number.isNaN(Date.parse(publishedAt))
      || (item.expires_at != null && (!expiresAt || Number.isNaN(Date.parse(expiresAt))))
      || typeof item.level !== "string"
      || !ANNOUNCEMENT_LEVELS.has(item.level as ExtensionAnnouncementLevel)
    ) {
      continue;
    }
    ids.add(id);
    result.push({
      id,
      title,
      body,
      level: item.level as ExtensionAnnouncementLevel,
      published_at: publishedAt,
      expires_at: expiresAt,
    });
  }
  return result;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized && normalized.length <= maxLength ? normalized : null;
}

function parseMetrics(value: unknown): HeaderStatusMetric[] | null {
  if (!Array.isArray(value) || value.length > 6) return null;
  const result: HeaderStatusMetric[] = [];
  for (const item of value) {
    if (!isObject(item)) return null;
    const label = readText(item.label, 40);
    const metricValue = readText(item.value, 80);
    if (!label || !metricValue) return null;
    result.push({ label, value: metricValue });
  }
  return result;
}

function safeLink(value: unknown): string | null {
  const href = readText(value, 2048);
  if (!href) return null;
  try {
    const parsed = new URL(href);
    const isLoopbackHttp = parsed.protocol === "http:"
      && ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
    return parsed.protocol === "https:" || isLoopbackHttp ? href : null;
  } catch {
    return null;
  }
}

function safeApiEndpoint(value: unknown): string | null {
  const endpoint = readText(value, 512);
  if (
    !endpoint
    || !endpoint.startsWith("/api/")
    || endpoint.startsWith("//")
    || endpoint.includes("?")
    || endpoint.includes("#")
    || endpoint.includes("\\")
    || endpoint.split("/").includes("..")
  ) {
    return null;
  }
  return endpoint;
}

function parseActions(value: unknown): HeaderStatusAction[] | null {
  if (!Array.isArray(value) || value.length > 3) return null;
  const result: HeaderStatusAction[] = [];
  const ids = new Set<string>();
  for (const item of value) {
    if (!isObject(item)) return null;
    const id = readText(item.id, 64);
    const label = readText(item.label, 40);
    if (!id || !label || ids.has(id)) return null;
    ids.add(id);
    if (item.kind === "manage") {
      result.push({ id, label, kind: "manage" });
    } else if (item.kind === "page") {
      result.push({ id, label, kind: "page" });
    } else if (item.kind === "request") {
      const endpoint = safeApiEndpoint(item.endpoint);
      const method = item.method === "POST" || item.method === "DELETE"
        ? item.method
        : null;
      if (!endpoint || !method) return null;
      result.push({ id, label, kind: "request", endpoint, method });
    } else if (item.kind === "link") {
      const href = safeLink(item.href);
      if (!href) return null;
      result.push({ id, label, kind: "link", href });
    } else {
      return null;
    }
  }
  return result;
}

export function isActiveHeaderStatusSource(status: ExtensionStatus): boolean {
  return Boolean(
    status.enabled
      && status.status === "running"
      && status.header_status?.endpoint,
  );
}

export function parseHeaderStatusResponse(value: unknown): HeaderStatusPayload | null {
  if (!isObject(value) || !isObject(value.header_status)) return null;
  const raw = value.header_status;
  if (raw.visible !== true) return null;
  const label = readText(raw.label, 20);
  const displayValue = readText(raw.value, 40);
  const title = readText(raw.title, 80);
  const summary = readText(raw.summary, 180);
  const summaryHref = raw.summary_href === undefined
    ? undefined
    : safeLink(raw.summary_href);
  const metrics = parseMetrics(raw.metrics);
  const metadata = parseMetrics(raw.metadata);
  const actions = parseActions(raw.actions);
  const announcements = parseAnnouncements(value.announcements);
  const refreshAfterMs = raw.refresh_after_ms == null
    ? undefined
    : raw.refresh_after_ms;
  const updatedAt = readText(raw.updated_at, 80);
  const tones = new Set<HeaderStatusTone>(["normal", "attention", "critical", "stale"]);
  if (
    !label
    || !displayValue
    || !title
    || !summary
    || (raw.summary_href !== undefined && !summaryHref)
    || !metrics
    || !metadata
    || !actions
    || (
      refreshAfterMs !== undefined
      && (
        typeof refreshAfterMs !== "number"
        || !Number.isInteger(refreshAfterMs)
        || refreshAfterMs < 500
        || refreshAfterMs > 60_000
      )
    )
    || !updatedAt
    || Number.isNaN(Date.parse(updatedAt))
    || typeof raw.tone !== "string"
    || !tones.has(raw.tone as HeaderStatusTone)
  ) {
    return null;
  }
  return {
    visible: true,
    label,
    value: displayValue,
    title,
    summary,
    summary_href: summaryHref ?? undefined,
    tone: raw.tone as HeaderStatusTone,
    metrics,
    metadata,
    actions,
    announcements,
    refresh_after_ms: refreshAfterMs,
    updated_at: updatedAt,
  };
}
