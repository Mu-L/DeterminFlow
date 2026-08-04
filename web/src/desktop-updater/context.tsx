import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { DownloadEvent, Update } from "@tauri-apps/plugin-updater";

import {
  DESKTOP_UPDATE_LAST_CHECK_KEY,
  calculateDownloadProgress,
  describeUpdateError,
  isDesktopRuntime,
  shouldAutoCheckForUpdate,
} from "../lib/desktop-update";
import {
  DesktopUpdateContext,
  type DesktopUpdateContextValue,
  type DesktopUpdateInfo,
  type DesktopUpdatePhase,
} from "./context-value";

interface UpdateMetadata {
  rid: number;
  currentVersion: string;
  version: string;
  date?: string;
  body?: string;
  rawJson: Record<string, unknown>;
}

function readLastCheck(): string | null {
  try {
    return window.localStorage.getItem(DESKTOP_UPDATE_LAST_CHECK_KEY);
  } catch {
    return null;
  }
}

function rememberSuccessfulCheck(): void {
  try {
    window.localStorage.setItem(DESKTOP_UPDATE_LAST_CHECK_KEY, String(Date.now()));
  } catch {
    // A blocked localStorage must not disable updates for the desktop app.
  }
}

export function DesktopUpdateProvider({ children }: { children: ReactNode }) {
  const enabled = isDesktopRuntime();
  const [phase, setPhase] = useState<DesktopUpdatePhase>(enabled ? "idle" : "disabled");
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [update, setUpdate] = useState<DesktopUpdateInfo | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const updateResource = useRef<Update | null>(null);
  const checkInFlight = useRef(false);

  const releaseUpdateResource = useCallback(async () => {
    const resource = updateResource.current;
    updateResource.current = null;
    if (resource) await resource.close().catch(() => undefined);
  }, []);

  const performCheck = useCallback(async (silent: boolean) => {
    if (!enabled || checkInFlight.current) return;
    checkInFlight.current = true;
    setPhase("checking");
    setError(null);
    setProgress(null);

    try {
      const [{ getVersion }, { Update, check }, { invoke }] = await Promise.all([
        import("@tauri-apps/api/app"),
        import("@tauri-apps/plugin-updater"),
        import("@tauri-apps/api/core"),
      ]);
      const installedVersion = await getVersion();
      setCurrentVersion(installedVersion);
      let availableUpdate: Update | null;
      try {
        const metadata = await invoke<UpdateMetadata | null>("check_update_sources");
        availableUpdate = metadata ? new Update(metadata) : null;
      } catch {
        availableUpdate = await check({ timeout: 15_000 });
      }
      rememberSuccessfulCheck();
      await releaseUpdateResource();

      if (!availableUpdate) {
        setUpdate(null);
        setPhase("up-to-date");
        return;
      }

      updateResource.current = availableUpdate;
      setUpdate({
        version: availableUpdate.version,
        date: availableUpdate.date,
        body: availableUpdate.body,
      });
      setNoticeDismissed(false);
      setPhase("available");
    } catch (caught) {
      if (silent) {
        setPhase("idle");
        setError(null);
      } else {
        setPhase("error");
        setError(describeUpdateError(caught));
      }
    } finally {
      checkInFlight.current = false;
    }
  }, [enabled, releaseUpdateResource]);

  const checkForUpdates = useCallback(
    () => performCheck(false),
    [performCheck],
  );

  const installUpdate = useCallback(async () => {
    const resource = updateResource.current;
    if (!enabled || !resource) return;

    setPhase("downloading");
    setProgress(0);
    setError(null);
    let downloadedBytes = 0;
    let totalBytes: number | undefined;

    const onDownloadEvent = (event: DownloadEvent) => {
      if (event.event === "Started") {
        totalBytes = event.data.contentLength;
        setProgress(totalBytes ? 0 : null);
      } else if (event.event === "Progress") {
        downloadedBytes += event.data.chunkLength;
        setProgress(calculateDownloadProgress(downloadedBytes, totalBytes));
      } else {
        setProgress(100);
        setPhase("installing");
      }
    };

    try {
      await resource.download(onDownloadEvent, { timeout: 10 * 60 * 1000 });
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("prepare_for_update");
      await resource.install();
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (caught) {
      setPhase("available");
      setProgress(null);
      setError(describeUpdateError(caught));
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    if (shouldAutoCheckForUpdate(readLastCheck())) {
      void performCheck(true);
      return;
    }

    void import("@tauri-apps/api/app")
      .then(({ getVersion }) => getVersion())
      .then(setCurrentVersion)
      .catch(() => undefined);
  }, [enabled, performCheck]);

  useEffect(() => () => {
    void releaseUpdateResource();
  }, [releaseUpdateResource]);

  const value = useMemo<DesktopUpdateContextValue>(() => ({
    enabled,
    phase,
    currentVersion,
    update,
    progress,
    error,
    noticeDismissed,
    checkForUpdates,
    installUpdate,
    dismissNotice: () => setNoticeDismissed(true),
  }), [
    checkForUpdates,
    currentVersion,
    enabled,
    error,
    installUpdate,
    noticeDismissed,
    phase,
    progress,
    update,
  ]);

  return (
    <DesktopUpdateContext.Provider value={value}>
      {children}
    </DesktopUpdateContext.Provider>
  );
}
