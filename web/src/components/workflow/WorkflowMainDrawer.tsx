/**
 * WorkflowMainDrawer — Workflow Main 对话抽屉组件
 *
 * 两种模式：
 * - inline:  填参页面使用，不可收起，宽度由父级拖拽分隔线控制
 * - drawer:  任务详情页使用，可拖拽调整宽度，窄于阈值自动收起，
 *            收起后在右侧显示浮窗把手可重新拖出
 *
 * 复用 canonical conversation adapter + StreamingChatView 进行流式对话，
 * 连接 /ws/events 监听 wf_task_update 事件。
 */
import { useState, useEffect, useCallback } from "react";
import { Bot, Sparkles, Loader, GripVertical, ChevronRight, ChevronLeft } from "lucide-react";
import { useWebSocket } from "../../hooks/useWebSocket";
import { useStreamingSession } from "../../hooks/useStreamingSession";
import StreamingChatView from "../StreamingChatView";
import { preStartWorkflow } from "../../lib/api";

type MainTakeoverState = "idle" | "connecting" | "connected" | "running";

// ============ Props ============

export interface WorkflowMainDrawerProps {
  mode: "inline" | "drawer";
  workflowId: string;
  taskId: string | null;
  mainSessionId?: string | null;
  mainTakeover?: boolean;
  workflowName?: string;
  nodeCount?: number;

  // inline mode callbacks
  onMainStarted?: (sessionId: string, taskId: string) => void;
  onVariableUpdate?: (key: string, value: string) => void;
  onTaskStarted?: (taskId: string) => void;

  // drawer mode controls
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;

  /** 额外类名 */
  className?: string;
}

// ============ 常量 ============

const DRAWER_MIN_WIDTH = 360;
const DRAWER_MAX_WIDTH = 900;
const DRAWER_DEFAULT_WIDTH = 520;
const DRAWER_COLLAPSE_THRESHOLD = 150;
const FLOATING_HANDLE_WIDTH = 28;
const DRAG_OPEN_THRESHOLD = 80; // 把手左拖超过此距离展开抽屉

function isEventRecord(event: unknown): event is Record<string, unknown> {
  return typeof event === "object" && event !== null;
}

// ============ 组件 ============

