import { memo } from "react";
import { Loader2, CheckCircle2, Pencil } from "lucide-react";
import CodingToolCard from "./CodingToolCard";
import { isCodingTool } from "../lib/codingTools";

interface ToolCallCardProps {
  name: string;
  args: string;
  result?: string;
  status: "building" | "running" | "completed";
}

function ToolCallCard({ name, args, result, status }: ToolCallCardProps) {
  // 编码工具使用 Rich 展示组件
  if (isCodingTool(name)) {
    return <CodingToolCard name={name} args={args} result={result} status={status} />;
  }

  // 尝试格式化 args JSON 用于展示
  let formattedArgs: string = args;
  if (args) {
    try {
      formattedArgs = JSON.stringify(JSON.parse(args), null, 2);
    } catch {
      // args 不完整时（流式构建中），直接显示原始字符串
      formattedArgs = args;
    }
  }

  const statusLabel = status === "building" ? "生成参数..." : status === "running" ? "执行中..." : "完成";
  const statusBadgeBg = status === "completed"
    ? "bg-green-500/20 text-green-400"
    : "bg-amber-500/20 text-amber-400";

  return (
    <div className="mb-2 ml-10">
      <div
        role="article"
        aria-label={`工具调用 ${name} - ${statusLabel}`}
        className={`px-3 py-2 bg-slate-800/50 border border-slate-700/40 rounded-lg transition-colors duration-200 ${status === "running" ? "animate-pulse-slow motion-reduce:animate-none" : ""}`}
      >
        <div className="flex items-center gap-2">
          {status === "running" ? (
            <Loader2 className="w-4 h-4 animate-spin motion-reduce:animate-none text-amber-400" aria-hidden="true" />
          ) : status === "building" ? (
            <Pencil className="w-4 h-4 text-amber-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-green-400" aria-hidden="true" />
          )}
          <span className="text-sm font-medium text-amber-400">{name}</span>
          <span className={`text-xs px-2 py-0.5 rounded-full ${statusBadgeBg}`} role="status" aria-label={statusLabel}>
            {statusLabel}
          </span>
        </div>
        {formattedArgs && formattedArgs !== "{}" && (
          <div className="mt-1.5 text-xs text-muted-foreground">
            <pre className="bg-slate-900/60 rounded p-1.5 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap" role="region" aria-label={`${name} 参数`}>
              {formattedArgs}
            </pre>
          </div>
        )}
        {result && status === "completed" && (
          <div className="mt-1.5 text-xs text-slate-400">
            <div className="bg-slate-900/60 rounded p-1.5 overflow-x-auto max-h-32 overflow-y-auto" role="region" aria-label={`${name} 结果`}>
              {result.length > 300 ? result.slice(0, 300) + "..." : result}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ToolCallCard);
