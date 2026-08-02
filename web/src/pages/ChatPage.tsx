import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  Send, Zap, Trash2, X,
  Square, Edit3, Minimize2,
} from "lucide-react";

// Dialog focus trap helper
function useDialogFocus(open: boolean, containerRef: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    if (!open || !containerRef.current) return;
    const el = containerRef.current;
    // Auto-focus first input/textarea
    const firstInput = el.querySelector<HTMLInputElement | HTMLTextAreaElement>("input, textarea");
    firstInput?.focus();
    // Focus trap
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusable = el.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    el.addEventListener("keydown", handleKeyDown);
    return () => el.removeEventListener("keydown", handleKeyDown);
  }, [open, containerRef]);
}
import { ScrollArea } from "@/components/ui/scroll-area";

import { useChat } from "../hooks/useChat";
import { useSessions } from "../hooks/useSessions";
import { useApprovals } from "../hooks/useApprovals";
import type { Message } from "../types";
import ChatMessage from "../components/ChatMessage";
import StreamingMessage from "../components/StreamingMessage";
import ThinkingChain from "../components/ThinkingChain";
import ToolCallCard from "../components/ToolCallCard";
import ApprovalPanel from "../components/ApprovalPanel";
import ResizableSidePanel from "../components/ResizableSidePanel";
import MonitoringCard from "../components/MonitoringCard";

import {
  fetchSessionDetail, fetchSessionSystemPrompt, deleteSession, killSession,
  compressSession, createNewMainSession,
  fetchPresetPhrases, createPresetPhrase, updatePresetPhrase, deletePresetPhrase,
} from "../lib/api";
import { SessionDetail, PresetPhrase } from "../types";

