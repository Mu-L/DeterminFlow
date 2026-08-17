import type { ExtensionAnnouncement } from "./header-status-model";

const STORAGE_KEY = "determinflow:extension-announcements:v1";
const MAX_SEEN_ANNOUNCEMENTS = 200;

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface PendingExtensionAnnouncement extends ExtensionAnnouncement {
  extensionId: string;
  extensionName: string;
}

export function extensionAnnouncementKey(extensionId: string, announcementId: string): string {
  return `${extensionId}:${announcementId}`;
}

export function readSeenAnnouncementKeys(storage: StorageLike | null): Set<string> {
  if (!storage) return new Set();
  try {
    const value: unknown = JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(value)) return new Set();
    return new Set(
      value.filter((item): item is string => typeof item === "string").slice(-MAX_SEEN_ANNOUNCEMENTS),
    );
  } catch {
    return new Set();
  }
}

export function rememberAnnouncementKey(storage: StorageLike | null, key: string): void {
  if (!storage) return;
  try {
    const seen = [...readSeenAnnouncementKeys(storage)];
    const next = [...seen.filter((item) => item !== key), key].slice(-MAX_SEEN_ANNOUNCEMENTS);
    storage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A blocked localStorage must not prevent announcements from being displayed.
  }
}

export function browserStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
