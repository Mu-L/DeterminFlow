import { useState, useRef, useEffect } from "react";
import { Edit3, Bot, AlertTriangle, ChevronRight, ChevronDown } from "lucide-react";
import { Message } from "../types";
import MarkdownRenderer from "./MarkdownRenderer";
import ThinkingChain from "./ThinkingChain";
import CompressionDivider from "./CompressionDivider";
import ToolCallCard from "./ToolCallCard";
import ContentSafetyWarningMsg from "./ContentSafetyWarningMsg";
import ContentSafetyDiagnosticMsg from "./ContentSafetyDiagnosticMsg";
import RecursionLimitMsg from "./RecursionLimitMsg";

// ============================================================
// MessageRenderer — 全类型消息注册表 + 统一路由组件
//
// 所有消息类型通过此注册表路由，不再在 ChatMessage 或
// SessionDetailPanel 中写 if/else 分支。
//
// 新增类型只需两步：
// 1. 创建组件，实现 MsgComponent 接口 ({ message: Message })
// 2. 在 messageRegistry 中注册一行
// ============================================================

type MsgComponent = React.ComponentType<{
  message: Message;
  onEdit?: (messageId: string, newContent: string) => void;
  onCommand?: (payload: { type: string; [key: string]: unknown }) => boolean;
  editable?: boolean;
  streaming?: boolean;
  readonly?: boolean;
}>;

// ---- 各类型渲染组件 ----

// 解析用户消息，提取用户实际内容（隐藏注入信息）
function parseUserContent(content: string): { userContent: string; hasInjection: boolean } {
  const userMessageMarker = "<USER_MESSAGE>";
  const markerIndex = content.indexOf(userMessageMarker);

  if (markerIndex !== -1) {
    // 找到标记，只返回用户消息部分
    return {
      userContent: content.substring(markerIndex + userMessageMarker.length).trim(),
      hasInjection: true,
    };
  }

  // 没有标记，返回原始内容
  return { userContent: content, hasInjection: false };
}

