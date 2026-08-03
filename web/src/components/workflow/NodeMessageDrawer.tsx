/**
 * NodeMessageDrawer - 节点消息抽屉
 *
 * 从页面右侧弹出，展示工作流节点的执行详情：
 *   - Agent 节点：消息流 + 推理链路（支持实时流式 token）
 *   - Script 节点：stdout / stderr / 提取的变量
 *   - 复用项目中"图谱"页面的 SessionDetailPanel 布局模式。
 */
import { useState, useEffect, useRef, useMemo } from "react";
import { X, Loader, Bot, Wrench, MessageSquare, GripVertical, Play, Terminal, CheckCircle, XCircle, ChevronDown, ChevronRight } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ConversationTimeline } from "../conversation";
import type { NodeExecutionInfo, NodeMessageResponse, Message } from "../../types";
import type { StreamingSegment } from "../../hooks/useNodeStreaming";
import NodeFailureRuntimePanel from "./NodeFailureRuntimePanel";

interface NodeMessageDrawerProps {
  workflowId: string;
  taskId: string;
  nodeId: string;
  messages: NodeMessageResponse | null;
  loading: boolean;
  nodeType?: string;
  nodeState?: NodeExecutionInfo;
  onClose: () => void;
  /** 流式片段（来自 useNodeStreaming hook） */
  streamingSegments?: StreamingSegment[];
  /** 是否正在流式输出 */
  isStreaming?: boolean;
  /** canonical 会话消息（snapshot/revision 协议） */
  conversationMessages?: Message[];
  conversationId?: string | null;
  connected?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export default function NodeMessageDrawer({
  workflowId,
  taskId,
  nodeId,
  messages,
  loading,
  nodeType,
  nodeState,
  onClose,
  streamingSegments = [],
  isStreaming = false,
  conversationMessages,
  conversationId,
  connected = true,
  error = null,
  onRetry,
}: NodeMessageDrawerProps) {
  const [width, setWidth] = useState(560);
  const [isResizing, setIsResizing] = useState(false);
  const reasoningEndRef = useRef<HTMLDivElement>(null);
  const displayMessages = conversationMessages ?? messages?.messages ?? [];
  const msgCount = displayMessages.length + streamingSegments.length;

  // 推理链路变化时也滚动
  useEffect(() => {
    reasoningEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgCount, isStreaming]);

  const isRunning = nodeState?.status === "running" || messages?.node_status === "running";
  const isScript = nodeType === "script";
  const showStreaming = isStreaming;

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;
      const newWidth = window.innerWidth - e.clientX;
      setWidth(Math.max(360, Math.min(900, newWidth)));
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    }

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

  const statusLabel = (s: string) => { const m: Record<string, string> = { pending: "待执行", running: "执行中", retry_waiting: "等待重试", waiting_approval: "待审批", success: "成功", completed: "已完成", failed: "失败", skipped: "已跳过" }; return m[s] || s; };
  const statusColor = (s: string) => { const m: Record<string, string> = { success: "text-green-400", completed: "text-green-400", running: "text-blue-400", retry_waiting: "text-amber-400", waiting_approval: "text-amber-400", failed: "text-red-400", pending: "text-gray-400", skipped: "text-slate-400" }; return m[s] || "text-gray-400"; };

