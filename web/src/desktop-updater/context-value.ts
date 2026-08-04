import { createContext, useContext } from "react";

export type DesktopUpdatePhase =
  | "disabled"
  | "idle"
  | "checking"
  | "up-to-date"
  | "available"
  | "downloading"
  | "installing"
  | "error";

export interface DesktopUpdateInfo {
  version: string;
  date?: string;
  body?: string;
}

export interface DesktopUpdateContextValue {
  enabled: boolean;
  phase: DesktopUpdatePhase;
  currentVersion: string | null;
  update: DesktopUpdateInfo | null;
  progress: number | null;
  error: string | null;
  noticeDismissed: boolean;
  checkForUpdates: () => Promise<void>;
  installUpdate: () => Promise<void>;
  dismissNotice: () => void;
}

const disabledContext: DesktopUpdateContextValue = {
  enabled: false,
  phase: "disabled",
  currentVersion: null,
  update: null,
  progress: null,
  error: null,
  noticeDismissed: false,
  checkForUpdates: async () => undefined,
  installUpdate: async () => undefined,
  dismissNotice: () => undefined,
};

export const DesktopUpdateContext = createContext<DesktopUpdateContextValue>(disabledContext);

export function useDesktopUpdate(): DesktopUpdateContextValue {
  return useContext(DesktopUpdateContext);
}
