import type { RefObject } from "react";
import { List, Plus, Trash2, X } from "lucide-react";

import type { WorkflowSummary } from "../../types";

export type WorkflowPageTab = "templates" | "scripts" | "history";

export interface WorkflowConfirmState {
  message: string;
  onConfirm: () => void;
}

interface WorkflowTabBarProps {
  tab: WorkflowPageTab;
  onTabChange: (tab: WorkflowPageTab) => void;
}

const TABS: Array<{ id: WorkflowPageTab; label: string }> = [
  { id: "templates", label: "模板" },
  { id: "scripts", label: "脚本库" },
  { id: "history", label: "任务历史" },
];

export function WorkflowTabBar({ tab, onTabChange }: WorkflowTabBarProps) {
  return (
    <div role="tablist" aria-label="工作流页面导航" className="h-11 flex items-center gap-0 border-b border-indigo-500/10 shrink-0">
      {TABS.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={tab === id}
          aria-controls={`wf-tabpanel-${id}`}
          onClick={() => onTabChange(id)}
          className={`px-5 h-full text-sm font-medium transition-colors duration-200 border-b-2 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30 ${
            tab === id
              ? "text-slate-100 border-indigo-500"
              : "text-slate-400 border-transparent hover:text-slate-100"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

interface WorkflowTemplatePanelProps {
  workflows: WorkflowSummary[];
  loading: boolean;
  errorMessage: string | null;
  onDismissError: () => void;
  onCreate: () => void;
  onView: (workflowId: string) => void;
  onDelete: (workflowId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  idle: "待运行",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
};

const STATUS_COLORS: Record<string, string> = {
  idle: "text-slate-400",
  running: "text-blue-400",
  completed: "text-green-400",
  failed: "text-red-400",
  stopped: "text-amber-400",
};

export function WorkflowTemplatePanel({
  workflows,
  loading,
  errorMessage,
  onDismissError,
  onCreate,
  onView,
  onDelete,
}: WorkflowTemplatePanelProps) {
  return (
    <div className="flex-1 p-6 overflow-auto" id="wf-tabpanel-templates" role="tabpanel" aria-label="工作流模板">
      <div className="max-w-5xl mx-auto">
        {errorMessage && (
          <div role="alert" aria-live="polite" className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-center justify-between">
            <span>{errorMessage}</span>
            <button type="button" onClick={onDismissError} aria-label="关闭错误通知" className="ml-4 text-red-400/60 hover:text-red-400 transition-colors duration-200 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center">
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-semibold text-slate-100">工作流模板</h2>
            <p className="text-sm text-slate-400 mt-1">管理 Agent 手动编排工作流，编辑与运行分离</p>
          </div>
          <button type="button" onClick={onCreate} aria-label="新建工作流" className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors cursor-pointer min-h-[44px]">
            <Plus size={16} aria-hidden="true" />新建工作流
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20 gap-1.5" role="status" aria-label="正在加载工作流列表">
            {[0, 1, 2].map((index) => (
              <span key={index} className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse motion-reduce:animate-none" style={{ animationDelay: `${index * 150}ms` }} />
            ))}
            <span className="sr-only">加载中...</span>
          </div>
        ) : workflows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-500">
            <List size={48} className="mb-4 opacity-50" aria-hidden="true" />
            <p className="text-lg">暂无工作流</p>
            <p className="text-sm mt-1">点击&quot;新建工作流&quot;开始创建</p>
          </div>
        ) : (
          <section aria-label="工作流模板列表" className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {workflows.map((workflow) => (
              <div
                key={workflow.workflow_id}
                role="button"
                tabIndex={0}
                onClick={() => onView(workflow.workflow_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onView(workflow.workflow_id);
                  }
                }}
                className="group p-5 rounded-xl bg-slate-900 border border-indigo-500/10 hover:border-indigo-500/40 cursor-pointer transition-all duration-200 hover:shadow-lg hover:shadow-indigo-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
              >
                <div className="flex items-start justify-between mb-3">
                  <h3 className="text-slate-100 font-medium truncate flex-1">{workflow.name || "未命名工作流"}</h3>
                </div>
                <div className="flex items-center gap-3 text-xs text-slate-400 mb-3">
                  <span>{workflow.node_count} 节点</span>
                  <span>v{workflow.version}</span>
                  <span className={STATUS_COLORS[workflow.status] || "text-slate-400"}>
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1 align-middle" aria-hidden="true" />
                    {STATUS_LABELS[workflow.status] || workflow.status}
                  </span>
                  {!!workflow.running_tasks && <span className="text-blue-400">({workflow.running_tasks} 任务)</span>}
                </div>
                <div className="mt-3 text-xs text-slate-500 mb-3">
                  更新于 {new Date(workflow.updated_at).toLocaleDateString("zh-CN")}
                </div>
                <div className="flex items-center pt-2 border-t border-indigo-500/5">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(workflow.workflow_id);
                    }}
                    aria-label={`删除工作流 ${workflow.name || "未命名工作流"}`}
                    className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-500/5 hover:bg-red-500/15 text-red-400/70 hover:text-red-400 text-xs transition-colors ml-auto cursor-pointer min-h-[44px]"
                  >
                    <Trash2 size={12} aria-hidden="true" />删除
                  </button>
                </div>
              </div>
            ))}
          </section>
        )}
      </div>
    </div>
  );
}

interface WorkflowConfirmDialogProps {
  dialog: WorkflowConfirmState | null;
  onClose: () => void;
  confirmButtonRef: RefObject<HTMLButtonElement>;
  descriptionId: string;
}

export function WorkflowConfirmDialog({ dialog, onClose, confirmButtonRef, descriptionId }: WorkflowConfirmDialogProps) {
  if (!dialog) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="确认操作"
      aria-describedby={descriptionId}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}
      tabIndex={-1}
    >
      <div className="bg-slate-800 rounded-xl p-6 max-w-md mx-4 border border-slate-700">
        <p id={descriptionId} className="text-slate-100 mb-6">{dialog.message}</p>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg text-slate-300 hover:text-slate-100 transition-colors duration-200 cursor-pointer min-h-[44px]">
            取消
          </button>
          <button type="button" ref={confirmButtonRef} onClick={dialog.onConfirm} className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-white transition-colors duration-200 cursor-pointer min-h-[44px]">
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