  return (
    <div
      className="h-full flex flex-col bg-slate-900 border-l border-indigo-500/10 shrink-0 overflow-hidden relative"
      style={{ width: `${width}px`, minWidth: "360px", maxWidth: "900px" }}
    >
      {/* Resize Handle */}
      <div
        onMouseDown={handleMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-label="拖拽调整抽屉宽度"
        className={`absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-indigo-500/50 transition-colors z-10 group ${
          isResizing ? "bg-indigo-500/60" : ""
        }`}
      >
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
          <GripVertical size={16} className="text-indigo-500" aria-hidden="true" />
        </div>
      </div>

      {/* Header */}
      <div className="h-10 px-3 flex items-center justify-between border-b border-indigo-500/10 shrink-0">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">节点</span>
          <span className="text-slate-200 font-mono">{nodeId}</span>
          {isScript && <Terminal size={12} className="text-emerald-500" />}
          {nodeState && (
            <span className={`text-xs ${statusColor(nodeState.status)}`}>
              · {statusLabel(nodeState.status)}
            </span>
          )}
          {isRunning && !nodeState && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/20">
              <Play size={9} className="text-blue-500 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
              <span className="text-xs text-blue-500">执行中</span>
            </div>
          )}
          {showStreaming && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
              <span className="text-xs text-emerald-500">流式输出中</span>
            </div>
          )}
          {conversationId && !connected && (
            <span className="text-amber-400 text-xs">· 正在重连</span>
          )}
          {messages && (
            <>
              <span className="text-slate-400 text-xs ml-1">
                {displayMessages.length} 条消息
              </span>
              {messages.agent_type && (
                <span className="text-indigo-500 text-xs">· {messages.agent_type}</span>
              )}
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭节点消息"
          className="p-1 rounded hover:bg-indigo-500/10 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {nodeState && (
        <NodeFailureRuntimePanel
          workflowId={workflowId}
          taskId={taskId}
          nodeId={nodeId}
          nodeState={nodeState}
        />
      )}

      {/* Content */}
      {isScript ? (
        <ScriptOutputView nodeState={nodeState} />
      ) : (
        <div className="flex-1 flex min-h-0">
          {/* Left: Message History */}
          <div className="flex-1 flex flex-col border-r border-indigo-500/10 min-w-0">
            <div className="px-3 py-1.5 border-b border-indigo-500/10">
              <span className="text-xs text-slate-400">消息流</span>
            </div>
            <ConversationTimeline
              messages={displayMessages}
              streamingSegments={streamingSegments}
              isStreaming={isStreaming}
              loading={loading}
              error={error}
              onRetry={onRetry}
              conversationId={conversationId}
              ariaLabel={`节点 ${nodeId} 消息流`}
              readonly={true}
              contentClassName="px-3 py-2"
              emptyState={(
                <div className="flex min-h-48 flex-col items-center justify-center px-4 py-12 text-slate-500">
                  {isRunning || showStreaming ? (
                    <>
                      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-blue-500/20 bg-blue-500/10">
                        <Loader size={20} className="animate-spin text-blue-500 motion-reduce:animate-none" aria-hidden="true" />
                      </div>
                      <p className="mb-1 text-sm font-medium text-slate-400">节点正在执行中</p>
                      <p className="flex items-center gap-1 text-xs text-slate-500">
                        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500 motion-reduce:animate-none" aria-hidden="true" />
                        消息将实时更新...
                      </p>
                    </>
                  ) : (
                    <>
                      <MessageSquare size={32} className="mb-2 opacity-50" aria-hidden="true" />
                      <p className="text-sm">暂无消息</p>
                      <p className="mt-1 text-xs">
                        {messages?.node_status === "pending"
                          ? "节点尚未开始执行"
                          : "节点执行完成，但未生成消息记录"}
                      </p>
                    </>
                  )}
                </div>
              )}
            />
          </div>

          {/* Right: Reasoning Chain Timeline */}
          <div className="w-60 flex flex-col">
            <div className="px-3 py-1.5 border-b border-indigo-500/10">
              <span className="text-xs text-slate-400">推理链路</span>
            </div>
            <ScrollArea className="flex-1">
              <div className="px-3 py-2">
                <ReasoningChainTimeline messages={displayMessages} streamingSegments={streamingSegments} />
                <div ref={reasoningEndRef} />
              </div>
            </ScrollArea>
          </div>
        </div>
      )}
    </div>
  );
}


// ============ Script Output View ============

