import { useState, useMemo } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { MessageSquare, Wrench, Bot } from "lucide-react";
import { SessionDetail, Message, StreamingSegment } from "../types";
import { getStatusConfig, safeJsonParse, prettyJson } from "../lib/utils-helpers";
import ConversationTimeline from "./conversation/ConversationTimeline";

// ============ Status icon mapping (lucide, not emoji) ============

const STATUS_ICON_MAP: Record<string, React.ReactNode> = {
  running: <span className="w-2 h-2 rounded-full bg-green-400" />,
  streaming: <span className="w-2 h-2 rounded-full bg-cyan-400" />,
  completed: <span className="w-2 h-2 rounded-full bg-blue-400" />,
  error: <span className="w-2 h-2 rounded-full bg-red-400" />,
  waiting: <span className="w-2 h-2 rounded-full bg-amber-400" />,
  idle: <span className="w-2 h-2 rounded-full bg-slate-400" />,
};

interface SessionDetailPanelProps {
  session: SessionDetail;
  messages?: Message[];
  streamingSegments?: StreamingSegment[];
  isStreaming?: boolean;
  loading?: boolean;
  error?: Error | string | null;
  onRetry?: () => void;
  liveStatus?: string | null;
}

export default function SessionDetailPanel({
  session,
  messages = session.messages,
  streamingSegments = [],
  isStreaming = false,
  loading = false,
  error = null,
  onRetry,
  liveStatus = null,
}: SessionDetailPanelProps) {
  const status = liveStatus || session.status;
  const cfg = getStatusConfig(status);

  // Build reasoning chain from messages
  const reasoningChain = useMemo(
    () => buildReasoningChain(messages, streamingSegments),
    [messages, streamingSegments],
  );

  return (
    <div className="flex flex-col md:flex-row h-full">
      {/* Left: Message History */}
      <section aria-label="会话消息记录" className="flex-1 min-h-0 flex flex-col md:border-r border-b md:border-b-0 border-border min-w-0">
        <div className="px-4 py-2 border-b border-border flex items-center gap-2">
          <span className={cfg.color} aria-hidden="true">
            {STATUS_ICON_MAP[status] || <span className="w-2 h-2 rounded-full bg-slate-400" />}
          </span>
          <span className="text-sm font-mono font-bold text-cyan-400">{session.session_id}</span>
          <Badge variant="outline" className={`text-xs ${cfg.color}`} aria-label={`状态: ${status}`}>
            {status}
          </Badge>
          <span className="text-xs text-muted-foreground ml-auto">{messages.length} 条消息</span>
        </div>

        <ConversationTimeline
          messages={messages}
          streamingSegments={streamingSegments}
          isStreaming={isStreaming}
          loading={loading}
          error={error}
          onRetry={onRetry}
          conversationId={session.session_id}
          ariaLabel={`${session.session_id} 的会话消息`}
          readonly
        />
      </section>

      {/* Right: Reasoning Chain Timeline */}
      <section aria-label="推理链路" className="w-full min-h-0 md:w-80 flex flex-col">
        <div className="px-4 py-2 border-b border-border">
          <span className="text-sm font-medium text-muted-foreground">推理链路</span>
        </div>

        <ScrollArea className="flex-1">
          <div className="px-4 py-3">
            {reasoningChain.length > 0 ? (
              <div className="relative pl-6" role="list" aria-label="推理步骤列表">
                <div className="absolute left-2.5 top-0 bottom-0 w-px bg-slate-700" aria-hidden="true" />
                {reasoningChain.map((step, i) => (
                  <ReasoningStep key={i} step={step} />
                ))}
              </div>
            ) : (
              <div className="text-center text-muted-foreground text-sm py-8" role="status" aria-label="暂无推理链路">暂无推理链路</div>
            )}
          </div>
        </ScrollArea>
      </section>
    </div>
  );
}

// ============ Reasoning Chain Builder ============

interface ReasoningStepData {
  type: "llm" | "tool_call" | "tool_result";
  content: string;
  toolName?: string;
  toolArgs?: string;
  toolResult?: string;
}

