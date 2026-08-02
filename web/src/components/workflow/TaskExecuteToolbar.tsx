/**
 * TaskExecuteToolbar - 任务执行视图顶部工具栏
 *
 * 显示任务信息、状态、停止按钮、返回按钮。
 */
import { useState, useEffect, useCallback } from "react";
import { ArrowLeft, Square, Loader, Clock } from "lucide-react";
import { getTask, stopTask } from "../../lib/api";
import type { TaskDetailResponse } from "../../types";

interface TaskExecuteToolbarProps {
  workflowId: string;
  taskId: string;
  onBack: () => void;
  /** WebSocket 驱动的实时任务状态（替代 HTTP 轮询） */
  liveStatus?: string;
  liveCompletedAt?: string | null;
}

export default function TaskExecuteToolbar({ workflowId, taskId, onBack, liveStatus, liveCompletedAt }: TaskExecuteToolbarProps) {
  const [taskData, setTaskData] = useState<TaskDetailResponse | null>(null);
  const [stopping, setStopping] = useState(false);

  const fetchTask = useCallback(async () => {
    try {
      const data = await getTask(workflowId, taskId);
      setTaskData(data);
    } catch {
      /* ignore */
    }
  }, [workflowId, taskId]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  // WebSocket 驱动的实时状态合并
  useEffect(() => {
    if (!liveStatus) return;
    setTaskData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        task: {
          ...prev.task,
          status: liveStatus as TaskDetailResponse["task"]["status"],
          completed_at: liveCompletedAt ?? prev.task.completed_at,
        },
      };
    });
  }, [liveStatus, liveCompletedAt]);

  const handleStop = async () => {
    setStopping(true);
    try {
      await stopTask(workflowId, taskId);
      setTimeout(fetchTask, 500);
    } catch (e) {
      console.error("停止任务失败:", e);
    } finally {
      setStopping(false);
    }
  };

  const task = taskData?.task;
  // 以 liveStatus（WebSocket 实时状态）为优先，回退到 HTTP 获取的状态
  const effectiveStatus = liveStatus || task?.status || "";
  const isRunning = effectiveStatus === "running";
  const isFinished = effectiveStatus === "completed" || effectiveStatus === "failed" || effectiveStatus === "stopped";

  const statusLabel: Record<string, string> = {
    pending: "等待中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    stopped: "已停止",
  };

  const statusColor: Record<string, string> = {
    pending: "bg-slate-500/10 text-slate-400 border-slate-500/20",
    running: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    completed: "bg-green-500/10 text-green-400 border-green-500/20",
    failed: "bg-red-500/10 text-red-400 border-red-500/20",
    stopped: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  };

  const nodeCount = task ? Object.keys(task.node_states || {}).length : 0;

  const formatTime = (iso: string | null) => {
    if (!iso) return "-";
    return new Date(iso).toLocaleTimeString("zh-CN");
  };

  return (
    <div
      className="h-12 px-4 bg-slate-900 border-b border-indigo-500/10 flex items-center justify-between shrink-0 select-none"
      role="toolbar"
      aria-label="任务执行工具栏"
    >
      {/* Left */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          aria-label="返回任务历史"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-indigo-500/10 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px]"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          任务历史
        </button>

        <span className="text-sm text-slate-200 font-medium truncate max-w-[300px]" title={task?.name || taskId}>
          {task?.name || taskId}
        </span>
        <span className="text-xs text-slate-500 font-mono">{taskId}</span>

        {task && (
          <div className="flex items-center gap-2">
            <span className={`text-xs px-2 py-0.5 rounded-full border ${statusColor[effectiveStatus] || "text-slate-400"}`}>
              {statusLabel[effectiveStatus] || effectiveStatus}
            </span>
            <span className="text-xs text-slate-400 flex items-center gap-1">
              <Clock size={12} aria-hidden="true" />
              {formatTime(task.started_at)}
            </span>
            <span className="text-xs text-slate-500">{nodeCount} 节点</span>
          </div>
        )}
      </div>

      {/* Center: hint */}
      <div className="text-xs text-slate-500">
        {isRunning ? "点击节点可查看消息流和推理链路" : isFinished ? "点击节点可查看执行消息" : ""}
      </div>

      {/* Right: Stop button */}
      <div className="flex items-center gap-2">
        {isRunning && (
          <button
            type="button"
            onClick={handleStop}
            disabled={stopping}
            aria-label="停止任务"
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-500 text-sm font-medium transition-colors disabled:opacity-50 cursor-pointer min-h-[44px]"
          >
            {stopping ? (
              <Loader size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Square size={14} aria-hidden="true" />
            )}
            停止任务
          </button>
        )}
      </div>
    </div>
  );
}
