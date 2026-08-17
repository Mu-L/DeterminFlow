import assert from "node:assert/strict";
import test from "node:test";

import {
  extensionAnnouncementKey,
  readSeenAnnouncementKeys,
  rememberAnnouncementKey,
} from "./extension-announcement-state";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("stores only stable extension and announcement identities", () => {
  const storage = new MemoryStorage();
  const first = extensionAnnouncementKey("public-api", "notice-1");
  const second = extensionAnnouncementKey("public-api", "notice-2");

  rememberAnnouncementKey(storage, first);
  rememberAnnouncementKey(storage, second);
  rememberAnnouncementKey(storage, first);

  assert.deepEqual([...readSeenAnnouncementKeys(storage)], [second, first]);
});

test("fails open when persisted state is unavailable", () => {
  const blockedStorage = {
    getItem: () => { throw new Error("blocked"); },
    setItem: () => { throw new Error("blocked"); },
  };

  assert.deepEqual([...readSeenAnnouncementKeys(blockedStorage)], []);
  assert.doesNotThrow(() => rememberAnnouncementKey(blockedStorage, "public-api:notice-1"));
});
