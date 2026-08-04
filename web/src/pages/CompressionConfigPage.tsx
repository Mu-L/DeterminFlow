import { useState, useEffect, useRef, useCallback } from "react";
import { Save, RefreshCw, Sliders, Zap, FileText, RotateCcw, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
    <div aria-label="压缩配置编辑器">
      <div className="space-y-4">
        {/* 错误提示 */}
        {error && (
          <div className="flex items-center gap-3 p-4 border border-red-500/20 bg-red-500/5 rounded-lg" role="alert" aria-live="polite">
            <AlertTriangle size={16} className="text-red-500 shrink-0" aria-hidden="true" />
            <span className="text-sm flex-1">{error}</span>
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={loadConfig}
              aria-label="重试加载配置"
              className="cursor-pointer min-h-[44px]"
            >
              <RefreshCw size={14} className="mr-1" aria-hidden="true" />
              重试
            </Button>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={resetConfig} type="button" aria-label="重置为默认配置" className="cursor-pointer min-h-[44px]">
            <RotateCcw size={16} className="mr-2" aria-hidden="true" />
            重置默认
          </Button>
          <Button onClick={saveConfig} disabled={saving} type="button" aria-label="保存压缩配置" className="cursor-pointer min-h-[44px]">
            {saving ? (
              <RefreshCw size={16} className="mr-2 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Save size={16} className="mr-2" aria-hidden="true" />
            )}
            保存配置
          </Button>
        </div>

        {/* 通用配置 */}
        <Card aria-label="通用配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Zap className="text-amber-500" aria-hidden="true" />
              通用配置
            </CardTitle>
            <CardDescription>
              压缩系统的基础设置
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="compression-enabled">启用压缩</Label>
                <p className="text-sm text-muted-foreground">
                  开启或关闭上下文压缩功能
                </p>
              </div>
              <Switch
                id="compression-enabled"
                checked={config.general.enabled}
                onCheckedChange={(checked) => updateGeneralConfig('enabled', checked)}
                aria-label="启用或禁用压缩功能"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="compactionThreshold">压缩触发阈值 ({(config.general.compactionThreshold * 100).toFixed(0)}%)</Label>
              <p className="text-sm text-muted-foreground">
                当上下文占用率超过此阈值时触发FullCompact压缩
              </p>
              <Slider
                id="compactionThreshold"
                value={[config.general.compactionThreshold * 100]}
                onValueChange={([value]) => updateGeneralConfig('compactionThreshold', value / 100)}
                max={95}
                min={50}
                step={5}
                className="w-full"
                aria-label="压缩触发阈值"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>50%</span>
                <span>95%</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* MicroCompact配置 */}
        <Card aria-label="MicroCompact配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-green-500" aria-hidden="true" />
              MicroCompact配置
            </CardTitle>
            <CardDescription>
              工具结果微压缩策略参数
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="maxToolResults">工具结果数量阈值</Label>
                <Input
                  id="maxToolResults"
                  type="number"
                  value={config.micro_compact.maxToolResults}
                  onChange={(e) => updateMicroConfig('maxToolResults', parseInt(e.target.value) || 0)}
                  min={5}
                  max={50}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  历史中累计的工具结果个数阈值
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="keepRecentToolResults">保留最近工具结果数</Label>
                <Input
                  id="keepRecentToolResults"
                  type="number"
                  value={config.micro_compact.keepRecentToolResults}
                  onChange={(e) => updateMicroConfig('keepRecentToolResults', parseInt(e.target.value) || 0)}
                  min={1}
                  max={20}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  保留最近的N个工具结果原文
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="toolResultTokenRatio">工具结果Token占比阈值 ({(config.micro_compact.toolResultTokenRatio * 100).toFixed(0)}%)</Label>
              <Slider
                id="toolResultTokenRatio"
                value={[config.micro_compact.toolResultTokenRatio * 100]}
                onValueChange={([value]) => updateMicroConfig('toolResultTokenRatio', value / 100)}
                max={80}
                min={10}
                step={5}
                className="w-full"
                aria-label="工具结果Token占比阈值"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>10%</span>
                <span>80%</span>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="placeholder">占位符文本</Label>
              <Input
                id="placeholder"
                value={config.micro_compact.placeholder}
                onChange={(e) => updateMicroConfig('placeholder', e.target.value)}
                placeholder="[Content compacted]"
                className="min-h-[44px]"
              />
              <p className="text-xs text-muted-foreground">
                替换压缩后工具结果的占位符文本
              </p>
            </div>
          </CardContent>
        </Card>

        {/* FullCompact配置 */}
        <Card aria-label="FullCompact配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-purple-500" aria-hidden="true" />
              FullCompact配置
            </CardTitle>
            <CardDescription>
              全量摘要压缩策略参数
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="keepRecentTokens">保留最近Token数</Label>
                <Input
                  id="keepRecentTokens"
                  type="number"
                  value={config.full_compact.keepRecentTokens}
                  onChange={(e) => updateFullConfig('keepRecentTokens', parseInt(e.target.value) || 0)}
                  min={10000}
                  max={200000}
                  step={1000}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  保留最近的Token数不被压缩
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="summaryTokenBudget">摘要生成最大Token数</Label>
                <Input
                  id="summaryTokenBudget"
                  type="number"
                  value={config.full_compact.summaryTokenBudget}
                  onChange={(e) => updateFullConfig('summaryTokenBudget', parseInt(e.target.value) || 0)}
                  min={1000}
                  max={100000}
                  step={500}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  摘要生成的max_tokens参数
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="fullCompactMaxRetryCount">最大重试次数</Label>
              <Input
                id="fullCompactMaxRetryCount"
                type="number"
                value={config.full_compact.maxRetryCount}
                onChange={(e) => updateFullConfig('maxRetryCount', parseInt(e.target.value))}
                min={0}
                max={5}
                className="min-h-[44px]"
              />
              <p className="text-xs text-muted-foreground">
                摘要生成失败时的最大重试次数
              </p>
            </div>
          </CardContent>
        </Card>

        {/* ReactiveCompact配置 */}
        <Card aria-label="ReactiveCompact配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-red-500" aria-hidden="true" />
              ReactiveCompact配置
            </CardTitle>
            <CardDescription>
              渐进式丢弃压缩策略参数
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <Label htmlFor="reactiveCompactMaxRetryCount">最大重试次数</Label>
              <Input
                id="reactiveCompactMaxRetryCount"
                type="number"
                value={config.reactive_compact.maxRetryCount}
                onChange={(e) => updateReactiveConfig('maxRetryCount', parseInt(e.target.value))}
                min={1}
                max={10}
                className="min-h-[44px]"
              />
              <p className="text-xs text-muted-foreground">
                渐进丢弃最大重试次数
              </p>
            </div>
          </CardContent>
        </Card>

        {/* 后处理配置 */}
        <Card aria-label="后处理配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-cyan-500" aria-hidden="true" />
              后处理配置
            </CardTitle>
            <CardDescription>
              压缩后处理参数
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="maxFilesToRead">最多重读文件数</Label>
                <Input
                  id="maxFilesToRead"
                  type="number"
                  value={config.post_compact.maxFilesToRead}
                  onChange={(e) => updatePostConfig('maxFilesToRead', parseInt(e.target.value) || 0)}
                  min={0}
                  max={10}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  压缩后最多重读的文件数
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="maxTokensPerFile">每文件重读Token上限</Label>
                <Input
                  id="maxTokensPerFile"
                  type="number"
                  value={config.post_compact.maxTokensPerFile}
                  onChange={(e) => updatePostConfig('maxTokensPerFile', parseInt(e.target.value) || 0)}
                  min={1000}
                  max={20000}
                  step={1000}
                  className="min-h-[44px]"
                />
                <p className="text-xs text-muted-foreground">
                  每个文件重读的Token上限
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 日志配置 */}
        <Card aria-label="日志配置">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-teal-500" aria-hidden="true" />
              日志配置
            </CardTitle>
            <CardDescription>
              压缩日志存储配置
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <Label htmlFor="logsDir">日志目录</Label>
              <Input
                id="logsDir"
                value={config.transcript.logsDir}
                onChange={(e) => updateTranscriptConfig('logsDir', e.target.value)}
                placeholder="./logs/compression"
                className="min-h-[44px]"
              />
              <p className="text-xs text-muted-foreground">
                JSONL日志文件存储目录
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

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
    <section aria-label="压缩配置" className="overflow-hidden rounded-xl border border-slate-700/50 bg-slate-800/80">
      <button
        type="button"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-controls="compression-config-content"
        className="flex w-full items-center justify-between px-5 py-4 transition-colors hover:bg-white/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
      >
        <div className="flex items-center gap-3">
          <Sliders size={18} className="text-orange-400" aria-hidden="true" />
          <h3 className="text-base font-semibold text-slate-100">压缩配置</h3>
        </div>
        {expanded ? <ChevronUp size={18} className="text-slate-400" /> : <ChevronDown size={18} className="text-slate-400" />}
      </button>
      {hasOpened && (
        <div id="compression-config-content" hidden={!expanded} className="border-t border-slate-700/50 px-5 py-5">
          <CompressionConfigEditor />
        </div>
      )}
    </section>
  );
}
