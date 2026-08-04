import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, Settings } from "lucide-react";
import { getModelProviders, updateSessionModel } from "../lib/api";
import type { ModelProvider, SessionDetail } from "../types";

type MenuKey = "provider" | "model" | "effort";

const EFFORT_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  max: "极高",
  xhigh: "极高",
};

const MENU_LABELS: Record<MenuKey, string> = {
  provider: "供应商",
  model: "模型",
  effort: "推理强度",
};

interface Props {
  sessionId: string | null;
  session: SessionDetail | null;
  disabled?: boolean;
  onUpdated: (modelId: string, modelParams: Record<string, unknown>) => void;
  onOpenSettings: () => void;
}

function SelectionRow({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex min-h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-sm transition-colors ${
        active
          ? "bg-indigo-500/15 text-indigo-100"
          : "text-slate-300 hover:bg-slate-700/70 hover:text-slate-100"
      }`}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {value ? <span className="max-w-24 truncate text-[11px] text-slate-500">{value}</span> : null}
      {active ? <Check size={14} className="shrink-0 text-indigo-400" /> : null}
    </button>
  );
}

export default function ModelSwitcher({
  sessionId,
  session,
  disabled = false,
  onUpdated,
  onOpenSettings,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeMenu, setActiveMenu] = useState<MenuKey>("model");
  const [providers, setProviders] = useState<Record<string, Omit<ModelProvider, "id">>>({});
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getModelProviders();
      setProviders(result.providers);
      setError(null);
    } catch {
      setError("模型配置加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const [selectedProviderId, selectedModelName] = (session?.model_id || "").split(":", 2);
  const selectedProvider = providers[selectedProviderId];
  const configuredEffort = typeof session?.model_params?.reasoning_effort === "string"
    ? session.model_params.reasoning_effort
    : null;
  const providerEntries = Object.entries(providers);
  const supportedEfforts = selectedProvider?.capabilities?.reasoning_efforts || [];
  const selectedEffort = configuredEffort && supportedEfforts.includes(configuredEffort)
    ? configuredEffort
    : null;
  const displayEffort = selectedEffort ? (EFFORT_LABELS[selectedEffort] || selectedEffort) : "默认";
  const hasModels = providerEntries.some(([, provider]) => provider.models.length > 0);

  const menuValues = useMemo(() => ({
    provider: selectedProvider?.name || "未配置",
    model: selectedModelName || "未配置",
    effort: displayEffort,
  }), [displayEffort, selectedModelName, selectedProvider?.name]);

  const applySelection = async (
    providerId: string,
    modelName: string,
    effort: string | null,
  ) => {
    if (!sessionId || updating) return;
    setUpdating(true);
    setError(null);
    try {
      const result = await updateSessionModel(sessionId, `${providerId}:${modelName}`, effort);
      onUpdated(result.model_id, result.model_params);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模型切换失败");
    } finally {
      setUpdating(false);
    }
  };

  const renderSubmenu = () => {
    if (activeMenu === "provider") {
      return providerEntries.map(([providerId, provider]) => (
        <SelectionRow
          key={providerId}
          label={provider.name}
          value={`${provider.models.length} 个模型`}
          active={providerId === selectedProviderId}
          onClick={() => {
            const firstModel = provider.models[0];
            if (!firstModel) return;
            const efforts = provider.capabilities?.reasoning_efforts || [];
            const retainedEffort = configuredEffort && efforts.includes(configuredEffort)
              ? configuredEffort
              : null;
            void applySelection(providerId, firstModel, retainedEffort);
          }}
        />
      ));
    }

    if (activeMenu === "model") {
      if (!selectedProvider) return <p className="p-2.5 text-sm text-slate-500">请先选择供应商</p>;
      return selectedProvider.models.map((model) => (
        <SelectionRow
          key={model}
          label={model}
          active={model === selectedModelName}
          onClick={() => void applySelection(selectedProviderId, model, selectedEffort)}
        />
      ));
    }

    return (
      <>
        <SelectionRow
          label="默认"
          active={selectedEffort === null}
          onClick={() => {
            if (selectedModelName) void applySelection(selectedProviderId, selectedModelName, null);
          }}
        />
        {supportedEfforts.map((effort) => (
          <SelectionRow
            key={effort}
            label={EFFORT_LABELS[effort] || effort}
            value={effort}
            active={effort === selectedEffort}
            onClick={() => {
              if (selectedModelName) void applySelection(selectedProviderId, selectedModelName, effort);
            }}
          />
        ))}
      </>
    );
  };

  const switchDisabled = disabled || !sessionId || session?.type !== "main" || session?.runtime_scope === "workflow";

  return (
    <div ref={rootRef} className="relative shrink-0">
      {open ? (
        <div className="absolute bottom-[calc(100%+0.65rem)] right-0 z-40 flex items-end gap-2">
          <div className="w-60 overflow-hidden rounded-xl border border-slate-600/80 bg-slate-800 p-1.5 shadow-2xl shadow-slate-950/50">
            {!hasModels && !loading ? (
              <div className="p-2">
                <p className="text-sm font-medium text-slate-200">尚未配置模型</p>
                <button
                  type="button"
                  onClick={onOpenSettings}
                  className="mt-2 flex min-h-9 items-center gap-2 rounded-lg px-2.5 text-xs font-medium text-indigo-300 hover:bg-indigo-500/10"
                >
                  <Settings size={14} aria-hidden="true" />
                  前往模型设置
                </button>
              </div>
            ) : (
              (Object.keys(MENU_LABELS) as MenuKey[]).map((menu) => (
                <button
                  key={menu}
                  type="button"
                  onMouseEnter={() => setActiveMenu(menu)}
                  onFocus={() => setActiveMenu(menu)}
                  onClick={() => setActiveMenu(menu)}
                  className={`flex min-h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-sm transition-colors ${
                    activeMenu === menu
                      ? "bg-slate-700/90 text-slate-100"
                      : "text-slate-300 hover:bg-slate-700/60"
                  }`}
                >
                  <span className="flex-1">{MENU_LABELS[menu]}</span>
                  <span className="max-w-24 truncate text-xs text-slate-500">{menuValues[menu]}</span>
                  <ChevronRight size={14} className="shrink-0 text-slate-500" aria-hidden="true" />
                </button>
              ))
            )}
            {(error || updating) ? (
              <div className="mt-1 border-t border-slate-700 px-2.5 pt-2 text-xs text-slate-400">
                {updating ? "正在切换模型" : error}
              </div>
            ) : null}
          </div>

          {hasModels ? (
            <div className="max-h-64 w-48 overflow-y-auto rounded-xl border border-slate-600/80 bg-slate-800 p-1.5 shadow-2xl shadow-slate-950/50">
              {loading ? (
                <div className="flex min-h-20 items-center justify-center gap-2 text-sm text-slate-500">
                  <Loader2 size={15} className="animate-spin motion-reduce:animate-none" />
                  加载中
                </div>
              ) : renderSubmenu()}
            </div>
          ) : null}
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => {
          if (!open) void loadProviders();
          setOpen(!open);
        }}
        disabled={switchDisabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="切换模型"
        className="flex h-9 max-w-52 items-center gap-2 rounded-full bg-slate-700/75 px-3 text-xs text-slate-300 transition-colors hover:bg-slate-700 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-45"
      >
        <span className={`h-3 w-3 shrink-0 rounded-full border border-slate-500 border-t-indigo-400 ${updating || loading ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden="true" />
        <span className="truncate">{selectedModelName || (loading ? "加载模型" : "未配置模型")}</span>
        {selectedModelName ? <span className="shrink-0 text-slate-500">{displayEffort}</span> : null}
        <ChevronDown size={13} className="shrink-0 text-slate-500" aria-hidden="true" />
      </button>
    </div>
  );
}
