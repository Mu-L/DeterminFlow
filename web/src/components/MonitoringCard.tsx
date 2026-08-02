import { memo, useMemo } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Zap, Cpu, Database, Brain, BarChart3, Layers, Eye, Code2,
  ChevronRight, ChevronLeft
} from "lucide-react";
import { TokenUsage } from "../types";

interface Props {
  tokenUsage: TokenUsage | null;
  collapsed?: boolean;
  onToggle?: () => void;
}

function EmptyCollapsed({ onToggle }: { onToggle?: () => void }) {
  return (
    <div
      className="h-full flex flex-col items-center gap-1 py-2 cursor-pointer hover:bg-white/[0.03] transition-colors select-none rounded-r-lg border border-white/[0.04] bg-slate-950/60"
      onClick={onToggle}
      role="button"
      tabIndex={0}
      aria-label="展开 Token 监控面板"
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle?.(); } }}
    >
      <ChevronRight size={12} className="text-slate-600 shrink-0" aria-hidden="true" />
      <div className="flex-1 w-1.5 bg-white/[0.04] rounded-full min-h-[20px]" aria-hidden="true" />
      <span className="text-xs text-slate-700 font-mono">-</span>
    </div>
  );
}

function EmptyExpanded({ onToggle }: { onToggle?: () => void }) {
  return (
    <div className="h-full flex flex-col rounded-xl border border-white/[0.06] bg-slate-900 select-none">
      <div className="shrink-0 flex items-center gap-1.5 px-3 py-2.5 border-b border-white/[0.05]">
        <BarChart3 size={14} className="text-slate-600" aria-hidden="true" />
        <span className="text-xs font-semibold text-slate-500 tracking-wide">Token 监控</span>
        <button
          onClick={onToggle}
          className="ml-auto p-0.5 rounded hover:bg-white/[0.06] transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="折叠 Token 监控面板"
        >
          <ChevronLeft size={12} className="text-slate-600" aria-hidden="true" />
        </button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center px-4 text-center gap-2" role="status" aria-label="暂无 Token 监控数据">
        <BarChart3 size={24} className="text-slate-700" aria-hidden="true" />
        <span className="text-xs text-slate-600 leading-relaxed">
          暂无数据<br />发送消息后开始统计
        </span>
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtPct(numerator: number, denominator: number): string {
  if (!denominator) return "0.0%";
  return `${Math.min((numerator / denominator) * 100, 100).toFixed(1)}%`;
}

function barWidth(numerator: number, denominator: number): string {
  if (!denominator) return "0%";
  return `${Math.min((numerator / denominator) * 100, 100).toFixed(1)}%`;
}

function MonitoringCard({ tokenUsage, collapsed, onToggle }: Props) {
  const cacheHitRate = useMemo(() => {
    if (!tokenUsage) return "0.0%";
    const { prompt_tokens, cached_tokens } = tokenUsage.api;
    if (!prompt_tokens) return "0.0%";
    return fmtPct(cached_tokens, prompt_tokens);
  }, [tokenUsage]);

  // 空状态：尚未有 token 数据
  if (!tokenUsage) {
    if (collapsed) return <EmptyCollapsed onToggle={onToggle} />;
    return <EmptyExpanded onToggle={onToggle} />;
  }

  const { api, estimated, max_context_tokens, model_id, llm_call_count } = tokenUsage;
  const estOther = Math.max(0, estimated.total_tokens - estimated.system_prompt_tokens - estimated.tool_result_tokens);
  const ctxPct = fmtPct(estimated.total_tokens, max_context_tokens);
  const ctxBarPct = Math.min((estimated.total_tokens / max_context_tokens) * 100, 100);
  // 占比 > 80% 变红，> 60% 变黄
  const ctxBgClass = ctxBarPct > 80 ? "bg-red-500" : ctxBarPct > 60 ? "bg-amber-500" : "bg-green-500";
  const ctxTextClass = ctxBarPct > 80 ? "text-red-500" : ctxBarPct > 60 ? "text-amber-500" : "text-green-500";

  // ========== 折叠态：竖向窄条 ==========
  if (collapsed) {
    return (
      <div
        className="h-full flex flex-col items-center gap-1 py-2 cursor-pointer hover:bg-white/[0.03] transition-colors select-none rounded-r-lg border border-white/[0.04] bg-slate-950/60"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        aria-label={`展开 Token 监控面板，当前上下文占用 ${ctxPct}，已调用 ${llm_call_count} 次`}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle?.(); } }}
      >
        <ChevronRight size={12} className="text-slate-500 shrink-0" aria-hidden="true" />
        <span className="text-xs text-slate-600 font-mono tabular-nums">
          {llm_call_count}
        </span>
        {/* 竖向占比条 */}
        <div className="flex-1 w-1.5 bg-white/[0.04] rounded-full overflow-hidden relative min-h-[20px]" aria-hidden="true">
          <div
            className={`absolute bottom-0 left-0 w-full rounded-full transition-all duration-500 ${ctxBgClass}`}
            style={{ height: `${ctxBarPct}%` }}
          />
        </div>
        <span className="text-xs text-slate-600 font-mono tabular-nums">
          {ctxPct}
        </span>
      </div>
    );
  }

  // ========== 展开态：完整竖向面板 ==========
  return (
    <div className="h-full flex flex-col rounded-xl border border-white/[0.06] bg-slate-900 select-none" role="region" aria-label="Token 监控面板">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-1.5 px-3 py-2.5 border-b border-white/[0.05]">
        <BarChart3 size={14} className="text-indigo-500" aria-hidden="true" />
        <span className="text-xs font-semibold text-slate-300 tracking-wide">
          Token 监控
        </span>
        <button
          onClick={onToggle}
          className="ml-auto p-0.5 rounded hover:bg-white/[0.06] transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="折叠 Token 监控面板"
        >
          <ChevronLeft size={12} className="text-slate-500" aria-hidden="true" />
        </button>
      </div>

      {/* Scrollable Body */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4 text-xs">

        {/* --- 上下文概览 --- */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-500">上下文占用</span>
            <span className={`font-mono tabular-nums ${ctxTextClass}`}>
              {formatTokens(estimated.total_tokens)} / {formatTokens(max_context_tokens)}
            </span>
          </div>
          <div className="h-2.5 rounded-full bg-white/[0.05] overflow-hidden" role="progressbar" aria-valuenow={ctxBarPct} aria-valuemin={0} aria-valuemax={100} aria-label={`上下文占用 ${ctxPct}`}>
            <div
              className={`h-full rounded-full transition-all duration-500 ease-out ${ctxBgClass}`}
              style={{ width: `${ctxBarPct}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-600">
            <span>{ctxPct} 已用</span>
            <span>#{llm_call_count} 次调用</span>
          </div>
        </div>

        {/* --- API 原始数据 --- */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 text-xs text-slate-500 font-medium">
            <Zap size={14} className="text-indigo-500/70" aria-hidden="true" />
            API 返回
          </div>
          <div className="space-y-1" role="list" aria-label="API Token 统计">
            <StatRow icon={Layers} label="Prompt" value={formatTokens(api.prompt_tokens)} colorClass="text-indigo-500" />
            <StatRow icon={Eye} label="Completion" value={formatTokens(api.completion_tokens)} colorClass="text-purple-500" />
            <StatRow icon={Cpu} label="Total" value={formatTokens(api.total_tokens)} colorClass="text-cyan-500" />
            <StatRow icon={Database} label="Cached" value={formatTokens(api.cached_tokens)} colorClass="text-green-500" />
            <StatRow icon={Brain} label="Reasoning" value={formatTokens(api.reasoning_tokens)} colorClass="text-amber-500" />
          </div>
        </div>

        {/* --- 估算 Token 占比 --- */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 text-xs text-slate-500 font-medium">
            <Database size={14} className="text-purple-500/70" aria-hidden="true" />
            估算占比
          </div>
          <EstimateRow
            label="系统提示词"
            value={estimated.system_prompt_tokens}
            pct={fmtPct(estimated.system_prompt_tokens, max_context_tokens)}
            width={barWidth(estimated.system_prompt_tokens, max_context_tokens)}
            bgClass="bg-indigo-500"
          />
          <EstimateRow
            label="工具结果"
            value={estimated.tool_result_tokens}
            pct={fmtPct(estimated.tool_result_tokens, max_context_tokens)}
            width={barWidth(estimated.tool_result_tokens, max_context_tokens)}
            bgClass="bg-amber-500"
          />
          <EstimateRow
            label="其他消息"
            value={estOther}
            pct={fmtPct(estOther, max_context_tokens)}
            width={barWidth(estOther, max_context_tokens)}
            bgClass="bg-purple-500"
          />
        </div>

        {/* --- 命中率 --- */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 text-xs text-slate-500 font-medium">
            <Code2 size={14} className="text-emerald-400/70" aria-hidden="true" />
            缓存命中率
          </div>
          <div className="text-center text-lg font-mono font-bold text-emerald-400" role="status" aria-label={`缓存命中率 ${cacheHitRate}`}>
            {cacheHitRate}
          </div>
        </div>

        {/* --- 模型信息 --- */}
        <div className="pt-2 border-t border-white/[0.04] space-y-1">
          <div className="text-xs text-slate-600 truncate" title={model_id}>
            {model_id || "Unknown"}
          </div>
          <div className="text-xs text-slate-600 tabular-nums">
            上限 {formatTokens(max_context_tokens)}
          </div>
        </div>

      </div>
    </div>
  );
}

export default memo(MonitoringCard);

/* ---- 子组件 (竖向单列布局) ---- */

function StatRow({
  icon: Icon,
  label,
  value,
  colorClass,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  colorClass: string;
}) {
  return (
    <div className="flex items-center gap-1.5 py-0.5" role="listitem">
      <Icon size={14} className={colorClass} aria-hidden="true" />
      <span className="text-xs text-slate-500">{label}</span>
      <span className="ml-auto text-xs font-mono text-slate-300 tabular-nums">
        {value}
      </span>
    </div>
  );
}

function EstimateRow({
  label,
  value,
  pct,
  width,
  bgClass,
}: {
  label: string;
  value: number;
  pct: string;
  width: string;
  bgClass: string;
}) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-500">{label}</span>
        <span className="text-slate-500 font-mono tabular-nums">
          {formatTokens(value)} ({pct})
        </span>
      </div>
      <div className="h-1 rounded-full bg-white/[0.04] overflow-hidden" role="progressbar" aria-valuenow={parseFloat(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={`${label} 占比 ${pct}`}>
        <div
          className={`h-full rounded-full transition-all duration-500 ease-out ${bgClass}`}
          style={{ width }}
        />
      </div>
    </div>
  );
}
