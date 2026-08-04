import { ExternalLink, Loader2 } from "lucide-react";

import type { WorkflowTask } from "../../types";

export type WorkflowTaskPatch = Pick<WorkflowTask, "workflow_id" | "task_id" | "status"> &
  Partial<Omit<WorkflowTask, "workflow_id" | "task_id" | "status">>;

function taskTimestamp(task: WorkflowTask): string {
  return task.updated_at || task.completed_at || task.started_at || task.created_at;
}

function patchTimestamp(patch: WorkflowTaskPatch): string {
  return patch.updated_at
    || patch.completed_at
    || patch.started_at
    || patch.created_at
    || "";
}

// eslint-disable-next-line react-refresh/only-export-components -- pure merge helper is covered by node:test
export function upsertWorkflowTask(
  tasks: WorkflowTask[],
  patch: WorkflowTaskPatch,
): WorkflowTask[] {
  const existing = tasks.find(
    (task) => task.workflow_id === patch.workflow_id && task.task_id === patch.task_id,
  );
  if (
    existing
    && patchTimestamp(patch)
    && taskTimestamp(existing).localeCompare(patchTimestamp(patch)) > 0
  ) {
    return tasks;
  }
  const merged: WorkflowTask = {
    name: patch.task_id,
    current_node_id: null,
    run_id: null,
    created_at: patch.created_at || new Date(0).toISOString(),
    started_at: null,
    completed_at: null,
    node_states: patch.node_states || existing?.node_states || {},
    ...existing,
    ...patch,
  };
  return [
    merged,
    ...tasks.filter(
      (task) => task.workflow_id !== patch.workflow_id || task.task_id !== patch.task_id,
    ),
  ].sort((left, right) => taskTimestamp(right).localeCompare(taskTimestamp(left)));
}

const STATUS_LABELS: Record<WorkflowTask["status"], string> = {
  pending: "待启动",
  pre_running: "待确认",
  resume_pending: "恢复中",
  running: "运行中",
  retry_waiting: "等待重试",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
};

const STATUS_STYLES: Record<WorkflowTask["status"], string> = {
  pending: "text-slate-400 bg-slate-500/10",
  pre_running: "text-amber-300 bg-amber-500/10",
  resume_pending: "text-cyan-300 bg-cyan-500/10",
  running: "text-indigo-300 bg-indigo-500/10",
  retry_waiting: "text-amber-300 bg-amber-500/10",
  completed: "text-emerald-300 bg-emerald-500/10",
  failed: "text-red-300 bg-red-500/10",
  stopped: "text-slate-400 bg-slate-500/10",
};

interface ChatWorkflowTasksProps {
  tasks: WorkflowTask[];
  loading?: boolean;
  onOpenTask: (task: WorkflowTask) => void;
}

export default function ChatWorkflowTasks({
  tasks,
  loading = false,
  onOpenTask,
}: ChatWorkflowTasksProps) {
  if (!loading && tasks.length === 0) return null;

  return (
    <section
      className="shrink-0 border-b border-slate-700/50 bg-slate-900/70 px-6 py-2"
      aria-label="后台任务"
    >
      <div className="mx-auto flex w-full max-w-4xl items-center gap-2 overflow-x-auto">
        <span className="shrink-0 text-xs font-medium text-slate-400">后台任务</span>
        {loading && tasks.length === 0 ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500 motion-reduce:animate-none" aria-label="加载中" />
        ) : (
          tasks.slice(0, 6).map((task) => {
            const progress = task.progress;
            return (
              <button
                key={`${task.workflow_id}:${task.task_id}`}
                type="button"
                onClick={() => onOpenTask(task)}
                className="group flex min-w-44 max-w-64 shrink-0 items-center gap-2 rounded-lg border border-slate-700/70 bg-slate-800/70 px-3 py-2 text-left transition-colors hover:border-slate-600 hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                aria-label={`查看任务 ${task.name}`}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-slate-200">{task.name}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500">
                    <span className={`rounded px-1.5 py-0.5 ${STATUS_STYLES[task.status]}`}>
                      {STATUS_LABELS[task.status]}
                    </span>
                    {task.main_takeover && (
                      <span className="rounded bg-violet-500/10 px-1.5 py-0.5 text-violet-300">接管</span>
                    )}
                    {progress && progress.total > 0 && (
                      <span>{progress.completed}/{progress.total}</span>
                    )}
                  </span>
                </span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0 text-slate-600 group-hover:text-slate-400" aria-hidden="true" />
              </button>
            );
          })
        )}
      </div>
    </section>
  );
}