const UserMsg: MsgComponent = ({ message, onEdit, editable, streaming, readonly }) => {
  const isAgent = (message.source && message.source.startsWith("agent:"))
    || (message.name && message.name.startsWith("agent_"));
  const agentId = message.source?.startsWith("agent:") ? message.source.slice(6)
    : message.name?.startsWith("agent_") ? message.name.slice(6) : null;
  const [injectionExpanded, setInjectionExpanded] = useState(false);

  // 编辑状态
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editControlsRef = useRef<HTMLDivElement>(null);

  // 解析消息内容
  const { userContent, hasInjection } = parseUserContent(message.content || "");

  // 进入编辑时自动聚焦 textarea
  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [editing]);

  const showEditButton = editable && !streaming && !readonly && !isAgent;

  if (isAgent && agentId) {
    return (
      <div className="flex justify-start mb-4" role="article" aria-label={`Agent ${agentId} 的消息`}>
        <div className="max-w-[85%]">
          <div className="flex items-center gap-1.5 mb-1">
            <Bot size={14} className="text-purple-500" aria-hidden="true" />
            <span className="text-xs font-mono text-purple-500">Agent {agentId}</span>
          </div>
          <div className="px-4 py-3 rounded-2xl rounded-bl-md border border-purple-500/30 bg-purple-500/10">
            <MarkdownRenderer content={userContent} className="text-sm" />
          </div>
        </div>
      </div>
    );
  }

  const handleStartEdit = () => {
    setEditContent(userContent);
    setEditing(true);
  };

  const handleSubmitEdit = () => {
    const trimmed = editContent.trim();
    if (trimmed && trimmed !== userContent && onEdit && message.id) {
      onEdit(message.id, trimmed);
    }
    setEditing(false);
  };

  const handleCancelEdit = () => {
    setEditing(false);
  };

  const handleBlur = (e: React.FocusEvent) => {
    // 不要在焦点移到编辑控制按钮时取消编辑（防止 onBlur 竞态）
    if (editControlsRef.current && editControlsRef.current.contains(e.relatedTarget as Node)) {
      return;
    }
    handleCancelEdit();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmitEdit();
    }
    if (e.key === "Escape") {
      handleCancelEdit();
    }
  };

  return (
    <div className="flex justify-end mb-4" role="article" aria-label="用户消息">
      <div className="max-w-[70%]">
        {/* 注入元信息折叠栏 */}
        {hasInjection && message.injection_meta && message.injection_meta.length > 0 && !editing && (
          <div className="mb-1.5">
            <button
              type="button"
              onClick={() => setInjectionExpanded(!injectionExpanded)}
              aria-expanded={injectionExpanded}
              aria-label={`系统注入信息 ${message.injection_meta.length}项，${injectionExpanded ? "收起" : "展开"}`}
              className="flex items-center gap-1.5 text-xs text-muted-foreground/60 hover:text-muted-foreground/80 transition-colors duration-200 cursor-pointer min-h-[44px] focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded"
            >
              {injectionExpanded
                ? <ChevronDown size={14} aria-hidden="true" />
                : <ChevronRight size={14} aria-hidden="true" />}
              <span>系统注入信息</span>
              <span className="text-xs opacity-60">({message.injection_meta.length}项)</span>
            </button>
            {injectionExpanded && (
              <div className="mt-1 p-2 rounded-md bg-slate-800/40 border border-border/30" role="list" aria-label="注入信息详情">
                {message.injection_meta.map((meta, index) => (
                  <div key={index} className="text-xs text-muted-foreground/50 py-0.5" role="listitem">
                    <span className="font-medium">{meta.name}:</span> {meta.content}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 用户消息内容 */}
        <div className="group relative">
          {editing ? (
            <div className="px-4 py-3 rounded-2xl rounded-br-md bg-indigo-500/20 border border-indigo-500/30 text-slate-100">
              <textarea
                ref={textareaRef}
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={handleBlur}
                rows={Math.min(editContent.split("\n").length, 10)}
                aria-label="编辑消息内容"
                className="w-full bg-transparent border-none outline-none text-sm text-slate-100 placeholder:text-muted-foreground resize-none min-h-[44px]"
              />
              <div ref={editControlsRef} className="flex items-center justify-between mt-2">
                <span className="text-xs text-muted-foreground/60">
                  Enter 提交 · Esc 取消
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={handleCancelEdit}
                    aria-label="取消编辑"
                    className="px-2 py-1 text-xs rounded-md bg-slate-700/60 text-muted-foreground hover:text-slate-200 hover:bg-slate-600 transition-colors duration-200 cursor-pointer min-h-[44px]"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={handleSubmitEdit}
                    disabled={!editContent.trim() || editContent.trim() === userContent}
                    aria-label="发送编辑后的消息"
                    className="px-2 py-1 text-xs rounded-md bg-indigo-500/80 text-white hover:bg-indigo-500 transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer min-h-[44px]"
                  >
                    发送
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="px-4 py-3 rounded-2xl rounded-br-md bg-indigo-500/20 border border-indigo-500/30 text-slate-100">
              <p className="text-sm whitespace-pre-wrap">{userContent}</p>
            </div>
          )}

          {/* 编辑按钮：hover 时显示在气泡右侧 */}
          {showEditButton && !editing && (
            <button
              type="button"
              onClick={handleStartEdit}
              aria-label="编辑消息"
              className="absolute -right-9 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-muted-foreground/40 hover:text-indigo-500 hover:bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-all duration-200 cursor-pointer min-h-[44px] min-w-[44px]"
            >
              <Edit3 size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

const AssistantMsg: MsgComponent = ({ message }) => {
  return (
    <div className="flex justify-start mb-4" role="article" aria-label="助手消息">
      <div className="max-w-[85%]">
        {message.reasoning_content && (
          <ThinkingChain content={message.reasoning_content} isStreaming={false} />
        )}
        {message.content && (
          <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-slate-800/50 border border-slate-700/50">
            <MarkdownRenderer content={message.content} className="text-sm" />
          </div>
        )}
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mt-2 space-y-2">
            {message.tool_calls.map((tc, i) => (
              <ToolCallCard
                key={tc.id || i}
                name={tc.function.name}
                args={tc.function.arguments}
                result={tc.function.result}
                status="completed"
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

const ToolMsg: MsgComponent = ({ message }) => {
  const [expanded, setExpanded] = useState(false);
  const content = message.content || "";
  const brief = content.length > 100 ? content.slice(0, 100) + "..." : content;
  return (
    <div className="mb-3 ml-10" role="article" aria-label="工具返回消息">
      <div className="bg-green-500/5 border border-green-500/20 rounded-lg px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          aria-label={`工具返回：${brief}${content.length > 100 ? (expanded ? "，收起完整内容" : "，展开完整内容") : ""}`}
          className="flex items-center gap-2 w-full text-left cursor-pointer min-h-[44px] transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded"
        >
          <ChevronRight size={14} className={`text-green-500 transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden="true" />
          <span className="text-xs text-muted-foreground">{brief}</span>
          {content.length > 100 && (
            <span className="text-xs text-muted-foreground ml-auto transition-colors duration-200">{expanded ? "收起" : "展开"}</span>
          )}
        </button>
        {expanded && content.length > 100 && (
          <pre className="mt-2 text-xs bg-slate-900/60 rounded p-2 overflow-x-auto text-slate-400 whitespace-pre-wrap" role="region" aria-label="工具返回完整内容">{content}</pre>
        )}
      </div>
    </div>
  );
};

const CompressionDividerMsg: MsgComponent = ({ message }) => {
  const evt = message.event || message.compression_event;
  if (!evt) return null;
  return <CompressionDivider event={evt} strategy={message.strategy} />;
};

const SystemPromptMsg: MsgComponent = () => null; // 不渲染

const ErrorFallback: MsgComponent = ({ message }) => {
  return (
    <div className="flex justify-start mb-4" role="alert" aria-label="未知消息类型">
      <div className="max-w-[85%] px-4 py-3 rounded-2xl rounded-bl-md bg-slate-800/50 border border-red-500/30">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle size={14} className="text-red-400" aria-hidden="true" />
          <span className="text-xs text-red-400">未知消息类型: {message.type || "(无)"}</span>
        </div>
        <pre className="text-xs text-slate-500 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(message, null, 2)}
        </pre>
      </div>
    </div>
  );
};

// ---- 注册表 ----

const messageRegistry: Record<string, MsgComponent> = {
  user: UserMsg,
  assistant: AssistantMsg,
  tool: ToolMsg,
  compression_divider: CompressionDividerMsg,
  system_prompt: SystemPromptMsg,
  content_safety_warning: ContentSafetyWarningMsg,
  content_filter_warning: ContentSafetyWarningMsg,
  content_safety_diagnostic: ContentSafetyDiagnosticMsg,
  recursion_limit_reached: RecursionLimitMsg,
};

// ---- 路由组件 ----

interface MessageRendererProps {
  message: Message;
  onEdit?: (messageId: string, newContent: string) => void;
  onCommand?: (payload: { type: string; [key: string]: unknown }) => boolean;
  editable?: boolean;
  streaming?: boolean;
  readonly?: boolean;
}

/**
 * 统一消息渲染路由。
 *
 * 根据 message.type 查找注册表渲染对应组件。
 * - 新格式：type 字段 (user/assistant/tool/compression_divider/...)
 * - 旧格式兼容：type 不存在时 fallback 到 display 或 role
 * - 未知类型：ErrorFallback（向用户展示原始 JSON）
 */
export default function MessageRenderer({ message, onEdit, onCommand, editable, streaming, readonly }: MessageRendererProps) {
  // 1. 确定 type
  let msgType = message.type;
  if (!msgType) {
    // 旧格式兼容
    if (message.display) {
      msgType = message.display;
    } else if (message.compression_event) {
      msgType = "compression_divider";
    } else if (message.role) {
      msgType = message.role;
      if (msgType === "system") return null;  // 旧 system 消息不渲染
    }
  }

  if (!msgType || msgType === "system_prompt") return null;

  // 2. 查找注册表
  const Component = messageRegistry[msgType];
  if (!Component) {
    return <ErrorFallback message={message} />;
  }

  // 3. 渲染，透传编辑相关 props
  return (
    <Component
      message={message}
      onEdit={onEdit}
      onCommand={onCommand}
      editable={editable}
      streaming={streaming}
      readonly={readonly}
    />
  );
}