export default function WorkflowMainDrawer({
  mode,
  workflowId,
  taskId,
  mainSessionId: propMainSessionId,
  mainTakeover = false,
  onMainStarted,
  onVariableUpdate,
  onTaskStarted,
  isOpen: propIsOpen = false,
  onOpenChange,
  className = "",
}: WorkflowMainDrawerProps) {
  // ---- 内部状态 ----
  const [takeoverState, setTakeoverState] = useState<MainTakeoverState>("idle");
  const [startError, setStartError] = useState<string | null>(null);
  const [internalSessionId, setInternalSessionId] = useState<string | null>(propMainSessionId || null);

  // drawer 模式尺寸
  const [drawerWidth, setDrawerWidth] = useState(DRAWER_DEFAULT_WIDTH);
  const [isDrawerOpen, setIsDrawerOpen] = useState(propIsOpen);
  const [isResizing, setIsResizing] = useState(false);
  const [isDraggingHandle, setIsDraggingHandle] = useState(false);
  const [handleDragX, setHandleDragX] = useState(0);

  // Session 来源：外部传入优先，否则内部状态
  const mainSessionId = propMainSessionId ?? internalSessionId;

  const takeoverConnected = takeoverState === "connected" || takeoverState === "running";
  // drawer 模式下，仅当有 sessionId 且抽屉打开时才连接
  const drawerConnected = mode === "drawer" ? (isDrawerOpen && !!mainSessionId) : false;
  const isChatConnected = mode === "inline" ? takeoverConnected : drawerConnected;

  // ---- 流式对话（chat 通道） ----
  const {
    messages: chatMessages,
    streamingSegments,
    phase: chatPhase,
    isStreaming: chatIsStreaming,
    connected: chatConnected,
    error: chatError,
    sendMessage,
    retry: retryChat,
    abortStream,
  } = useStreamingSession({
    sessionId: isChatConnected ? mainSessionId : null,
    autoConnect: isChatConnected,
    onExtraEvent: useCallback(
      (event: unknown) => {
        if (
          isEventRecord(event) &&
          event.type === "wf_variable_update" &&
          typeof event.key === "string" &&
          typeof event.value === "string"
        ) {
          onVariableUpdate?.(event.key, event.value);
        }
      },
      [onVariableUpdate],
    ),
  });

  // ---- 工作流事件（events 通道，接收 wf_task_update） ----
  useWebSocket({
    url: "/ws/events",
    autoConnect: takeoverConnected || (mode === "drawer" && !!mainSessionId),
    onMessage: useCallback(
      (event: unknown) => {
        if (
          isEventRecord(event) &&
          event.type === "wf_task_update" &&
          event.workflow_id === workflowId &&
          (!taskId || event.task_id === taskId) &&
          typeof event.status === "string" &&
          event.status !== "pre_running"
        ) {
          setTakeoverState("running");
          if (mode === "inline" && onTaskStarted && typeof event.task_id === "string") {
            onTaskStarted(event.task_id);
          }
        }
      },
      [mode, onTaskStarted, taskId, workflowId],
    ),
    reconnectInterval: 5000,
  });

  // sync external isOpen -> internal
  useEffect(() => {
    if (mode === "drawer") {
      setIsDrawerOpen(propIsOpen);
    }
  }, [propIsOpen, mode]);

  // sync external sessionId
  useEffect(() => {
    if (propMainSessionId) {
      setInternalSessionId(propMainSessionId);
      setTakeoverState("connected");
    }
  }, [propMainSessionId]);

  // ---- 启动 Main ----
  const handleStartMain = async () => {
    setTakeoverState("connecting");
    setStartError(null);
    try {
      const result = await preStartWorkflow(workflowId, true);
      if (result.session_id && result.task_id) {
        setInternalSessionId(result.session_id);
        onMainStarted?.(result.session_id, result.task_id);
        setTakeoverState("connected");
      } else {
        setTakeoverState("idle");
        setStartError("启动 Main 会话失败，请重试");
      }
    } catch {
      setTakeoverState("idle");
      setStartError("启动 Main 会话失败，请检查连接后重试");
    }
  };

  // ---- drawer 拖拽 resize ----
  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
  };

  const collapseDrawer = useCallback(() => {
    setIsDrawerOpen(false);
    onOpenChange?.(false);
    setDrawerWidth(DRAWER_DEFAULT_WIDTH);
  }, [onOpenChange]);

  const openDrawer = useCallback(() => {
    setIsDrawerOpen(true);
    onOpenChange?.(true);
    setDrawerWidth(DRAWER_DEFAULT_WIDTH);
  }, [onOpenChange]);

  useEffect(() => {
    if (!isResizing) return;
    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      setDrawerWidth(Math.max(DRAWER_MIN_WIDTH, Math.min(DRAWER_MAX_WIDTH, newWidth)));
    };
    const handleMouseUp = () => {
      setIsResizing(false);
      if (drawerWidth < DRAWER_COLLAPSE_THRESHOLD) {
        collapseDrawer();
      }
    };
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing, drawerWidth, collapseDrawer]);

  // ---- 把手拖拽 ----
  const handleGripMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingHandle(true);
    setHandleDragX(e.clientX);
  };

  useEffect(() => {
    if (!isDraggingHandle) return;
    const handleMouseMove = (e: MouseEvent) => {
      setHandleDragX(e.clientX);
    };
    const handleMouseUp = () => {
      setIsDraggingHandle(false);
      // 如果向左拖拽超过阈值，展开抽屉
      const dragDelta = handleDragX - window.innerWidth + FLOATING_HANDLE_WIDTH;
      if (dragDelta < -DRAG_OPEN_THRESHOLD) {
        openDrawer();
      }
      setHandleDragX(0);
    };
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isDraggingHandle, handleDragX, openDrawer]);

  // ---- 渲染辅助 ----
  const takeoverEnabled = mode === "inline" || mainTakeover;
  const takeoverStatusLabel = takeoverState === "connected"
    ? takeoverEnabled
      ? "预启动 · Main 已接管"
      : "Main 正在跟踪任务"
    : takeoverState === "running"
      ? takeoverEnabled
        ? "任务执行中 · 逐节点审批"
        : "任务执行中"
      : "";

  const chatHeader = (
    <div className="px-4 py-3 border-b border-indigo-500/10 bg-slate-900/50 flex items-center gap-2 shrink-0">
      <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center">
        <Bot size={14} className="text-white" aria-hidden="true" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-slate-200">Workflow Main</div>
        {takeoverStatusLabel && (
          <div className="text-xs text-slate-500">{takeoverStatusLabel}</div>
        )}
      </div>
      {/* drawer 模式关闭按钮 */}
      {mode === "drawer" && (
        <button
          type="button"
          onClick={collapseDrawer}
          aria-label="关闭 Main 对话"
          className="p-1 rounded hover:bg-indigo-500/10 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      )}
    </div>
  );

  // ============ INLINE 模式渲染 ============

  if (mode === "inline") {
    return (
      <div className={`flex-1 flex flex-col min-h-0 bg-slate-950 ${className}`}>
        {takeoverState === "idle" || takeoverState === "connecting" ? (
          <div className="flex-1 flex items-center justify-center p-8">
            <div className="text-center max-w-sm">
              <div className="w-20 h-20 mx-auto mb-6 rounded-2xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
                <Sparkles size={36} className="text-indigo-500" aria-hidden="true" />
              </div>
              <h3 className="text-lg font-semibold text-slate-200 mb-2">Main 接管模式</h3>
              <p className="text-sm text-slate-400 mb-6 leading-relaxed">
                提前启动一个 AI Main Agent 来接管此工作流。它可以帮你智能填写全局变量、了解工作流结构，并在任务执行时审批每个节点的产出。
              </p>
              {startError && <p className="mb-4 text-sm text-red-300" role="alert">{startError}</p>}
              <button
                type="button"
                onClick={handleStartMain}
                disabled={takeoverState === "connecting"}
                aria-label="启动 Main 会话并接管工作流"
                className="group relative inline-flex items-center gap-2 px-8 py-3 rounded-xl bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-all cursor-pointer min-h-[44px]"
              >
                {takeoverState === "connecting" ? (
                  <>
                    <Loader size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    正在启动 Main 会话...
                  </>
                ) : (
                  <>
                    <Sparkles size={16} aria-hidden="true" />
                    启动 Main 会话并接管工作流
                  </>
                )}
              </button>
            </div>
          </div>
        ) : (
          <StreamingChatView
            messages={chatMessages}
            streamingSegments={streamingSegments}
            isStreaming={chatIsStreaming}
            onSendMessage={sendMessage}
            onAbort={abortStream}
            inputEnabled={true}
            inputPlaceholder="向 Main 发送消息..."
            header={chatHeader}
            conversationId={mainSessionId}
            connected={chatConnected}
            loading={chatPhase === "loading" || chatPhase === "reconnecting"}
            error={chatError}
            onRetry={retryChat}
          />
        )}
      </div>
    );
  }

  // ============ DRAWER 模式：浮窗把手 ============
  if (!isDrawerOpen) {
    return (
      <div
        className="h-full flex items-center shrink-0 relative"
        style={{ width: `${FLOATING_HANDLE_WIDTH}px` }}
      >
        {/* 可拖拽把手 */}
        <div
          onMouseDown={handleGripMouseDown}
          className="absolute inset-0 flex flex-col items-center justify-center gap-1 cursor-col-resize bg-slate-900/80 hover:bg-slate-900 border-l border-indigo-500/20 hover:border-indigo-500/40 transition-colors group"
          title="拖拽打开 Main 对话"
        >
          <ChevronLeft size={12} className="text-indigo-500/60 group-hover:text-indigo-500 transition-colors" aria-hidden="true" />
          <span className="text-xs text-indigo-500/50 group-hover:text-indigo-500/70 transition-colors leading-tight text-center">
            Main
          </span>
          <Bot size={12} className="text-indigo-500/40 group-hover:text-indigo-500/60 transition-colors" aria-hidden="true" />
        </div>
      </div>
    );
  }

  // ============ DRAWER 模式：展开的抽屉 ============
  return (
    <div
      className="h-full flex flex-col bg-slate-900 border-l border-indigo-500/10 shrink-0 overflow-hidden relative"
      style={{ width: `${drawerWidth}px`, minWidth: `${DRAWER_MIN_WIDTH}px`, maxWidth: `${DRAWER_MAX_WIDTH}px` }}
    >
      {/* Resize Handle */}
      <div
        onMouseDown={handleResizeMouseDown}
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

      <StreamingChatView
        messages={chatMessages}
        streamingSegments={streamingSegments}
        isStreaming={chatIsStreaming}
        onSendMessage={sendMessage}
        onAbort={abortStream}
        inputEnabled={true}
        inputPlaceholder="向 Main 发送消息..."
        header={chatHeader}
        conversationId={mainSessionId}
        connected={chatConnected}
        loading={chatPhase === "loading" || chatPhase === "reconnecting"}
        error={chatError}
        onRetry={retryChat}
      />
    </div>
  );
}
