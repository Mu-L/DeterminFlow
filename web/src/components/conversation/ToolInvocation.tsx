import {
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleX,
  Loader2,
  Pencil,
} from "lucide-react";
import type { ToolInvocationModel, ToolInvocationStatus } from "./conversationTypes";
import TechnicalDisclosure from "./TechnicalDisclosure";

interface StatusPresentation {
  label: string;
  badgeClass: string;
  borderClass: string;
  icon: React.ReactNode;
}

const STATUS_PRESENTATION: Record<ToolInvocationStatus, StatusPresentation> = {
  pending: {
    label: "等待结果",
    badgeClass: "bg-slate-500/15 text-slate-400",
    borderClass: "border-slate-700/50",
    icon: <CircleDashed size={16} className="text-slate-400" aria-hidden="true" />,
  },
  building: {
    label: "生成参数",
    badgeClass: "bg-amber-500/15 text-amber-300",
    borderClass: "border-amber-500/20",
    icon: <Pencil size={16} className="animate-pulse text-amber-400 motion-reduce:animate-none" aria-hidden="true" />,
  },
  running: {
    label: "执行中",
    badgeClass: "bg-amber-500/15 text-amber-300",
    borderClass: "border-amber-500/25",
    icon: <Loader2 size={16} className="animate-spin text-amber-400 motion-reduce:animate-none" aria-hidden="true" />,
  },
  succeeded: {
    label: "已完成",
    badgeClass: "bg-green-500/15 text-green-300",
    borderClass: "border-green-500/20",
    icon: <CheckCircle2 size={16} className="text-green-400" aria-hidden="true" />,
  },
  failed: {
    label: "执行失败",
    badgeClass: "bg-red-500/15 text-red-300",
    borderClass: "border-red-500/25",
    icon: <CircleX size={16} className="text-red-400" aria-hidden="true" />,
  },
  cancelled: {
    label: "已取消",
    badgeClass: "bg-slate-500/15 text-slate-300",
    borderClass: "border-slate-500/25",
    icon: <Ban size={16} className="text-slate-400" aria-hidden="true" />,
  },
};

export interface ToolInvocationProps {
  invocation: ToolInvocationModel;
  className?: string;
}

export default function ToolInvocation({ invocation, className = "" }: ToolInvocationProps) {
  const presentation = STATUS_PRESENTATION[invocation.status];
  const hasArguments = !!invocation.arguments && invocation.arguments.trim() !== "{}";
  const hasResult = invocation.result !== undefined;

  return (
    <article
      aria-label={`工具调用 ${invocation.name}，${presentation.label}`}
      className={`ml-10 rounded-lg border bg-slate-800/50 px-3 py-2 ${presentation.borderClass} ${className}`}
    >
      <header className="flex min-h-8 items-center gap-2">
        {presentation.icon}
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-amber-300" title={invocation.name}>
          {invocation.name}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${presentation.badgeClass}`}
          role="status"
          aria-label={presentation.label}
        >
          {presentation.label}
        </span>
      </header>

      {hasArguments && (
        <TechnicalDisclosure label="参数" value={invocation.arguments} />
      )}
      {invocation.error && (
        <TechnicalDisclosure label="错误" value={invocation.error} tone="error" />
      )}
      {hasResult && (
        <TechnicalDisclosure
          label={invocation.status === "failed" ? "失败结果" : "结果"}
          value={invocation.result || ""}
          tone={invocation.status === "failed" ? "error" : "neutral"}
        />
      )}
    </article>
  );
}
