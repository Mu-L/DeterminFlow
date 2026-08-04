export const CORE_TAB_IDS = [
  "chat",
  "dashboard",
  "graph",
  "roundtable",
  "orchestration",
  "workflow",
  "cron",
  "skills",
  "rules",
  "system-prompt",
  "settings",
  "extensions",
] as const;

export type CoreTabId = (typeof CORE_TAB_IDS)[number];

const CORE_TAB_ID_SET = new Set<string>(CORE_TAB_IDS);

export function isCoreTabId(value: string): value is CoreTabId {
  return CORE_TAB_ID_SET.has(value);
}
