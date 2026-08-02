import { useState } from "react";
import {
  Eye, EyeOff, Plus, Trash2, Save, RefreshCw,
  ChevronDown, ChevronUp, Star, StarOff,
} from "lucide-react";

export interface ModelProvider {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  models: string[];
  maxContextTokens?: number;
  models_config?: Record<string, { maxContextTokens?: number }>;
  hyperparameter_values: Record<string, unknown>;
}

export interface ProviderSchema {
  display_name: string;
  default_base_url: string;
  hyperparams: Record<string, {
    type: "boolean" | "select" | "number";
    default: unknown;
    label: string;
    options?: string[];
    min?: number;
    max?: number;
  }>;
}

interface Props {
  provider: ModelProvider;
  schema: ProviderSchema | null;
  isDefault: boolean;
  onUpdate: (providerId: string, updates: Partial<ModelProvider>) => Promise<void>;
  onDelete: (providerId: string) => Promise<void>;
  onSetDefault: (providerId: string) => Promise<void>;
  onAddModel: (providerId: string, modelName: string) => Promise<void>;
  onRemoveModel: (providerId: string, modelName: string) => Promise<void>;
}

export default function ModelProviderCard({
  provider,
  schema,
  isDefault,
  onUpdate,
  onDelete,
  onSetDefault,
  onAddModel,
  onRemoveModel,
}: Props) {
  const [showApiKey, setShowApiKey] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [newModel, setNewModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [edited, setEdited] = useState(false);
  const [localProvider, setLocalProvider] = useState<ModelProvider>(provider);

  const handleUpdate = async () => {
    setSaving(true);
    try {
      await onUpdate(provider.id, localProvider);
      setEdited(false);
    } finally {
      setSaving(false);
    }
  };

  const handleAddModel = async () => {
    if (!newModel.trim()) return;
    await onAddModel(provider.id, newModel.trim());
    setNewModel("");
  };

  const handleRemoveModel = async (modelName: string) => {
    await onRemoveModel(provider.id, modelName);
  };

  const updateHyperparam = (key: string, value: unknown) => {
    setLocalProvider({
      ...localProvider,
      hyperparameter_values: {
        ...localProvider.hyperparameter_values,
        [key]: value,
      },
    });
    setEdited(true);
  };

  const renderHyperparamInput = (key: string, paramSchema: { type: "boolean" | "select" | "number"; default: unknown; label: string; options?: string[]; min?: number; max?: number }): React.ReactNode => {
    const value = localProvider.hyperparameter_values[key] ?? paramSchema.default;

    if (paramSchema.type === "boolean") {
      return (
        <button
          type="button"
          role="switch"
          aria-checked={!!value}
          aria-label={paramSchema.label}
          onClick={() => updateHyperparam(key, !value)}
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
        </button>
      );
    }

    if (paramSchema.type === "select" && paramSchema.options) {
      return (
        <div className="relative">
          <select
            id={`hyperparam-${provider.id}-${key}`}
            value={String(value)}
            onChange={(e) => updateHyperparam(key, e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-3 pr-8 py-2 text-sm text-slate-200 min-h-[44px]
              focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30
              appearance-none cursor-pointer transition-all duration-200"
          >
            {(paramSchema.options ?? []).map((opt: string) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
          <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" aria-hidden="true" />
        </div>
      );
    }

    if (paramSchema.type === "number") {
      return (
        <div className="flex items-center gap-3">
          <input
            type="range"
            id={`hyperparam-${provider.id}-${key}`}
            min={paramSchema.min ?? 0}
            max={paramSchema.max ?? 100}
            step={paramSchema.min !== undefined && paramSchema.min < 1 ? 0.01 : 1}
            value={Number(value)}
            onChange={(e) => updateHyperparam(key, Number(e.target.value))}
            className="flex-1 h-1.5 bg-slate-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500
              [&::-webkit-slider-thumb]:cursor-pointer"
          />
          <span className="text-sm text-slate-300 font-mono w-16 text-right">{String(value)}</span>
        </div>
      );
    }

    return null;
  };

  return (
    <div className={`bg-slate-900/50 border rounded-xl p-4 transition-all ${
      isDefault ? "border-indigo-500/50" : "border-slate-700"
    }`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
              onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-label={expanded ? "折叠供应商配置" : "展开供应商配置"}
            className="text-slate-400 hover:text-slate-200 transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded-lg p-1"
          >
            {expanded ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
          </button>
          <h3 className="text-lg font-semibold text-slate-200">{localProvider.name}</h3>
          {isDefault && (
            <span className="px-2 py-0.5 text-xs bg-indigo-500/20 text-indigo-400 rounded-full">
              默认
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onSetDefault(provider.id)}
            aria-label={isDefault ? "已是默认供应商" : "设为默认供应商"}
            className={`p-2.5 rounded-lg transition-all duration-200 cursor-pointer min-h-[44px] min-w-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 ${
              isDefault
                ? "text-indigo-400 bg-indigo-500/10"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
            }`}
            title={isDefault ? "已是默认供应商" : "设为默认供应商"}
          >
            {isDefault ? <Star size={16} /> : <StarOff size={16} />}
          </button>
          <button
            type="button"
            onClick={handleUpdate}
            disabled={!edited || saving}
            aria-label={saving ? "保存中..." : "保存更改"}
            className={`p-2.5 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 ${
              edited
                ? "text-green-400 hover:bg-green-500/10 cursor-pointer"
                : "text-slate-600 cursor-not-allowed"
            }`}
            title="保存更改"
          >
            {saving ? <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" /> : <Save size={16} />}
          </button>
          <button
            type="button"
            onClick={() => onDelete(provider.id)}
            aria-label="删除供应商"
            className="p-2.5 text-slate-400 hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-all duration-200 cursor-pointer min-h-[44px] min-w-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/30"
            title="删除供应商"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {/* Content */}
      {expanded && (
        <div className="space-y-4">
          {/* API Key */}
          <div>
            <label htmlFor={`provider-${provider.id}-api-key`} className="block text-sm text-slate-400 mb-1 cursor-pointer">API Key</label>
            <div className="relative">
              <input
                type={showApiKey ? "text" : "password"}
                id={`provider-${provider.id}-api-key`}
                value={localProvider.api_key}
                onChange={(e) => {
                  setLocalProvider({ ...localProvider, api_key: e.target.value });
                  setEdited(true);
                }}
                placeholder="sk-..."
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 min-h-[44px]
                  focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 transition-all duration-200"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                aria-label={showApiKey ? "隐藏 API Key" : "显示 API Key"}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200 transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
              >
                {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Base URL */}
          <div>
            <label htmlFor={`provider-${provider.id}-base-url`} className="block text-sm text-slate-400 mb-1 cursor-pointer">API Base URL</label>
            <input
              type="text"
              id={`provider-${provider.id}-base-url`}
              value={localProvider.base_url}
              onChange={(e) => {
                setLocalProvider({ ...localProvider, base_url: e.target.value });
                setEdited(true);
              }}
              placeholder="https://api.example.com/v1"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[44px]
                focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 transition-all duration-200"
            />
          </div>

          {/* Max Context Tokens */}
          <div>
            <label htmlFor={`provider-${provider.id}-max-tokens`} className="block text-sm text-slate-400 mb-1 cursor-pointer">最大上下文 Tokens</label>
            <input
              type="number"
              id={`provider-${provider.id}-max-tokens`}
              value={localProvider.maxContextTokens || 128000}
              onChange={(e) => {
                setLocalProvider({ ...localProvider, maxContextTokens: parseInt(e.target.value) || 128000 });
                setEdited(true);
              }}
              min={1000}
              step={1000}
              placeholder="128000"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[44px]
                focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 transition-all duration-200"
            />
            <p className="text-xs text-slate-500 mt-1">模型支持的最大上下文窗口大小（tokens）</p>
          </div>

          {/* Models */}
          <div>
            <label htmlFor={`provider-${provider.id}-new-model`} className="block text-sm text-slate-400 mb-2 cursor-pointer">模型列表</label>
            <div className="flex flex-wrap gap-2 mb-2">
              {localProvider.models.map((model) => (
                <span
                  key={model}
                  className="inline-flex items-center gap-1 px-3 py-1 bg-slate-800 border border-slate-600 rounded-full text-sm text-slate-300"
                >
                  {model}
                  <button
                    type="button"
                    onClick={() => handleRemoveModel(model)}
                    aria-label={`移除模型 ${model}`}
                    className="ml-1 text-slate-500 hover:text-red-400 transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
                  >
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                id={`provider-${provider.id}-new-model`}
                value={newModel}
                onChange={(e) => setNewModel(e.target.value)}
                placeholder="输入模型名称"
                className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[44px]
                  focus:border-indigo-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 transition-all duration-200"
                onKeyDown={(e) => e.key === "Enter" && handleAddModel()}
              />
              <button
                type="button"
                onClick={handleAddModel}
                disabled={!newModel.trim()}
                aria-label="添加模型"
                className="px-3 py-2 bg-indigo-500/20 text-indigo-400 rounded-lg hover:bg-indigo-500/30
                  disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 cursor-pointer min-h-[44px]"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>

          {/* Hyperparameters */}
          {schema && Object.keys(schema.hyperparams).length > 0 && (
            <div>
              <label className="block text-sm text-slate-400 mb-2">超参数</label>
              <div className="space-y-3">
                {Object.entries(schema.hyperparams).map(([key, paramSchema]) => (
                  <div key={key} className="flex items-center justify-between gap-4">
                    <label htmlFor={`hyperparam-${provider.id}-${key}`} className="text-sm text-slate-300">{paramSchema.label}</label>
                    {renderHyperparamInput(key, paramSchema)}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