function ScriptOutputView({ nodeState }: { nodeState?: NodeExecutionInfo }) {
  const [showStderr, setShowStderr] = useState(false);
  const [showOutputs, setShowOutputs] = useState(true);

  if (!nodeState) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-slate-500">
        <Terminal size={32} className="mb-2 opacity-50" aria-hidden="true" />
        <p className="text-sm">等待脚本执行...</p>
      </div>
    );
  }

  const status = nodeState.status;
  const isSuccess = status === "completed";
  const isFailed = status === "failed";
  const hasOutputs = nodeState.outputs && Object.keys(nodeState.outputs).length > 0;
  const hasStderr = nodeState.stderr && nodeState.stderr.trim().length > 0;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Status Banner */}
      <div className={`px-3 py-2 border-b border-indigo-500/10 ${
        isSuccess ? "bg-emerald-500/5" : isFailed ? "bg-red-500/5" : "bg-blue-500/5"
      }`}>
        <div className="flex items-center gap-2 text-xs">
          {isSuccess ? <CheckCircle size={14} className="text-emerald-500" aria-hidden="true" /> :
           isFailed ? <XCircle size={14} className="text-red-500" aria-hidden="true" /> :
           <Loader size={14} className="text-blue-500 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
          <span className={`font-medium ${
            isSuccess ? "text-emerald-500" : isFailed ? "text-red-500" : "text-blue-500"
          }`}>
            {isSuccess ? "执行成功" : isFailed ? `执行失败${nodeState.error ? `: ${nodeState.error}` : ""}` : "执行中..."}
          </span>
        </div>
        {nodeState.summary && (
          <p className="text-xs text-slate-400 mt-1">{nodeState.summary}</p>
        )}
      </div>

      {/* Extracted Outputs */}
      {hasOutputs && (
        <div className="border-b border-indigo-500/10">
          <button
            type="button"
            onClick={() => setShowOutputs(!showOutputs)}
            aria-expanded={showOutputs}
            aria-label="展开/折叠输出变量"
            className="w-full px-3 py-1.5 flex items-center gap-1 text-xs text-slate-200 hover:bg-indigo-500/5 transition-colors cursor-pointer"
          >
            {showOutputs ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
            <span>输出变量</span>
            <span className="text-slate-500 ml-1">({Object.keys(nodeState.outputs!).length})</span>
          </button>
          {showOutputs && (
            <div className="px-3 pb-2">
              <div className="rounded border border-indigo-500/10 overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-950">
                      <th scope="col" className="text-left px-2 py-1 text-slate-400 font-medium w-1/3">变量名</th>
                      <th scope="col" className="text-left px-2 py-1 text-slate-400 font-medium">值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(nodeState.outputs!).map(([k, v]) => (
                      <tr key={k} className="border-t border-indigo-500/5">
                        <td className="px-2 py-1 text-indigo-500 font-mono">{k}</td>
                        <td className="px-2 py-1 text-slate-200 font-mono break-all">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Stdout */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="px-3 py-1.5 border-b border-indigo-500/10 flex items-center gap-2">
          <span className="text-xs text-emerald-500 uppercase tracking-wider font-medium">stdout</span>
        </div>
        <ScrollArea className="flex-1">
          <pre className="px-3 py-2 text-xs text-slate-200 font-mono whitespace-pre-wrap break-all leading-relaxed">
            {nodeState.stdout || "(无输出)"}
          </pre>
        </ScrollArea>
      </div>

      {/* Stderr (collapsible) */}
      <div className="border-t border-indigo-500/10">
        <button
          type="button"
          onClick={() => setShowStderr(!showStderr)}
          aria-expanded={showStderr}
          aria-label="展开/折叠 stderr 输出"
          className="w-full px-3 py-1.5 flex items-center gap-1 text-xs hover:bg-indigo-500/5 transition-colors cursor-pointer"
        >
          {showStderr ? <ChevronDown size={12} className="text-red-500" /> : <ChevronRight size={12} className="text-red-500" />}
          <span className="text-red-500 uppercase tracking-wider font-medium">stderr</span>
          <span className="text-slate-500">{hasStderr ? "" : "(空)"}</span>
        </button>
        {showStderr && (
          <div className="max-h-40">
            <ScrollArea className="max-h-40">
              <pre className="px-3 py-2 text-xs text-red-500/80 font-mono whitespace-pre-wrap break-all leading-relaxed bg-red-500/5">
                {nodeState.stderr || "(无输出)"}
              </pre>
            </ScrollArea>
          </div>
        )}
      </div>
    </div>
  );
}


// ============ Reasoning Chain Timeline ============

interface ReasoningStep {
  type: "llm" | "tool_call" | "tool_result";
  content: string;
  toolName?: string;
  toolArgs?: string;
  toolResult?: string;
}

function buildReasoningChain(messages: Message[]): ReasoningStep[] {
  const chain: ReasoningStep[] = [];
  messages.forEach((msg) => {
    const msgType = msg.type || msg.role;
    if (msgType === "assistant") {
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        chain.push({
          type: "llm",
          content: msg.content || "(决定调用工具)",
        });
        msg.tool_calls.forEach((tc: { function: { name: string; arguments: string } }) => {
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
  return chain;
}

function ReasoningChainTimeline({ messages, streamingSegments = [] }: { messages: Message[]; streamingSegments?: StreamingSegment[] }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  // 合并 base 消息 + 流式片段的推理链路
  const chain = useMemo(() => {
    const baseChain = buildReasoningChain(messages);
    if (streamingSegments.length === 0) return baseChain;

    const streamingChain: ReasoningStep[] = [];
    for (const seg of streamingSegments) {
      if (seg.type === "text") {
        streamingChain.push({ type: "llm", content: seg.content.slice(0, 100) || "(正在生成...)" });
      } else if (seg.type === "reasoning") {
        streamingChain.push({ type: "llm", content: `[思考中] ${seg.content.slice(0, 80)}...` });
      } else if (seg.type === "tool") {
        if (seg.tool.status === "building") {
          streamingChain.push({
            type: "tool_call",
            content: seg.tool.name || "准备调用工具...",
            toolName: seg.tool.name,
            toolArgs: seg.tool.args,
          });
        } else if (seg.tool.status === "running") {
          streamingChain.push({
            type: "tool_call",
            content: seg.tool.name,
            toolName: seg.tool.name,
            toolArgs: seg.tool.args,
          });
        } else {
          streamingChain.push({
            type: "tool_result",
            content: (seg.tool.result || seg.tool.status).slice(0, 100),
            toolResult: seg.tool.result,
          });
        }
      }
    }

    return [...baseChain, ...streamingChain];
  }, [messages, streamingSegments]);

  if (chain.length === 0) {
    return <div className="text-xs text-slate-500 py-4 text-center">暂无推理链路</div>;
  }

  const typeConfig: Record<string, { dotColor: string; label: string; icon: React.ReactNode }> = {
    llm: { dotColor: "bg-indigo-500", label: "LLM", icon: <Bot size={9} /> },
    tool_call: { dotColor: "bg-amber-500", label: "Tool Call", icon: <Wrench size={9} /> },
    tool_result: { dotColor: "bg-green-500", label: "Result", icon: <MessageSquare size={9} /> },
  };

  return (
    <div className="relative pl-5">
      <div className="absolute left-2 top-0 bottom-0 w-px bg-slate-700" />
      {chain.map((step, i) => {
        const cfg = typeConfig[step.type];
        const isExpanded = expandedIdx === i;
        return (
          <div key={i} className="relative pb-2.5">
            <div className={`absolute -left-3 w-2.5 h-2.5 rounded-full bg-slate-900 border ${cfg.dotColor.replace('bg-', 'border-')} ${cfg.dotColor}`} />
            <button
              type="button"
              onClick={() => setExpandedIdx(isExpanded ? null : i)}
              aria-expanded={isExpanded}
              aria-label={`${cfg.label}: ${step.toolName || step.content.slice(0, 40)}`}
              className="ml-3 w-[calc(100%-0.75rem)] text-left cursor-pointer"
            >
              <div className="flex items-center gap-1">
                <span className={`text-xs text-indigo-500`}>{cfg.icon}</span>
                <span className="text-xs font-medium text-slate-200">{cfg.label}</span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5 truncate">
                {step.toolName ? step.toolName : step.content}
              </p>
            </button>
            {isExpanded && (
              <div className="ml-3 mt-1 p-1.5 rounded bg-slate-950/60 text-xs text-slate-400 max-h-24 overflow-y-auto">
                {step.toolArgs && (
                  <div>
                    <span className="text-amber-500">参数:</span>
                    <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap break-all">
                      {safeJsonPretty(step.toolArgs)}
                    </pre>
                  </div>
                )}
                {step.toolResult && (
                  <div className="mt-1">
                    <span className="text-green-500">结果:</span>
                    <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap break-all">
                      {step.toolResult.slice(0, 300)}
                    </pre>
                  </div>
                )}
                {step.type === "llm" && !step.toolName && (
                  <div className="whitespace-pre-wrap break-all">{step.content}</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function safeJsonPretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
