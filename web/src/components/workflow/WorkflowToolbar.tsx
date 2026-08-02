/**
 * WorkflowToolbar - 顶部工具栏
 *
 * 支持两种模式：
 *   view   - 查看模式：返回列表、编辑、启动新任务
 *   editor - 编辑模式：返回（未保存拦截）、保存
 */
import { useState, useRef, useEffect } from "react";
import {
  ArrowLeft,
  Save,
  Play,
  Loader,
  Maximize2,
  ZoomIn,
  Edit,
} from "lucide-react";
import { createAndRunTask } from "../../lib/api";

interface WorkflowToolbarProps {
  workflowId: string;
  mode: "view" | "editor";
  onBack: () => void;
  onEdit?: () => void;
  onTaskStarted?: (taskId: string) => void;
  /** 启动新任务改为打开填参页面（参数变量功能） */
  onStartTaskFill?: () => void;
  onSave?: () => void;
  hasUnsaved?: boolean;
  saving?: boolean;
  name?: string;
  onRename?: (newName: string) => void;
}

export default function WorkflowToolbar({
  workflowId,
  mode,
  onBack,
  onEdit,
  onTaskStarted,
  onStartTaskFill,
  onSave,
  hasUnsaved,
  saving = false,
  name,
  onRename,
}: WorkflowToolbarProps) {
  const [starting, setStarting] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const handleRenameConfirm = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== name) {
      onRename?.(trimmed);
    }
    setIsEditing(false);
  };

  const handleSave = () => {
    onSave?.();
  };

  const handleStartTask = () => {
    // 优先使用填参页面流程
    if (onStartTaskFill) {
      onStartTaskFill();
      return;
    }
    // 降级：直接启动（兼容旧行为）
    setStarting(true);
    createAndRunTask(workflowId)
      .then((result) => {
        if (result.success) {
          onTaskStarted?.(result.task_id);
        } else {
          console.error(result.message || "启动失败");
        }
      })
      .catch((e) => {
        console.error("启动任务失败:", e);
      })
      .finally(() => setStarting(false));
  };

  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false);
  const leaveConfirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (showLeaveConfirm && leaveConfirmRef.current) {
      leaveConfirmRef.current.focus();
    }
  }, [showLeaveConfirm]);

  const handleBack = () => {
    if (mode === "editor" && hasUnsaved) {
      setShowLeaveConfirm(true);
      return;
    }
    onBack();
  };

  const confirmLeave = () => {
    setShowLeaveConfirm(false);
    onBack();
  };

  return (
    <>
    {/* 未保存离开确认对话框 */}
    {showLeaveConfirm && (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
        onClick={() => setShowLeaveConfirm(false)}
        onKeyDown={(e) => { if (e.key === "Escape") setShowLeaveConfirm(false); }}
        role="dialog"
        aria-modal="true"
        aria-label="确认离开编辑"
      >
        <div
          className="bg-slate-800 border border-indigo-500/20 rounded-lg p-5 max-w-sm w-full mx-4 shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="text-sm font-semibold text-slate-200 mb-2">未保存的更改</h3>
          <p className="text-xs text-slate-400 mb-4">您有未保存的更改，确定要离开吗？</p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowLeaveConfirm(false)}
              className="px-3 py-1.5 rounded text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors cursor-pointer"
            >
              取消
            </button>
            <button
              ref={leaveConfirmRef}
              type="button"
              onClick={confirmLeave}
              className="px-3 py-1.5 rounded text-xs bg-amber-500 hover:bg-amber-600 text-white transition-colors cursor-pointer"
            >
              离开
            </button>
          </div>
        </div>
      </div>
    )}
    <div className="h-12 px-4 bg-slate-900 border-b border-indigo-500/10 flex items-center justify-between shrink-0 select-none">
      {/* Left */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleBack}
          aria-label="返回"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-indigo-500/10 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px]"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          返回
        </button>

        {name ? (
          isEditing ? (
            <input
              ref={inputRef}
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRenameConfirm();
                if (e.key === "Escape") { setIsEditing(false); setEditValue(name); }
              }}
              onBlur={handleRenameConfirm}
              className="text-sm text-slate-200 bg-slate-950 border border-indigo-500/30 rounded px-2 py-0.5 outline-none focus:border-indigo-500 ml-1"
              placeholder="输入工作流名称"
            />
          ) : (
            <button
              type="button"
              className="text-sm text-slate-200 ml-1 cursor-pointer hover:text-indigo-500 transition-colors border-b border-transparent hover:border-indigo-500/30 truncate max-w-[200px] bg-transparent p-0 text-left"
              onClick={() => { setIsEditing(true); setEditValue(name); }}
              aria-label="点击编辑工作流名称"
            >
              {name}
            </button>
          )
        ) : (
          <div className="text-xs text-slate-500 ml-1 font-mono">
            {workflowId}
          </div>
        )}

        {mode === "editor" && (
          <span className="flex items-center gap-1 ml-2 px-2 py-0.5 rounded bg-amber-500/10 text-xs text-amber-500">
            编辑中
          </span>
        )}

        {mode === "view" && (
          <span className="flex items-center gap-1 ml-2 px-2 py-0.5 rounded bg-blue-500/10 text-xs text-blue-500">
            查看模式
          </span>
        )}
      </div>

      {/* Center: Zoom indicator */}
      {mode === "editor" && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <ZoomIn size={12} aria-hidden="true" />
          <span>右键更多操作</span>
        </div>
      )}
      {mode === "view" && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <ZoomIn size={12} aria-hidden="true" />
          <span>查看工作流定义 · 点击右上角编辑或启动任务</span>
        </div>
      )}

      {/* Right */}
      <div className="flex items-center gap-2">
        {mode === "editor" ? (
          <>
            <button
              type="button"
              onClick={() => {
                const el = document.querySelector(".react-flow__controls-fitview");
                if (el instanceof HTMLElement) el.click();
              }}
              aria-label="重置视图"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-indigo-500/10 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px]"
            >
              <Maximize2 size={14} aria-hidden="true" />
              重置
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              aria-label="保存工作流"
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-medium transition-colors disabled:opacity-50 cursor-pointer min-h-[44px]"
            >
              <Save size={14} aria-hidden="true" />
              {saving ? "保存中..." : "保存"}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={onEdit}
              aria-label="编辑工作流"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-500 text-sm transition-colors cursor-pointer min-h-[44px]"
            >
              <Edit size={14} aria-hidden="true" />
              编辑
            </button>
            <button
              type="button"
              onClick={() => {
                const el = document.querySelector(".react-flow__controls-fitview");
                if (el instanceof HTMLElement) el.click();
              }}
              aria-label="重置视图"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-indigo-500/10 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px]"
            >
              <Maximize2 size={14} aria-hidden="true" />
              重置
            </button>
            <button
              type="button"
              onClick={handleStartTask}
              disabled={starting}
              aria-label="启动新任务"
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-green-500/10 hover:bg-green-500/20 text-green-500 text-sm font-medium transition-colors disabled:opacity-50 cursor-pointer min-h-[44px]"
            >
              {starting ? (
                <Loader size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              )}
              启动新任务
            </button>
          </>
        )}
      </div>
    </div>
    </>
  );
}
