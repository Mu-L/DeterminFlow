import { useCallback, useEffect, useState } from "react";

import { fetchConfig } from "../lib/api";
import {
  SETTINGS_UPDATED_EVENT,
  readSystemPromptTabVisibility,
} from "../lib/navigation-settings";

export function useNavigationSettings(): boolean {
  const [showSystemPromptTab, setShowSystemPromptTab] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const result = await fetchConfig();
      setShowSystemPromptTab(readSystemPromptTabVisibility(result.config));
    } catch {
      setShowSystemPromptTab(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
    window.addEventListener(SETTINGS_UPDATED_EVENT, loadSettings);
    return () => window.removeEventListener(SETTINGS_UPDATED_EVENT, loadSettings);
  }, [loadSettings]);

  return showSystemPromptTab;
}
