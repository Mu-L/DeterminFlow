import { useState, useEffect, useRef, useCallback, type ReactNode } from "react";
import { Save, RefreshCw, Sliders, RotateCcw, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { useToast } from "@/components/ui/use-toast";

interface CompressionConfig {
  general: {
    compactionThreshold: number;
    enabled: boolean;
  };
  micro_compact: {
    maxToolResults: number;
    toolResultTokenRatio: number;
    keepRecentToolResults: number;
    placeholder: string;
  };
  full_compact: {
    keepRecentTokens: number;
    maxRetryCount: number;
    summaryTokenBudget: number;
  };
  reactive_compact: {
    maxRetryCount: number;
  };
  post_compact: {
    maxFilesToRead: number;
    maxTokensPerFile: number;
  };
  transcript: {
    logsDir: string;
  };
}

const defaultConfig: CompressionConfig = {
  general: {
    compactionThreshold: 0.80,
    enabled: true,
  },
  micro_compact: {
    maxToolResults: 15,
    toolResultTokenRatio: 0.40,
    keepRecentToolResults: 5,
    placeholder: "[Content compacted]",
  },
  full_compact: {
    keepRecentTokens: 51200,
    maxRetryCount: 2,
    summaryTokenBudget: 4096,
  },
  reactive_compact: {
    maxRetryCount: 5,
  },
  post_compact: {
    maxFilesToRead: 5,
    maxTokensPerFile: 5000,
  },
  transcript: {
    logsDir: "./logs/compression",
  },
};

function CompressionField({
  id,
  label,
  description,
  children,
}: {
  id: string;
  label: ReactNode;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start sm:gap-4">
      <div className="flex min-w-0 flex-col gap-1 pt-1 sm:w-48 sm:flex-none">
        <Label htmlFor={id} className="cursor-pointer text-sm font-medium text-slate-300">
          {label}
        </Label>
        {description ? (
          <p className="text-xs leading-5 text-slate-500">{description}</p>
        ) : null}
      </div>
      <div className="min-w-0 flex-1 sm:max-w-md">{children}</div>
    </div>
  );
}

function CompressionGroup({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h4 id={id} className="text-sm font-semibold text-slate-200">
          {title}
        </h4>
        {description ? (
          <p className="text-xs text-slate-500">{description}</p>
        ) : null}
      </div>
      <div className="flex flex-col gap-4">{children}</div>
    </section>
  );
}

function CompressionConfigEditor() {
  const [config, setConfig] = useState<CompressionConfig>(defaultConfig);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showResetDialog, setShowResetDialog] = useState(false);
  const resetDialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const { toast } = useToast();

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch('/api/compression/config');
      if (!response.ok) {
        throw new Error('加载失败');
      }

      const data = await response.json();
      setConfig(data);
    } catch (error) {
      console.error("加载配置失败:", error);
      setError("无法加载压缩配置，使用默认配置");
      toast({
        title: "加载失败",
        description: "无法加载压缩配置，使用默认配置",
        variant: "destructive",
      });
      // 加载失败时使用默认配置
      setConfig(defaultConfig);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const saveConfig = async () => {
    try {
      setSaving(true);

      const response = await fetch('/api/compression/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '保存失败');
      }

      toast({
        title: "保存成功",
        description: "压缩配置已更新",
      });
    } catch (error) {
      console.error("保存配置失败:", error);
      toast({
        title: "保存失败",
        description: error instanceof Error ? error.message : "无法保存压缩配置",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const resetConfig = () => {
    setShowResetDialog(true);
  };

  const confirmReset = () => {
    setConfig(defaultConfig);
    setShowResetDialog(false);
    toast({
      title: "已重置",
      description: "配置已恢复为默认值",
    });
  };

  // 重置确认对话框焦点管理
  useEffect(() => {
    if (!showResetDialog || !resetDialogRef.current) return;
    const el = resetDialogRef.current;
    confirmBtnRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setShowResetDialog(false); return; }
      if (e.key !== "Tab") return;
      const focusable = el.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [showResetDialog]);

  const updateGeneralConfig = (key: keyof CompressionConfig['general'], value: boolean | number) => {
    setConfig(prev => ({
      ...prev,
      general: { ...prev.general, [key]: value },
    }));
  };

  const updateMicroConfig = (key: keyof CompressionConfig['micro_compact'], value: number | string) => {
    setConfig(prev => ({
      ...prev,
      micro_compact: { ...prev.micro_compact, [key]: value },
    }));
  };

  const updateFullConfig = (key: keyof CompressionConfig['full_compact'], value: number) => {
    setConfig(prev => ({
      ...prev,
      full_compact: { ...prev.full_compact, [key]: value },
    }));
  };

  const updateReactiveConfig = (key: keyof CompressionConfig['reactive_compact'], value: number) => {
    setConfig(prev => ({
      ...prev,
      reactive_compact: { ...prev.reactive_compact, [key]: value },
    }));
  };

  const updatePostConfig = (key: keyof CompressionConfig['post_compact'], value: number) => {
    setConfig(prev => ({
      ...prev,
      post_compact: { ...prev.post_compact, [key]: value },
    }));
  };

  const updateTranscriptConfig = (key: keyof CompressionConfig['transcript'], value: string) => {
    setConfig(prev => ({
      ...prev,
      transcript: { ...prev.transcript, [key]: value },
    }));
  };

  if (loading) {
    return (
      <div className="flex min-h-32 items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse motion-reduce:animate-none" role="status" aria-label="正在加载压缩配置">
          <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span>加载压缩配置...</span>
          <span className="sr-only">正在加载压缩配置，请稍候</span>
        </div>
      </div>
    );
  }

  return (
    <div aria-label="压缩配置编辑器" className="flex flex-col gap-5">
      {error && (
        <div className="flex items-center gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4" role="alert" aria-live="polite">
          <AlertTriangle size={16} className="shrink-0 text-red-500" aria-hidden="true" />
          <span className="flex-1 text-sm">{error}</span>
          <Button
            variant="outline"
            size="sm"
            type="button"
            onClick={loadConfig}
            aria-label="重试加载配置"
            className="min-h-11 cursor-pointer"
          >
            <RefreshCw data-icon="inline-start" aria-hidden="true" />
            重试
          </Button>
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={resetConfig}
          type="button"
          aria-label="重置为默认配置"
          className="min-h-11 cursor-pointer"
        >
          <RotateCcw data-icon="inline-start" aria-hidden="true" />
          重置默认
        </Button>
        <Button
          size="sm"
          onClick={saveConfig}
          disabled={saving}
          type="button"
          aria-label="保存压缩配置"
          className="min-h-11 cursor-pointer"
        >
          {saving ? (
            <RefreshCw data-icon="inline-start" className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <Save data-icon="inline-start" aria-hidden="true" />
          )}
          {saving ? "保存中..." : "保存配置"}
        </Button>
      </div>

      <CompressionGroup id="compression-general-heading" title="通用">
        <CompressionField id="compression-enabled" label="启用压缩">
          <Switch
            id="compression-enabled"
            checked={config.general.enabled}
            onCheckedChange={(checked) => updateGeneralConfig('enabled', checked)}
            aria-label="启用或禁用压缩功能"
          />
        </CompressionField>
        <CompressionField
          id="compactionThreshold"
          label={`压缩触发阈值 (${(config.general.compactionThreshold * 100).toFixed(0)}%)`}
          description="上下文占用率达到该值时触发 FullCompact"
        >
          <div className="flex flex-col gap-2 pt-2">
            <Slider
              id="compactionThreshold"
              value={[config.general.compactionThreshold * 100]}
              onValueChange={([value]) => updateGeneralConfig('compactionThreshold', value / 100)}
              max={95}
              min={50}
              step={5}
              aria-label="压缩触发阈值"
            />
            <div className="flex justify-between text-xs text-slate-500">
              <span>50%</span>
              <span>95%</span>
            </div>
          </div>
        </CompressionField>
      </CompressionGroup>

      <Separator />

      <CompressionGroup id="compression-micro-heading" title="MicroCompact" description="工具结果微压缩">
        <CompressionField
          id="maxToolResults"
          label="工具结果数量阈值"
          description="达到该数量后触发工具结果压缩"
        >
          <Input
            id="maxToolResults"
            type="number"
            value={config.micro_compact.maxToolResults}
            onChange={(e) => updateMicroConfig('maxToolResults', parseInt(e.target.value) || 0)}
            min={5}
            max={50}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
        <CompressionField id="keepRecentToolResults" label="保留最近工具结果数">
          <Input
            id="keepRecentToolResults"
            type="number"
            value={config.micro_compact.keepRecentToolResults}
            onChange={(e) => updateMicroConfig('keepRecentToolResults', parseInt(e.target.value) || 0)}
            min={1}
            max={20}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
        <CompressionField
          id="toolResultTokenRatio"
          label={`工具结果 Token 占比 (${(config.micro_compact.toolResultTokenRatio * 100).toFixed(0)}%)`}
          description="工具结果占上下文的比例达到该值时触发压缩"
        >
          <div className="flex flex-col gap-2 pt-2">
            <Slider
              id="toolResultTokenRatio"
              value={[config.micro_compact.toolResultTokenRatio * 100]}
              onValueChange={([value]) => updateMicroConfig('toolResultTokenRatio', value / 100)}
              max={80}
              min={10}
              step={5}
              aria-label="工具结果 Token 占比阈值"
            />
            <div className="flex justify-between text-xs text-slate-500">
              <span>10%</span>
              <span>80%</span>
            </div>
          </div>
        </CompressionField>
        <CompressionField id="placeholder" label="占位符文本" description="替换压缩后的工具结果原文">
          <Input
            id="placeholder"
            value={config.micro_compact.placeholder}
            onChange={(e) => updateMicroConfig('placeholder', e.target.value)}
            placeholder="[Content compacted]"
            className="min-h-11 font-mono"
          />
        </CompressionField>
      </CompressionGroup>

      <Separator />

      <CompressionGroup id="compression-full-heading" title="FullCompact" description="全量摘要压缩">
        <CompressionField id="keepRecentTokens" label="保留最近 Token 数">
          <Input
            id="keepRecentTokens"
            type="number"
            value={config.full_compact.keepRecentTokens}
            onChange={(e) => updateFullConfig('keepRecentTokens', parseInt(e.target.value) || 0)}
            min={10000}
            max={200000}
            step={1000}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
        <CompressionField id="summaryTokenBudget" label="摘要生成最大 Token 数">
          <Input
            id="summaryTokenBudget"
            type="number"
            value={config.full_compact.summaryTokenBudget}
            onChange={(e) => updateFullConfig('summaryTokenBudget', parseInt(e.target.value) || 0)}
            min={1000}
            max={100000}
            step={500}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
        <CompressionField id="fullCompactMaxRetryCount" label="最大重试次数">
          <Input
            id="fullCompactMaxRetryCount"
            type="number"
            value={config.full_compact.maxRetryCount}
            onChange={(e) => updateFullConfig('maxRetryCount', parseInt(e.target.value))}
            min={0}
            max={5}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
      </CompressionGroup>

      <Separator />

      <CompressionGroup id="compression-reactive-heading" title="ReactiveCompact" description="渐进式丢弃压缩">
        <CompressionField id="reactiveCompactMaxRetryCount" label="最大重试次数">
          <Input
            id="reactiveCompactMaxRetryCount"
            type="number"
            value={config.reactive_compact.maxRetryCount}
            onChange={(e) => updateReactiveConfig('maxRetryCount', parseInt(e.target.value))}
            min={1}
            max={10}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
      </CompressionGroup>

      <Separator />

      <CompressionGroup id="compression-post-heading" title="后处理">
        <CompressionField id="maxFilesToRead" label="最多重读文件数">
          <Input
            id="maxFilesToRead"
            type="number"
            value={config.post_compact.maxFilesToRead}
            onChange={(e) => updatePostConfig('maxFilesToRead', parseInt(e.target.value) || 0)}
            min={0}
            max={10}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
        <CompressionField id="maxTokensPerFile" label="每文件重读 Token 上限">
          <Input
            id="maxTokensPerFile"
            type="number"
            value={config.post_compact.maxTokensPerFile}
            onChange={(e) => updatePostConfig('maxTokensPerFile', parseInt(e.target.value) || 0)}
            min={1000}
            max={20000}
            step={1000}
            className="min-h-11 w-32 text-center font-mono"
          />
        </CompressionField>
      </CompressionGroup>

      <Separator />

      <CompressionGroup id="compression-logs-heading" title="日志">
        <CompressionField id="logsDir" label="日志目录">
          <Input
            id="logsDir"
            value={config.transcript.logsDir}
            onChange={(e) => updateTranscriptConfig('logsDir', e.target.value)}
            placeholder="./logs/compression"
            className="min-h-11 font-mono"
          />
        </CompressionField>
      </CompressionGroup>

      {/* 重置确认对话框 */}
      {showResetDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => { if (e.target === e.currentTarget) setShowResetDialog(false); }}
        >
          <div
            ref={resetDialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="确认重置配置"
            className="bg-slate-900 border border-slate-700 rounded-lg p-6 max-w-md w-full mx-4 shadow-xl"
          >
            <div className="flex items-start gap-3 mb-4">
              <AlertTriangle size={20} className="text-amber-500 shrink-0 mt-0.5" aria-hidden="true" />
              <div>
                <h2 className="text-lg font-semibold">确认重置配置</h2>
                <p className="text-sm text-muted-foreground mt-1">
                  将把所有压缩参数恢复为默认值，当前修改将丢失。
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                type="button"
                onClick={() => setShowResetDialog(false)}
                aria-label="取消重置"
                className="cursor-pointer min-h-[44px]"
              >
                取消
              </Button>
              <Button
                ref={confirmBtnRef}
                variant="destructive"
                type="button"
                onClick={confirmReset}
                aria-label="确认重置为默认配置"
                className="cursor-pointer min-h-[44px]"
              >
                确认重置
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CompressionConfigSection() {
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);

  const toggleExpanded = () => {
    const nextExpanded = !expanded;
    setExpanded(nextExpanded);
    if (nextExpanded) setHasOpened(true);
  };

  return (
    <section aria-label="压缩配置" className="overflow-hidden rounded-xl border border-slate-700/50 bg-slate-800/80 transition-colors duration-300 hover:border-slate-600/50">
      <button
        type="button"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-controls="compression-config-content"
        className="flex w-full cursor-pointer items-center justify-between px-5 py-4 transition-colors duration-200 hover:bg-white/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
      >
        <div className="flex items-center gap-3">
          <Sliders size={18} className="text-orange-400" aria-hidden="true" />
          <h3 className="text-base font-semibold text-slate-100">压缩配置</h3>
        </div>
        {expanded ? <ChevronUp size={18} className="text-slate-400" /> : <ChevronDown size={18} className="text-slate-400" />}
      </button>
      {hasOpened && (
        <div id="compression-config-content" hidden={!expanded} className="px-5 pb-5">
          <CompressionConfigEditor />
        </div>
      )}
    </section>
  );
}