function buildReasoningChain(
  messages: Message[],
  streamingSegments: StreamingSegment[],
): ReasoningStepData[] {
  const chain: ReasoningStepData[] = [];

  messages.forEach((msg) => {
    const msgType = msg.type || msg.role;
    if (msgType === "assistant") {
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        // LLM decided to call tools
        chain.push({
          type: "llm",
          content: msg.content || "(决定调用工具)",
        });
        msg.tool_calls.forEach((tc) => {
          chain.push({
            type: "tool_call",
            content: tc.function.name,
            toolName: tc.function.name,
            toolArgs: tc.function.arguments,
          });
        });
      } else if (msg.content) {
        chain.push({
          type: "llm",
          content: (msg.content || "").slice(0, 100),
        });
      }
    } else if (msgType === "tool") {
      chain.push({
        type: "tool_result",
        content: (msg.content || "").slice(0, 100),
        toolResult: msg.content,
      });
    }
  });

  streamingSegments.forEach((segment) => {
    if (segment.type === "text" || segment.type === "reasoning") {
      if (segment.content) {
        chain.push({
          type: "llm",
          content: segment.content.slice(0, 100),
        });
      }
      return;
    }
    chain.push({
      type: "tool_call",
      content: segment.tool.name,
      toolName: segment.tool.name,
      toolArgs: segment.tool.args,
    });
    if (segment.tool.result !== undefined) {
      chain.push({
        type: "tool_result",
        content: segment.tool.result.slice(0, 100),
        toolName: segment.tool.name,
        toolResult: segment.tool.result,
      });
    }
  });

  return chain;
}

// ============ Reasoning Step ============

function ReasoningStep({ step }: { step: ReasoningStepData }) {
  const [expanded, setExpanded] = useState(false);

  const typeConfig = {
    llm: { color: "border-indigo-400", textColor: "text-indigo-400", dotColor: "bg-indigo-400", label: "LLM", icon: <Bot size={12} /> },
    tool_call: { color: "border-amber-400", textColor: "text-amber-400", dotColor: "bg-amber-400", label: "Tool Call", icon: <Wrench size={12} /> },
    tool_result: { color: "border-green-400", textColor: "text-green-400", dotColor: "bg-green-400", label: "Result", icon: <MessageSquare size={12} /> },
  };

  const cfg = typeConfig[step.type];

  return (
    <div className="relative pb-3" role="listitem">
      <div className={`absolute -left-3.5 w-3 h-3 rounded-full bg-slate-900 border-2 ${cfg.color}`} aria-hidden="true" />

      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${cfg.label}: ${step.content}`}
        className="ml-4 w-[calc(100%-1rem)] text-left cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 rounded min-h-[44px] py-1"
      >
        <div className="flex items-center gap-1.5">
          <span className={`inline-block w-2 h-2 rounded-full ${cfg.dotColor}`} aria-hidden="true" />
          <span className={`text-xs font-medium ${cfg.textColor}`}>
            {cfg.label}
          </span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 truncate">{step.content}</p>
      </button>

      {expanded && (
        <div className="ml-4 mt-1 bg-slate-900/60 rounded p-2 text-xs text-slate-400">
          {step.toolArgs && (
            <div role="region" aria-label="工具调用参数">
              <div className="text-amber-400 mb-0.5">参数:</div>
              <pre className="overflow-x-auto" aria-label={`${step.toolName || "工具"} 的调用参数`}>{prettyJson(safeJsonParse(step.toolArgs))}</pre>
            </div>
          )}
          {step.toolResult && (
            <div className="mt-1" role="region" aria-label="工具调用结果">
              <div className="text-green-400 mb-0.5">结果:</div>
              <pre className="overflow-x-auto max-h-24 overflow-y-auto" aria-label={`${step.toolName || "工具"} 的返回结果`}>{step.toolResult.slice(0, 300)}</pre>
            </div>
          )}
          {step.type === "llm" && (
            <div className="overflow-x-auto" role="region" aria-label="LLM 推理内容">{step.content}</div>
          )}
        </div>
      )}
    </div>
  );
}
