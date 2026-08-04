export const SETTINGS_UPDATED_EVENT = "determinflow:settings-updated";
export const SYSTEM_PROMPT_TAB_CONFIG_KEY = "SHOW_SYSTEM_PROMPT_TAB";

export function readSystemPromptTabVisibility(
  config: Record<string, string | number | boolean>,
): boolean {
  const value = config[SYSTEM_PROMPT_TAB_CONFIG_KEY];
  return value === true || value === "true" || value === 1;
}

export function notifySettingsUpdated(): void {
  window.dispatchEvent(new Event(SETTINGS_UPDATED_EVENT));
}
