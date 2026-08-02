import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, ChevronRight } from "lucide-react";
import { safeJsonParse, prettyJson } from "../lib/utils-helpers";

interface MessageItemProps {
  message: {
    id?: string;
    type?: string;
    role?: string;
    content?: string;
    tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }>;
    tool_call_id?: string;
    name?: string;
  };
  index: number;
}

export default function MessageItem({ message, index }: MessageItemProps) {
  const [expanded, setExpanded] = useState(false);

  const roleColors: Record<string, string> = {
    system: "text-purple-500",
    user: "text-green-500",
    assistant: "text-indigo-500",
    tool: "text-amber-500",
  };

  const roleLabels: Record<string, string> = {
    system: "System",
    user: "User",
    assistant: "Assistant",
    tool: "Tool",
  };

  const msgRole = message.type || message.role || "";
  const color = roleColors[msgRole] || "text-slate-400";
  const label = roleLabels[msgRole] || msgRole;

  return (
    <div className="bg-slate-800/50 border border-slate-700/50 rounded-lg px-2.5 py-2">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${label} 消息 #${index}${expanded ? "，收起详情" : "，展开详情"}`}
        className="flex items-center gap-2 w-full text-left cursor-pointer min-h-[44px] transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded"
      >
        <span className="text-xs text-muted-foreground font-mono">#{index}</span>
        <span className={`text-xs font-medium ${color}`}>{label}</span>
        {message.name && (
          <span className="text-xs text-muted-foreground">({message.name})</span>
        )}
        {message.tool_calls && message.tool_calls.length > 0 && (
          <Badge variant="outline" className="text-xs text-amber-500 border-amber-500/30">
            {message.tool_calls.length} tool calls
          </Badge>
        )}
        {message.tool_call_id && (
          <Badge variant="outline" className="text-xs text-cyan-500 border-cyan-500/30">
            result
          </Badge>
        )}
        <span className="text-xs text-muted-foreground ml-auto" aria-hidden="true">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2" role="list" aria-label="消息详情">
          {message.content && (
            <div className="text-xs text-slate-300 bg-slate-900/60 rounded p-2 max-h-48 overflow-y-auto">
              <div className="text-xs text-muted-foreground mb-1">Content:</div>
              <div className="whitespace-pre-wrap break-words">{message.content}</div>
            </div>
          )}
          {message.tool_calls && message.tool_calls.map((tc, i) => (
            <div key={i} className="bg-slate-900/60 rounded p-2" role="listitem">
              <div className="text-xs font-mono text-amber-500 mb-1">{tc.function.name}</div>
              <div className="text-xs text-muted-foreground mb-0.5">Arguments:</div>
              <pre className="text-xs text-slate-400 overflow-x-auto">
                {prettyJson(safeJsonParse(tc.function.arguments))}
              </pre>
            </div>
          ))}
          {message.tool_call_id && (
            <div className="text-xs text-muted-foreground">
              Tool Call ID: <span className="font-mono text-cyan-500">{message.tool_call_id}</span>
            </div>
          )}
        </div>
      )}

      {!expanded && message.content && (
        <p className="text-xs text-muted-foreground mt-1 truncate" title={message.content}>
          {message.content.slice(0, 80)}
        </p>
      )}
    </div>
  );
}
