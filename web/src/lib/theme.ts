export const THEME_STORAGE_KEY = "determinflow-theme-v1";

export type Theme = "dark" | "light";

interface ThemeStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}

export function normalizeTheme(value: unknown): Theme {
  return value === "light" ? "light" : "dark";
}

function getBrowserStorage(): ThemeStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readStoredTheme(storage: ThemeStorage | null = getBrowserStorage()): Theme {
  if (!storage) return "dark";
  try {
    return normalizeTheme(storage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "dark";
  }
}

export function writeStoredTheme(
  theme: Theme,
  storage: ThemeStorage | null = getBrowserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Theme changes still apply for the current session when storage is unavailable.
  }
}

export function applyTheme(
  theme: Theme,
  root: HTMLElement | null = typeof document === "undefined" ? null : document.documentElement,
): void {
  if (!root) return;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
}

export function initializeTheme(): Theme {
  const theme = readStoredTheme();
  applyTheme(theme);
  return theme;
}

export function nextTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}
