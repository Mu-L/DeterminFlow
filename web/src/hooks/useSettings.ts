import { useState, useEffect, useCallback } from "react";
import { fetchConfig, updateConfig } from "../lib/api";
import { notifySettingsUpdated } from "../lib/navigation-settings";
import { ConfigItemMeta } from "../types";

export interface SettingsState {
  config: Record<string, string | number | boolean>;
  meta: ConfigItemMeta[];
  loading: boolean;
  saving: boolean;
  error: string | null;
  editedValues: Record<string, string | number | boolean>;
  hasChanges: boolean;
}

export function useSettings() {
  const [state, setState] = useState<SettingsState>({
    config: {},
    meta: [],
    loading: true,
    saving: false,
    error: null,
    editedValues: {},
    hasChanges: false,
  });

  const loadConfig = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fetchConfig();
      setState((s) => ({
        ...s,
        config: data.config,
        meta: data.meta,
        loading: false,
        editedValues: {},
        hasChanges: false,
      }));
    } catch (e) {
      console.error("加载配置失败:", e);
      setState((s) => ({ ...s, loading: false, error: "加载配置失败" }));
    }
  }, []);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const setValue = useCallback((key: string, value: string | number | boolean) => {
    setState((s) => {
      const newEdited = { ...s.editedValues, [key]: value };
      // 如果值回到了原值则移除
      if (s.config[key] === value) {
        delete newEdited[key];
      }
      return {
        ...s,
        editedValues: newEdited,
        hasChanges: Object.keys(newEdited).length > 0,
      };
    });
  }, []);

  const saveConfig = useCallback(async (persist: boolean = true) => {
    if (!state.hasChanges) return;
    setState((s) => ({ ...s, saving: true, error: null }));
    try {
      const data = await updateConfig(state.editedValues, persist);
      if (data.success) {
        setState((s) => ({
          ...s,
          config: data.config,
          saving: false,
          editedValues: {},
          hasChanges: false,
        }));
        notifySettingsUpdated();
      } else {
        setState((s) => ({
          ...s,
          saving: false,
          error: "保存失败",
        }));
      }
    } catch (e) {
      console.error("保存配置失败:", e);
      setState((s) => ({
        ...s,
        saving: false,
        error: "保存失败，请重试",
      }));
    }
  }, [state.hasChanges, state.editedValues]);

  const resetChanges = useCallback(() => {
    setState((s) => ({ ...s, editedValues: {}, hasChanges: false }));
  }, []);

  const getDisplayValue = useCallback((key: string) => {
    if (key in state.editedValues) return state.editedValues[key];
    return state.config[key];
  }, [state.config, state.editedValues]);

  const isEdited = useCallback((key: string) => {
    return key in state.editedValues;
  }, [state.editedValues]);

  return {
    ...state,
    loadConfig,
    setValue,
    saveConfig,
    resetChanges,
    getDisplayValue,
    isEdited,
  };
}
