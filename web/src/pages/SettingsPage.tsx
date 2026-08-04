import { useState, useEffect, useRef } from "react";
import {
  Settings, Cpu, Users, MessageCircle, Server, Code,
  Save, RefreshCw, Eye, EyeOff, Lock, ChevronDown, ChevronUp,
  Plus, Trash2,
} from "lucide-react";
import { useSettings } from "../hooks/useSettings";
import { ConfigItemMeta } from "../types";
import ModelProviderCard, { type ModelProvider, type ProviderSchema } from "../components/ModelProviderCard";
import AddModelProviderDialog from "../components/AddModelProviderDialog";
import {
  addModelProvider,
  deleteModelProvider,
  discoverProviderModels,
  getModelProviders,
  getProviderSchemas,
  prioritizeModelProvider,
  updateModelProvider,
} from "../lib/api";
import { DesktopUpdatePanel } from "../desktop-updater/DesktopUpdatePanel";
import CompressionConfigSection from "./CompressionConfigPage";

interface ConfigGroupDef {
  key: string;
  label: string;
  icon: React.ReactNode;
  color: string;
}

const CONFIG_GROUPS: ConfigGroupDef[] = [
  // 移除 "API 配置" 和 "模型参数"，已迁移到模型供应商管理
  { key: "agent", label: "多 Agent 参数", icon: <Users size={18} />, color: "cyan" },
  { key: "roundtable", label: "圆桌会议参数", icon: <MessageCircle size={18} />, color: "green" },
  { key: "coding", label: "编码工具", icon: <Code size={18} />, color: "rose" },
  { key: "system", label: "系统参数", icon: <Server size={18} />, color: "amber" },
];

// 静态颜色映射，避免动态类名导致 Tailwind 无法检测
const CONFIG_COLOR_MAP: Record<string, string> = {
  "cyan": "text-cyan-400",
  "green": "text-green-400",
  "rose": "text-rose-400",
  "amber": "text-amber-400",
  "purple": "text-purple-400",
  "indigo": "text-indigo-400",
};