const EMPTY_MESSAGES: Message[] = [];
export default function ChatPage() {
  const {
    connected, sendMessage, sendMessageToSession, editMessageAndResend,
    switchToSession, loadSessionHistory,
    getSessionMessages, getSessionStreaming, getSessionTokenUsage,
    getMainSessionId, setMainSessionId, abortStream,
  } = useChat();
  const { sessions, mainSessionId, loadSessions } = useSessions();
  const {
    pendingApprovals, resolvedApprovals,
    approve: handleApprove, reject: handleReject, clearResolved,
  } = useApprovals();
  const [input, setInput] = useState("");
  const [sidePanel, setSidePanel] = useState<"sessions" | "prompt" | "workspace">("sessions");
  const messageScrollAreaRef = useRef<HTMLDivElement>(null);
  const autoScrollFrameRef = useRef<number | null>(null);
  const shouldFollowOutputRef = useRef(true);

  // 预设短语状态
  const [presetPhrases, setPresetPhrases] = useState<PresetPhrase[]>([]);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(editDialogOpen, dialogRef);

  const [editLabel, setEditLabel] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [compressing, setCompressing] = useState(false);

  // 自定义确认对话框状态
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
  }>({ open: false, title: "", message: "", onConfirm: () => {} });
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  // 确认对话框焦点管理
  useEffect(() => {
    if (confirmDialog.open && confirmBtnRef.current) {
      setTimeout(() => confirmBtnRef.current?.focus(), 50);
    }
  }, [confirmDialog.open]);

  // 新建会话错误状态
  const [createSessionError, setCreateSessionError] = useState<string | null>(null);

  // 监控卡片折叠状态（默认折叠）
  const [monitoringCollapsed, setMonitoringCollapsed] = useState(true);

  // 实时 LLM 上下文状态
  const [llmContext, setLlmContext] = useState<Awaited<ReturnType<typeof fetchSessionSystemPrompt>> | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);

  // 会话切换相关状态
  const [viewingSessionId, setViewingSessionId] = useState<string | null>(null); // null = 当前主会话（实时）
  const [viewingSession, setViewingSession] = useState<SessionDetail | null>(null);
  const [loadingSession, setLoadingSession] = useState(false);

  // 主会话 ID 同步到 useChat（用于事件的路由回退）
  useEffect(() => {
    if (mainSessionId) {
      setMainSessionId(mainSessionId);
      // 首次加载或主会话变更时，拉取历史消息
      if (getSessionMessages(mainSessionId).length === 0) {
        fetchSessionDetail(mainSessionId).then((detail) => {
          loadSessionHistory(mainSessionId, detail.messages || [], detail.token_usage || null);
        }).catch(() => {});
      }
    }
  }, [mainSessionId, setMainSessionId, getSessionMessages, loadSessionHistory]);

  // 实时获取当前查看会话的完整 LLM 上下文
  const loadSystemPrompt = useCallback(async () => {
    const targetId = viewingSessionId || mainSessionId;
    if (!targetId) return;
    setPromptLoading(true);
    try {
      const data = await fetchSessionSystemPrompt(targetId);
      setLlmContext(data);
    } catch (e) {
      console.error("获取 LLM 上下文失败:", e);
    } finally {
      setPromptLoading(false);
    }
  }, [viewingSessionId, mainSessionId]);

  // 派生变量：当前目标会话及对应的消息/流式状态
  const getTargetSessionId = useCallback((): string | null => {
    return viewingSessionId || getMainSessionId();
  }, [viewingSessionId, getMainSessionId]);
  const targetSessionId = getTargetSessionId();
  const displayMessages = targetSessionId ? getSessionMessages(targetSessionId) : EMPTY_MESSAGES;
  const { isStreaming: isStreamingForCurrentView, streamingSegments, hasStreamedThisCycle } =
    getSessionStreaming(targetSessionId);
  const tokenUsage = getSessionTokenUsage(targetSessionId);

  // 切换到提示词面板时自动加载，会话切换时也自动刷新
  useEffect(() => {
    if (sidePanel === "prompt") {
      loadSystemPrompt();
    }
  }, [sidePanel, viewingSessionId, mainSessionId, loadSystemPrompt]);

  // 每次 LLM 对话结束后，如果正在看提示词面板，自动刷新
  useEffect(() => {
    if (sidePanel === "prompt" && !isStreamingForCurrentView) {
      loadSystemPrompt();
    }
  }, [displayMessages.length, isStreamingForCurrentView, sidePanel, loadSystemPrompt]);

  const getMessageViewport = useCallback(() => {
    return messageScrollAreaRef.current?.querySelector<HTMLElement>(
      "[data-radix-scroll-area-viewport]"
    ) || null;
  }, []);

  const scrollToBottom = useCallback((force = false) => {
    if (!force && !shouldFollowOutputRef.current) return;
    if (autoScrollFrameRef.current !== null) return;

    autoScrollFrameRef.current = requestAnimationFrame(() => {
      autoScrollFrameRef.current = null;
      const viewport = getMessageViewport();
      if (!viewport) return;
      viewport.scrollTop = viewport.scrollHeight;
      if (force) shouldFollowOutputRef.current = true;
    });
  }, [getMessageViewport]);

  // 用户主动向上滚动后停止自动跟随，回到底部附近时恢复。
  useEffect(() => {
    const viewport = getMessageViewport();
    if (!viewport) return;
    const handleScroll = () => {
      const distanceToBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      shouldFollowOutputRef.current = distanceToBottom < 160;
    };
    handleScroll();
    viewport.addEventListener("scroll", handleScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", handleScroll);
  }, [getMessageViewport]);

  const streamingTailLength = useMemo(() => {
    const tail = streamingSegments[streamingSegments.length - 1];
    if (!tail) return 0;
    if (tail.type === "text" || tail.type === "reasoning") return tail.content.length;
    return tail.tool.args.length + (tail.tool.result?.length || 0);
  }, [streamingSegments]);

  // 流式更新按动画帧即时跟随，避免每个 token 重启平滑滚动动画。
  useEffect(() => {
    if (isStreamingForCurrentView || hasStreamedThisCycle || displayMessages.length > 0) {
      scrollToBottom();
    }
  }, [
    displayMessages.length,
    streamingSegments.length,
    streamingTailLength,
    isStreamingForCurrentView,
    hasStreamedThisCycle,
    scrollToBottom,
  ]);

  // 切换会话后滚动到最底部
  useEffect(() => {
    shouldFollowOutputRef.current = true;
    scrollToBottom(true);
  }, [targetSessionId, scrollToBottom]);

  useEffect(() => {
    return () => {
      if (autoScrollFrameRef.current !== null) {
        cancelAnimationFrame(autoScrollFrameRef.current);
        autoScrollFrameRef.current = null;
      }
    };
  }, []);

  // 判断会话是否可交互（后端有已编译 graph 且状态非 error/idle）
  const isSessionInteractive = useCallback((session: SessionDetail | null): boolean => {
    if (!session) return false;
    if (session.has_graph === false) return false;
    return session.status !== "error" && session.status !== "idle";
  }, []);

  // 切换到查看某个会话
  const handleViewSession = useCallback(async (sessionId: string) => {
    // 如果点击的是当前正在查看的，取消查看回到主会话视图
    if (viewingSessionId === sessionId) {
      setViewingSessionId(null);
      setViewingSession(null);
      switchToSession(null);
      return;
    }

    setLoadingSession(true);
    try {
      const detail = await fetchSessionDetail(sessionId);
      setViewingSessionId(sessionId);
      setViewingSession(detail);
      // 加载历史消息到缓存并切换视图
      loadSessionHistory(sessionId, detail.messages || [], detail.token_usage || null);
      switchToSession(sessionId);
    } catch (e) {
      console.error("加载会话详情失败:", e);
    } finally {
      setLoadingSession(false);
    }
  }, [viewingSessionId, switchToSession, loadSessionHistory]);

  // 删除会话
  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmDialog({
      open: true,
      title: "删除会话",
      message: `确定要删除会话 ${sessionId} 吗？此操作不可恢复。`,
      onConfirm: async () => {
        try {
          await deleteSession(sessionId);
          if (viewingSessionId === sessionId) {
            setViewingSessionId(null);
            setViewingSession(null);
            switchToSession(null);
          }
          loadSessions();
        } catch (err) {
          console.error("删除会话失败:", err);
        }
      },
    });
  }, [viewingSessionId, switchToSession, loadSessions]);

  // 终止会话
  const handleKillSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmDialog({
      open: true,
      title: "终止会话",
      message: `确定要终止会话 ${sessionId} 吗？`,
      onConfirm: async () => {
        try {
          await killSession(sessionId);
          loadSessions();
        } catch (err) {
          console.error("终止会话失败:", err);
        }
      },
    });
  }, [loadSessions]);

  // 加载预设短语
  useEffect(() => {
    fetchPresetPhrases().then(setPresetPhrases).catch(() => {});
  }, []);

  // 中止当前查看会话的流式输出
  const handleStop = useCallback(async () => {
    const targetId = getTargetSessionId();
    if (!targetId) return;
    await abortStream(targetId);
  }, [getTargetSessionId, abortStream]);

  // 手动触发上下文压缩
  const handleCompress = useCallback(async () => {
    const targetId = viewingSessionId || mainSessionId;
    if (!targetId || compressing) return;
    setCompressing(true);
    try {
      await compressSession(targetId);
    } catch (e) {
      console.error("压缩失败:", e);
    } finally {
      setCompressing(false);
    }
  }, [viewingSessionId, mainSessionId, compressing]);

  // 点击预设短语
  const handlePresetSend = useCallback((content: string) => {
    if (!connected) return;
    const targetId = getTargetSessionId();
    if (!targetId) return;
    const { isStreaming: targetStreaming } = getSessionStreaming(targetId);
    if (targetStreaming) return;
    if (viewingSessionId && viewingSession && isSessionInteractive(viewingSession)) {
      sendMessageToSession(viewingSessionId, content);
    } else {
      sendMessage(content);
    }
  }, [connected, viewingSessionId, viewingSession, isSessionInteractive, sendMessageToSession, sendMessage, getTargetSessionId, getSessionStreaming]);

  // 编辑对话框 - 打开新增
  const openAddDialog = useCallback(() => {
    setEditingId(null);
    setEditLabel("");
    setEditContent("");
    setEditDialogOpen(true);
  }, []);

  // 编辑对话框 - 打开编辑
  const openEditDialog = useCallback((phrase: PresetPhrase) => {
    setEditingId(phrase.id);
    setEditLabel(phrase.label);
    setEditContent(phrase.content);
    setEditDialogOpen(true);
  }, []);

  // 保存预设短语
  const handleSavePresetPhrase = useCallback(async () => {
    if (!editLabel.trim() || !editContent.trim()) return;
    try {
      if (editingId) {
        const updated = await updatePresetPhrase(editingId, { label: editLabel.trim(), content: editContent.trim() });
        setPresetPhrases((prev) => prev.map((p) => (p.id === editingId ? updated : p)));
      } else {
        const created = await createPresetPhrase({ label: editLabel.trim(), content: editContent.trim() });
        setPresetPhrases((prev) => [...prev, created]);
      }
      setEditDialogOpen(false);
    } catch (e) {
      console.error("保存预设短语失败:", e);
    }
  }, [editLabel, editContent, editingId]);

  // 新建主会话（支持指定 agent_type）
  const handleCreateSession = useCallback(async (agentType?: string) => {
    setCreateSessionError(null);
    try {
      const result = await createNewMainSession(agentType);
      await loadSessions();
      // 自动选中新建的会话
      if (result.session_id) {
        handleViewSession(result.session_id);
      }
    } catch (e) {
      setCreateSessionError("新建会话失败：" + (e as Error).message);
    }
  }, [loadSessions, handleViewSession]);

  // 删除预设短语
  const handleDeletePresetPhrase = useCallback(async (phraseId: string) => {
    try {
      await deletePresetPhrase(phraseId);
      setPresetPhrases((prev) => prev.filter((p) => p.id !== phraseId));
    } catch (e) {
      console.error("删除预设短语失败:", e);
    }
  }, []);

  const handleSend = () => {
    if (!input.trim() || !connected) return;
    const targetId = getTargetSessionId();
    if (!targetId) return;
    // 目标会话正在流式输出时阻止发送
    const { isStreaming: targetStreaming } = getSessionStreaming(targetId);
    if (targetStreaming) return;

    if (viewingSessionId && viewingSession && isSessionInteractive(viewingSession)) {
      sendMessageToSession(viewingSessionId, input.trim());
    } else {
      sendMessage(input.trim());
    }
    setInput("");
  };

  const handleEditDisplayedMessage = useCallback((msgId: string, newContent: string) => {
    const targetId = getTargetSessionId();
    if (targetId) {
      editMessageAndResend(targetId, msgId, newContent);
    }
  }, [getTargetSessionId, editMessageAndResend]);

  // 预合并工具结果：将连续的 assistant + tool 消息合并，使 ToolCallCard 统一显示 args + result
  const mergedMessages = useMemo(() => {
    const result: Message[] = [];
    let i = 0;
    while (i < displayMessages.length) {
      const msg = displayMessages[i];
      if (msg.type === "tool") {
        i++;
        continue;
      }
      if (msg.type === "assistant" && msg.tool_calls && msg.tool_calls.length > 0) {
        const toolResults: Record<string, string> = {};
        let j = i + 1;
        while (j < displayMessages.length && displayMessages[j].type === "tool") {
          const toolMsg = displayMessages[j];
          if (toolMsg.tool_call_id) {
            toolResults[toolMsg.tool_call_id] = toolMsg.content || "";
          }
          j++;
        }
        const enhancedToolCalls = msg.tool_calls.map((tc) => ({
          ...tc,
          function: {
            ...tc.function,
            result: toolResults[tc.id] || tc.function.result,
          },
        }));
        result.push({ ...msg, tool_calls: enhancedToolCalls });
        i = j;
      } else {
        result.push(msg);
        i++;
      }
    }
    return result;
  }, [displayMessages]);

  // 计算可编辑消息范围：最后一条 compression_divider 之后的 user 消息可编辑
  const editableMap = useMemo(() => {
    const map = new Set<string>();
    // 找到最后一条 compression_divider 的索引
    let lastDividerIdx = -1;
    for (let i = displayMessages.length - 1; i >= 0; i--) {
      if (displayMessages[i].type === "compression_divider") {
        lastDividerIdx = i;
        break;
      }
    }
    // 标记 divider 之后的 user 消息为可编辑
    for (let i = lastDividerIdx + 1; i < displayMessages.length; i++) {
      const msg = displayMessages[i];
      if (msg.type === "user" && msg.id) {
        map.add(msg.id);
      }
    }
    return map;
  }, [displayMessages]);

  const isViewingOther = viewingSessionId !== null;
  // 判断是否是不可交互的历史会话（已完成/出错的非活跃会话）
  const isReadOnly = isViewingOther && !isSessionInteractive(viewingSession);

  // 会话列表按 updated_at 降序排序（最近活跃的在前）
  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [sessions],
  );
  const handleMonitoringToggle = useCallback(() => {
    setMonitoringCollapsed((value) => !value);
  }, []);

  return (
    <div className="h-[calc(100dvh-3.5rem)] flex">
      {/* Token 监控竖向边栏 - 左侧，可折叠 */}
      <div
        className={`shrink-0 flex flex-col transition-all duration-300 ${monitoringCollapsed ? "w-7" : "w-64"}`}
        role="complementary"
        aria-label="Token 监控面板"
      >
        <div className={`h-full ${monitoringCollapsed ? "px-1" : "px-2"} py-3 overflow-y-auto`}>
          <MonitoringCard
            tokenUsage={tokenUsage}
            collapsed={monitoringCollapsed}
            onToggle={handleMonitoringToggle}
          />
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0" role="main" aria-label="聊天区域">
        {/* 审批通知面板 */}
        <ApprovalPanel
          pendingApprovals={pendingApprovals}
          resolvedApprovals={resolvedApprovals}
          onApprove={handleApprove}
          onReject={handleReject}
          onClearResolved={clearResolved}
        />

        {/* Messages */}
        <ScrollArea ref={messageScrollAreaRef} className="flex-1 px-6 py-4">
          <div className="w-full max-w-4xl mx-auto">
            {mergedMessages.length === 0 && !isStreamingForCurrentView && !loadingSession && (
              <div className="flex flex-col items-center justify-center h-64 text-center" role="status" aria-label="暂无消息">
                <div className="w-16 h-16 rounded-2xl bg-indigo-500 flex items-center justify-center mb-4 animate-float motion-reduce:animate-none">
                  <Zap size={32} className="text-white" aria-hidden="true" />
                </div>
                <h2 className="text-xl font-semibold text-slate-200 mb-2">
                  {isViewingOther ? "此会话暂无消息" : "DeterminFlow"}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {isViewingOther
                    ? "可以在下方输入框向此会话发送消息"
                    : "从对话开始，或把复杂流程交给 Workflow 稳定执行"
                  }
                </p>
              </div>
            )}

            {loadingSession && (
              <div className="flex items-center justify-center h-32 text-muted-foreground text-sm" role="status" aria-label="加载会话消息中">
                <div className="animate-spin motion-reduce:animate-none w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full mr-2" aria-hidden="true" />
                <span className="sr-only">加载中</span>
                加载会话消息中...
              </div>
            )}

            {mergedMessages.map((msg, i) => (
              <ChatMessage
                key={`${viewingSessionId || "live"}-${i}`}
                message={msg}
                onEdit={handleEditDisplayedMessage}
                editable={msg.type === "user" && !!msg.id && editableMap.has(msg.id)}
                streaming={msg.type === "user" ? isStreamingForCurrentView : undefined}
                readonly={isReadOnly}
              />
            ))}

            {/* Streaming segments - show when streaming for the currently viewed session */}
            {isStreamingForCurrentView && streamingSegments.map((seg, i) => {
              if (seg.type === "text") {
                return (
                  <StreamingMessage
                    key={`seg-text-${i}`}
                    content={seg.content}
                    showCursor={isStreamingForCurrentView && i === streamingSegments.length - 1}
                  />
                );
              }
              if (seg.type === "reasoning") {
                const isLastReasoning = i === streamingSegments.length - 1;
                return (
                  <div key={`seg-reasoning-${i}`} className="flex justify-start mb-4">
                    <div className="max-w-[85%]">
                      <ThinkingChain
                        content={seg.content}
                        isStreaming={isLastReasoning && isStreamingForCurrentView}
                      />
                    </div>
                  </div>
                );
              }
              return (
                <ToolCallCard
                  key={`seg-tool-${seg.tool.run_id}`}
                  name={seg.tool.name}
                  args={seg.tool.args}
                  result={seg.tool.result}
                  status={seg.tool.status}
                />
              );
            })}
          </div>
        </ScrollArea>

        {/* Input Area */}
        <div className="px-6 pb-4">
          <div className="w-full max-w-4xl mx-auto space-y-2">
            {/* 预设短语栏 */}
            <div className="group flex items-center gap-1.5 flex-wrap min-h-[28px]" role="toolbar" aria-label="预设短语">
              <div className="flex-1 flex items-center gap-1.5 flex-wrap">
                {presetPhrases.length === 0 ? (
                  <span className="text-xs text-muted-foreground/40 italic">暂无预设短语，点击右侧编辑按钮添加</span>
                ) : (
                  presetPhrases.map((phrase) => (
                    <button
                      type="button"
                      key={phrase.id}
                      onClick={() => handlePresetSend(phrase.content)}
                      disabled={isReadOnly || !connected}
                      aria-label={`发送预设短语: ${phrase.label}`}
                      className="px-2.5 py-1 text-xs rounded-full bg-slate-700/60 text-slate-300 hover:bg-indigo-500/20 hover:text-indigo-400 border border-border/40 transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer whitespace-nowrap"
                    >
                      {phrase.label}
                    </button>
                  ))
                )}
              </div>
              {/* 编辑按钮 - hover 时显示 */}
              <button
                type="button"
                onClick={openAddDialog}
                title="编辑预设短语"
                aria-label="编辑预设短语"
                className="p-1.5 rounded-md text-muted-foreground hover:text-indigo-400 hover:bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-all cursor-pointer flex-shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center"
              >
                <Edit3 size={14} aria-hidden="true" />
              </button>
            </div>

            {/* 快捷按钮栏 */}
            <div className="flex items-center gap-2" role="toolbar" aria-label="快捷操作">
              <button
                type="button"
                onClick={handleCompress}
                disabled={isReadOnly || compressing}
                title="手动触发上下文压缩"
                aria-label="手动触发上下文压缩"
                className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors duration-200 cursor-pointer ${
                  compressing
                    ? "bg-slate-700 text-muted-foreground cursor-not-allowed"
                    : "bg-slate-700/60 text-slate-400 hover:bg-purple-500/20 hover:text-purple-400 border border-border/40"
                }`}
              >
                <Minimize2 size={12} aria-hidden="true" />
                {compressing ? "压缩中..." : "压缩上下文"}
              </button>
            </div>

            {/* 输入框 */}
            <div className={`flex items-end gap-3 bg-slate-800/80 border border-border/60 rounded-xl p-3 transition-colors duration-200 ${isReadOnly ? "opacity-50" : ""}`}>
              <div className="flex-1">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  aria-label="聊天消息输入"
                  placeholder={
                    isReadOnly
                      ? "该会话已结束，无法发送消息"
                      : isViewingOther
                        ? `向会话 ${viewingSessionId} 发消息... (Shift+Enter 换行)`
                        : "输入消息... (Shift+Enter 换行)"
                  }
                  rows={1}
                  disabled={isReadOnly}
                  className="w-full bg-transparent border-none outline-none text-sm text-foreground placeholder:text-muted-foreground resize-none max-h-32 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded-lg"
                  style={{ minHeight: "44px" }}
                />
              </div>
              {/* 发送/中止按钮 */}
              {isStreamingForCurrentView ? (
                <button
                  type="button"
                  onClick={handleStop}
                  title="中止输出"
                  aria-label="中止输出"
                  className="p-2 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/40 transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
                >
                  <Square size={18} className="fill-current" aria-hidden="true" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={!input.trim() || isStreamingForCurrentView || isReadOnly || !connected}
                  aria-label="发送消息"
                  className={`p-2 rounded-lg transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center ${
                    input.trim() && !isStreamingForCurrentView && !isReadOnly && connected
                      ? "bg-indigo-500 hover:bg-indigo-400 text-white"
                      : "bg-slate-700 text-muted-foreground cursor-not-allowed"
                  }`}
                >
                  <Send size={18} aria-hidden="true" />
                </button>
              )}
            </div>
            {!connected && (
              <div className="text-center text-red-400 text-xs mt-2" role="alert" aria-live="polite">WebSocket 未连接，请检查后端服务</div>
            )}
          </div>
        </div>

        {/* 预设短语编辑弹窗 */}
        {editDialogOpen && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
            onClick={() => setEditDialogOpen(false)}
            onKeyDown={(e) => { if (e.key === "Escape") setEditDialogOpen(false); }}
            role="presentation"
          >
            <div
              ref={dialogRef}
              className="bg-slate-800 border border-border/60 rounded-xl p-4 sm:p-5 w-[460px] max-w-[calc(100vw-2rem)] max-h-[80vh] overflow-y-auto shadow-2xl"
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label={editingId ? "编辑预设短语" : "新增预设短语"}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-slate-200">
                  {editingId ? "编辑预设短语" : "新增预设短语"}
                </h3>
                <button
                  type="button"
                  onClick={() => setEditDialogOpen(false)}
                  aria-label="关闭对话框"
                  className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-slate-700 cursor-pointer"
                >
                  <X size={16} aria-hidden="true" />
                </button>
              </div>

              {/* 已有预设短语列表 */}
              {presetPhrases.length > 0 && (
                <div className="space-y-1.5 mb-4 max-h-48 overflow-y-auto">
                  {presetPhrases.map((phrase) => (
                    <div
                      key={phrase.id}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs ${
                        editingId === phrase.id
                      ? "bg-indigo-500/15 border border-indigo-500/30"
                      : "bg-slate-700/50 hover:bg-slate-700 border border-transparent"
                      }`}
                    >
                      <div className="flex-1 min-w-0 mr-2">
                        <div className="text-slate-200 font-medium truncate">{phrase.label}</div>
                        <div className="text-muted-foreground truncate">{phrase.content}</div>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          type="button"
                          onClick={() => openEditDialog(phrase)}
                          className="p-1 rounded text-muted-foreground hover:text-cyan-400 hover:bg-slate-600 cursor-pointer"
                          title="编辑"
                          aria-label={`编辑短语: ${phrase.label}`}
                        >
                          <Edit3 size={12} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeletePresetPhrase(phrase.id)}
                          className="p-1 rounded text-muted-foreground hover:text-red-400 hover:bg-slate-600 cursor-pointer"
                          title="删除"
                          aria-label={`删除短语: ${phrase.label}`}
                        >
                          <Trash2 size={12} aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 新增/编辑表单 */}
              <div className="space-y-3">
                <div>
                  <label htmlFor="preset-label" className="text-xs text-muted-foreground block mb-1">显示名</label>
                  <input
                    id="preset-label"
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    placeholder="例如：自我介绍"
                    className="w-full px-3 py-2 text-sm bg-slate-700 border border-border/60 rounded-lg text-foreground placeholder:text-muted-foreground outline-none focus:border-indigo-500/60 transition-colors"
                  />
                </div>
                <div>
                  <label htmlFor="preset-content" className="text-xs text-muted-foreground block mb-1">实际输入内容</label>
                  <textarea
                    id="preset-content"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    placeholder="输入发送给 LLM 的实际文本..."
                    rows={3}
                    className="w-full px-3 py-2 text-sm bg-slate-700 border border-border/60 rounded-lg text-foreground placeholder:text-muted-foreground outline-none focus:border-indigo-500/60 transition-colors resize-none"
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-1">
                  {editingId && (
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(null);
                        setEditLabel("");
                        setEditContent("");
                      }}
                      className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors cursor-pointer min-h-[44px]"
                    >
                      取消编辑
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setEditDialogOpen(false)}
                    className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors cursor-pointer min-h-[44px]"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={handleSavePresetPhrase}
                    disabled={!editLabel.trim() || !editContent.trim()}
                    className="px-4 py-1.5 text-xs rounded-lg bg-indigo-500 text-white hover:bg-indigo-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer min-h-[44px]"
                  >
                    {editingId ? "保存" : "添加"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right Side Panel - Resizable */}
      <ResizableSidePanel
        sidePanel={sidePanel}
        setSidePanel={setSidePanel}
        sortedSessions={sortedSessions}
        viewingSessionId={viewingSessionId}
        mainSessionId={mainSessionId}
        onViewSession={handleViewSession}
        onDeleteSession={handleDeleteSession}
        onKillSession={handleKillSession}
        onCreateSession={handleCreateSession}
        llmContext={llmContext}
        promptLoading={promptLoading}
        onRefreshPrompt={loadSystemPrompt}
        viewingSession={viewingSession}
        sessions={sessions}
      />

      {/* 自定义确认对话框 */}
      {confirmDialog.open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
          onKeyDown={(e) => { if (e.key === "Escape") setConfirmDialog((prev) => ({ ...prev, open: false })); }}
          role="presentation"
        >
          <div
            className="bg-slate-800 border border-border/60 rounded-xl p-5 w-[400px] max-w-[calc(100vw-2rem)] shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={confirmDialog.title}
          >
            <h3 className="text-sm font-medium text-slate-200 mb-2">{confirmDialog.title}</h3>
            <p className="text-xs text-muted-foreground mb-5">{confirmDialog.message}</p>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
                className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors duration-200 cursor-pointer min-h-[44px]"
              >
                取消
              </button>
              <button
                ref={confirmBtnRef}
                type="button"
                onClick={() => {
                  confirmDialog.onConfirm();
                  setConfirmDialog((prev) => ({ ...prev, open: false }));
                }}
                className="px-4 py-1.5 text-xs rounded-lg bg-red-500 text-white hover:bg-red-400 transition-colors duration-200 cursor-pointer min-h-[44px]"
              >
                确认
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建会话错误提示 */}
      {createSessionError && (
        <div
          className="fixed bottom-4 right-4 z-50 max-w-sm bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 flex items-center gap-3 shadow-lg"
          role="alert"
          aria-live="polite"
        >
          <span className="text-xs text-red-300 flex-1">{createSessionError}</span>
          <button
            type="button"
            onClick={() => setCreateSessionError(null)}
            className="text-red-400 hover:text-red-300 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="关闭错误提示"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