function ConfigInput({
  item, value, onChange, edited,
}: {
  item: ConfigItemMeta;
  value: string | number | boolean;
  onChange: (v: string | number | boolean) => void;
  edited: boolean;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const inputId = `config-${item.key}`;

  if (item.readonly) {
    return (
      <div className="flex items-center gap-2">
        <Lock size={14} className="text-slate-500" />
        <span className="text-slate-400 text-sm font-mono">{String(value)}</span>
      </div>
    );
  }

  if (item.type === "boolean") {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={!!value}
        aria-label={item.label}
        onClick={() => onChange(!value)}
        className={`relative w-12 h-6 rounded-full transition-all duration-300 cursor-pointer ${
          value ? "bg-green-500/30 border-green-500/50" : "bg-slate-800 border-slate-600"
        } border hover:border-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50`}
      >
        <span
          className={`absolute top-0.5 w-5 h-5 rounded-full transition-all duration-300 ${
            value
              ? "left-6 bg-green-500"
              : "left-0.5 bg-slate-500"
          }`}
        />
        {edited && <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-500 rounded-full" />}
      </button>
    );
  }

  if (item.type === "select" && item.options) {
    return (
      <div className="relative">
        <select
          id={inputId}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-3 pr-8 py-2 text-sm text-slate-200 min-h-[44px]
            focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30
            appearance-none cursor-pointer transition-all duration-200"
        >
          {item.options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" aria-hidden="true" />
        {edited && <span className="absolute top-1 -right-1 w-2 h-2 bg-amber-500 rounded-full" />}
      </div>
    );
  }

  if (item.type === "number") {
    const hasSlider = item.step !== undefined && item.min !== undefined && item.max !== undefined;
    return (
      <div className="flex items-center gap-3">
        {hasSlider && (
          <input
            type="range"
            id={`${inputId}-range`}
            min={item.min}
            max={item.max}
            step={item.step}
            value={Number(value)}
            onChange={(e) => onChange(Number(e.target.value))}
            className="flex-1 h-1.5 bg-slate-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500
              [&::-webkit-slider-thumb]:cursor-pointer"
          />
        )}
        <div className="relative">
          <input
            type="number"
            id={inputId}
            min={item.min}
            max={item.max}
            step={item.step || 1}
            value={Number(value)}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-24 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[44px]
              focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30
              text-center font-mono transition-all duration-200
              [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
          />
          {edited && <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-500 rounded-full" />}
        </div>
      </div>
    );
  }

  // string type
  return (
    <div className="relative flex items-center gap-2">
      <input
        type={item.sensitive && !showSecret ? "password" : "text"}
        id={inputId}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[44px]
          focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30
          font-mono transition-all duration-200"
      />
      {item.sensitive && (
        <button
          type="button"
          onClick={() => setShowSecret(!showSecret)}
          aria-label={showSecret ? "隐藏密钥" : "显示密钥"}
          className="text-slate-400 hover:text-slate-200 transition-colors duration-200 p-1 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
        >
          {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      )}
      {edited && <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-500 rounded-full" />}
    </div>
  );
}

function ConfigGroup({
  group, items, getDisplayValue, setValue, isEdited,
}: {
  group: ConfigGroupDef;
  items: ConfigItemMeta[];
  getDisplayValue: (key: string) => string | number | boolean;
  setValue: (key: string, value: string | number | boolean) => void;
  isEdited: (key: string) => boolean;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const editedCount = items.filter((item) => isEdited(item.key)).length;

  const sectionId = `config-group-${group.key}`;

  return (
    <section aria-label={group.label} className="bg-slate-800/80 rounded-xl border border-slate-700/50 overflow-hidden transition-all duration-300 hover:border-slate-600/50">
      <button
        type="button"
        aria-expanded={!collapsed}
        aria-controls={sectionId}
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-white/[0.02] transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
      >
        <div className="flex items-center gap-3">
          <div className={CONFIG_COLOR_MAP[group.color] || "text-slate-400"}>{group.icon}</div>
          <h3 className="text-base font-semibold text-slate-100">{group.label}</h3>
          {editedCount > 0 && (
            <span className="px-2 py-0.5 text-xs rounded-full bg-amber-500/20 text-amber-500 border border-amber-500/30">
              {editedCount} 项已修改
            </span>
          )}
        </div>
        {collapsed ? <ChevronDown size={18} className="text-slate-400" /> : <ChevronUp size={18} className="text-slate-400" />}
      </button>
      {!collapsed && (
        <div id={sectionId} className="px-5 pb-5 space-y-4">
          {items.map((item) => (
            <div key={item.key} className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 sm:gap-4">
              <div className="flex-shrink-0 sm:w-48">
                <label htmlFor={`config-${item.key}`} className="text-sm text-slate-300 font-medium cursor-pointer">{item.label}</label>
                <p className="text-xs text-slate-500 mt-0.5 font-mono">{item.key}</p>
              </div>
              <div className="flex-1 sm:max-w-md">
                <ConfigInput
                  item={item}
                  value={getDisplayValue(item.key)}
                  onChange={(v) => setValue(item.key, v)}
                  edited={isEdited(item.key)}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default function SettingsPage() {
  const {
    meta, loading, saving, hasChanges,
    loadConfig, setValue, saveConfig, resetChanges, getDisplayValue, isEdited,
  } = useSettings();

  // 模型供应商状态
  const [providers, setProviders] = useState<Record<string, Omit<ModelProvider, "id">>>({});
  const [schemas, setSchemas] = useState<Record<string, ProviderSchema>>({});
  const [defaultProvider, setDefaultProvider] = useState<string>("");
  const [providersLoading, setProvidersLoading] = useState(true);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const [addDialogOpen, setAddDialogOpen] = useState(false);

  // 删除供应商确认对话框状态
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteProviderId, setDeleteProviderId] = useState<string | null>(null);
  const deleteConfirmRef = useRef<HTMLButtonElement>(null);

  // 加载模型供应商配置
  const loadProviders = async () => {
    setProvidersLoading(true);
    setProvidersError(null);
    try {
      const [providersData, schemasData] = await Promise.all([
        getModelProviders(),
        getProviderSchemas(),
      ]);
      setProviders(providersData.providers);
      setDefaultProvider(providersData.default_provider || "");
      setSchemas(schemasData.schemas);
    } catch {
      setProvidersError("加载模型供应商失败，请检查网络连接后重试");
    } finally {
      setProvidersLoading(false);
    }
  };

  useEffect(() => {
    loadProviders();
  }, []);

  // 删除确认对话框焦点管理
  useEffect(() => {
    if (!deleteDialogOpen) return;
    deleteConfirmRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDeleteDialogOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [deleteDialogOpen]);

  const groupedMeta: Record<string, ConfigItemMeta[]> = {};
  for (const item of meta) {
    if (!groupedMeta[item.group]) groupedMeta[item.group] = [];
    groupedMeta[item.group].push(item);
  }

  if (loading || providersLoading) {
    return (
      <div className="h-full flex items-center justify-center" role="status">
        <div className="flex items-center gap-3 text-slate-400">
          <RefreshCw size={20} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span className="sr-only">加载配置中...</span>
          <span aria-hidden="true">加载配置中...</span>
        </div>
      </div>
    );
  }

  return (
    <div role="main" aria-label="系统配置页面" className="h-[calc(100dvh-3.5rem)] overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
        {/* 操作栏 */}
        <nav aria-label="配置操作" className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Settings size={22} className="text-indigo-400" />
            <div>
              <h2 className="text-xl font-bold text-slate-100">系统配置</h2>
              <p className="text-xs text-slate-500 mt-0.5">{meta.length} 个配置项</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {hasChanges && (
              <button
                type="button"
                onClick={resetChanges}
                aria-label="撤销所有修改"
                className="px-4 py-2 text-sm rounded-lg border border-slate-600 text-slate-300
                  hover:bg-slate-700 transition-all duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
              >
                撤销修改
              </button>
            )}
            <button
              type="button"
              onClick={loadConfig}
              aria-label="重新加载配置"
              className="p-2 rounded-lg border border-slate-600 text-slate-400
                hover:bg-slate-700 hover:text-slate-200 transition-all duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
              title="重新加载"
            >
              <RefreshCw size={16} />
            </button>
            <button
              type="button"
              onClick={() => saveConfig(true)}
              disabled={!hasChanges || saving}
              aria-label={saving ? "保存中" : "保存配置"}
              className={`flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer ${
                hasChanges && !saving
                  ? "bg-indigo-600 text-white hover:bg-indigo-500"
                  : "bg-slate-700 text-slate-500 cursor-not-allowed"
              }`}
            >
              {saving ? (
                <RefreshCw size={14} className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Save size={14} />
              )}
              {saving ? "保存中..." : "保存配置"}
            </button>
          </div>
        </nav>

        <DesktopUpdatePanel />

        {/* 模型供应商配置 */}
        <section aria-label="模型配置" className="bg-slate-800/80 rounded-xl border border-slate-700/50 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-3">
              <div className="text-purple-400"><Cpu size={18} /></div>
              <h3 className="text-base font-semibold text-slate-100">模型配置</h3>
              <span className="text-xs text-slate-500">{Object.keys(providers).length} 个供应商</span>
            </div>
            <button
              type="button"
              onClick={() => setAddDialogOpen(true)}
              aria-label="添加新的模型供应商"
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg border border-slate-600 text-slate-300
                hover:bg-slate-700 transition-all cursor-pointer min-h-[44px]"
            >
              <Plus size={14} />
              添加供应商
            </button>
          </div>
          <div className="px-5 pb-5 space-y-4">
            {Object.entries(providers).map(([providerId, provider]) => (
              <ModelProviderCard
                key={providerId}
                provider={{ id: providerId, ...provider, hyperparameter_values: provider.hyperparameter_values || {} }}
                schema={schemas[providerId] || null}
                isDefault={providerId === defaultProvider}
                onUpdate={async (id, updates) => {
                  await updateModelProvider(id, updates);
                  await loadProviders();
                }}
                onDelete={async (id) => {
                  setDeleteProviderId(id);
                  setDeleteDialogOpen(true);
                }}
                onPrioritize={async (id) => {
                  await prioritizeModelProvider(id);
                  await loadProviders();
                }}
                onDiscoverModels={async (input) => {
                  const result = await discoverProviderModels(input);
                  return result.models;
                }}
              />
            ))}
            {providersError && (
              <div role="alert" className="flex items-center justify-between p-4 rounded-lg bg-red-500/10 border border-red-500/20">
                <span className="text-sm text-red-400">{providersError}</span>
                <button
                  type="button"
                  onClick={loadProviders}
                  aria-label="重试加载模型供应商"
                  className="px-3 py-1.5 text-xs rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer min-h-[44px]"
                >
                  重试
                </button>
              </div>
            )}
            {!providersError && Object.keys(providers).length === 0 && (
              <div role="status" className="text-center py-8 text-slate-500">
                暂无模型供应商配置，点击上方按钮添加
              </div>
            )}
          </div>

          <AddModelProviderDialog
            open={addDialogOpen}
            schemas={schemas}
            existingProviderIds={Object.keys(providers)}
            onClose={() => setAddDialogOpen(false)}
            onAdd={async (input) => {
              await addModelProvider(input);
              await loadProviders();
            }}
            onDiscoverModels={async (input) => {
              const result = await discoverProviderModels(input);
              return result.models;
            }}
          />

          {/* 删除供应商确认对话框 */}
          {deleteDialogOpen && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
              onClick={(e) => { if (e.target === e.currentTarget) setDeleteDialogOpen(false); }}
              role="presentation"
            >
              <div
                role="dialog"
                aria-modal="true"
                aria-label="确认删除供应商"
                aria-describedby="delete-provider-desc"
                className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-sm mx-4 space-y-4"
              >
                <p id="delete-provider-desc" className="sr-only">此操作将永久删除供应商配置且不可撤销</p>
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-red-500/10">
                    <Trash2 size={18} className="text-red-400" aria-hidden="true" />
                  </div>
                  <h3 className="text-lg font-semibold text-slate-100">删除供应商</h3>
                </div>
                <p className="text-sm text-slate-400">
                  确定删除供应商 <span className="font-mono text-slate-200">{deleteProviderId}</span> 吗？此操作不可撤销。
                </p>
                <div className="flex justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setDeleteDialogOpen(false)}
                    className="px-4 py-2 text-sm rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-800 transition-colors duration-200 cursor-pointer min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
                  >
                    取消
                  </button>
                  <button
                    ref={deleteConfirmRef}
                    type="button"
                    onClick={async () => {
                      if (!deleteProviderId) return;
                      try {
                        await deleteModelProvider(deleteProviderId);
                        await loadProviders();
                      } catch {
                        setProvidersError("删除供应商失败，请重试");
                      } finally {
                        setDeleteDialogOpen(false);
                        setDeleteProviderId(null);
                      }
                    }}
                    className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-500 transition-colors duration-200 cursor-pointer min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* 其他配置分组卡片 */}
        {CONFIG_GROUPS.filter((group) => group.key !== "system").map((group) => {
          const items = groupedMeta[group.key];
          if (!items || items.length === 0) return null;
          return (
            <ConfigGroup
              key={group.key}
              group={group}
              items={items}
              getDisplayValue={getDisplayValue}
              setValue={setValue}
              isEdited={isEdited}
            />
          );
        })}

        <CompressionConfigSection />

        {CONFIG_GROUPS.filter((group) => group.key === "system").map((group) => {
          const items = groupedMeta[group.key];
          if (!items || items.length === 0) return null;
          return (
            <ConfigGroup
              key={group.key}
              group={group}
              items={items}
              getDisplayValue={getDisplayValue}
              setValue={setValue}
              isEdited={isEdited}
            />
          );
        })}
      </div>
    </div>
  );
}
