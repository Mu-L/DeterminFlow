/**
 * TaskHistoryPanel - 任务历史列表（参照蓝鲸标准运维"任务历史"页面）
 *
 * 双模式：
 * - 全局模式（workflowId=null）：展示全部工作流的所有任务，调用 listAllTasks
 * - 单工作流模式（workflowId 传入）：展示单个工作流的任务，调用 listTasks
 *
 * 功能：
 * - 状态筛选 Tab（全部/等待中/运行中/已完成/失败/已停止）
 * - 名称/ID 搜索
 * - 表格展示 + 列头排序 + 列可见性设置浮窗
 * - 分页（15/30/50/100 条/页）
 * - 运行中任务自动轮询刷新
 */
import { useState, useEffect, useCallback, useRef } from "react";
import {
  Clock, CheckCircle, XCircle, Loader, Play,
  Search, ChevronUp, ChevronDown, ChevronLeft, ChevronRight,
  Settings, RotateCcw,
} from "lucide-react";
import { listTasks, listAllTasks } from "../../lib/api";
import type { WorkflowTask } from "../../types";

// ============ 常量 ============

type TaskStatus = "" | "pending" | "running" | "completed" | "failed" | "stopped";

const STATUS_TABS: { status: TaskStatus; label: string }[] = [
  { status: "", label: "全部" },
  { status: "pending", label: "等待中" },
  { status: "running", label: "运行中" },
  { status: "completed", label: "已完成" },
  { status: "failed", label: "失败" },
  { status: "stopped", label: "已停止" },
];

const PAGE_SIZE_OPTIONS = [15, 30, 50, 100];

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  pending: { label: "等待中", color: "text-slate-400", icon: <Clock size={14} aria-hidden="true" /> },
  pre_running: { label: "准备中", color: "text-sky-400", icon: <Clock size={14} aria-hidden="true" /> },
  resume_pending: { label: "恢复中", color: "text-amber-400", icon: <Loader size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> },
  running: { label: "运行中", color: "text-blue-400", icon: <Loader size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> },
  retry_waiting: { label: "等待重试", color: "text-amber-400", icon: <Clock size={14} aria-hidden="true" /> },
  completed: { label: "已完成", color: "text-green-400", icon: <CheckCircle size={14} aria-hidden="true" /> },
  failed: { label: "失败", color: "text-red-400", icon: <XCircle size={14} aria-hidden="true" /> },
  stopped: { label: "已停止", color: "text-amber-400", icon: <Play size={14} aria-hidden="true" /> },
};

interface ColumnDef {
  key: string;
  label: string;
  sortable: boolean;
  /** 仅全局模式显示 */
  globalOnly?: boolean;
}

const ALL_COLUMNS: ColumnDef[] = [
  { key: "name", label: "任务名称", sortable: true },
  { key: "workflow_name", label: "模板", sortable: true, globalOnly: true },
  { key: "workflow_id", label: "模板ID", sortable: false, globalOnly: true },
  { key: "status", label: "状态", sortable: true },
  { key: "started_at", label: "执行时间", sortable: true },
  { key: "completed_at", label: "完成时间", sortable: true },
  { key: "created_at", label: "创建时间", sortable: true },
];

// 始终显示的列（不可取消）
const FIXED_COLUMNS = new Set(["name", "status"]);

const EXTRA_COLUMNS: ColumnDef[] = [
  { key: "node_count", label: "节点", sortable: false },
  { key: "duration", label: "耗时", sortable: false },
];

// 默认可见列
function defaultVisibleKeys(isGlobal: boolean): Set<string> {
  const keys = ALL_COLUMNS
    .filter((c) => !c.globalOnly || isGlobal)
    .map((c) => c.key);
  return new Set(keys);
}

// ============ 格式化工具 ============

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return "< 1秒";
  if (seconds < 60) return `${seconds}秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return `${m}分${s}秒`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}时${rm}分${s}秒`;
}

function calcDuration(t: WorkflowTask): string {
  if (!t.started_at || !t.completed_at) return "-";
  const start = new Date(t.started_at).getTime();
  const end = new Date(t.completed_at).getTime();
  return formatDuration(Math.round((end - start) / 1000));
}

function nodeCount(t: WorkflowTask): number {
  return Object.keys(t.node_states || {}).length;
}

// ============ 组件 ============

interface TaskHistoryPanelProps {
  /** 工作流 ID：null/skip 表示全局模式，传入表示单工作流模式 */
  workflowId?: string | null;
  onTaskClick: (taskId: string, workflowId: string) => void;
  /** 重做任务回调（仅终态任务可用） */
  onRedoTask?: (taskId: string, workflowId: string) => void;
  refreshTrigger?: number;
}

/** 终态状态集合（可重做） */
const TERMINAL_STATUSES = new Set(["completed", "failed", "stopped"]);

export default function TaskHistoryPanel({ workflowId, onTaskClick, onRedoTask, refreshTrigger }: TaskHistoryPanelProps) {
  const isGlobal = workflowId == null;

  // 查询参数
  const [statusFilter, setStatusFilter] = useState<TaskStatus>("");
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 数据
  const [tasks, setTasks] = useState<WorkflowTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  // 列可见性
  const [visibleKeys, setVisibleKeys] = useState<Set<string>>(() => defaultVisibleKeys(isGlobal));
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const columnMenuRef = useRef<HTMLDivElement>(null);

  // 全局模式切换时重置可见列
  useEffect(() => {
    setVisibleKeys(defaultVisibleKeys(isGlobal));
  }, [isGlobal]);

  // 关闭列菜单（点击外部 + Escape）
  useEffect(() => {
    if (!columnMenuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (columnMenuRef.current && !columnMenuRef.current.contains(e.target as Node)) {
        setColumnMenuOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setColumnMenuOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [columnMenuOpen]);

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    const params = {
      status: statusFilter || undefined,
      search: search || undefined,
      sort_by: sortBy,
      sort_order: sortOrder,
      page,
      page_size: pageSize,
    };
    try {
      const data = isGlobal
        ? await listAllTasks(params)
        : await listTasks(workflowId!, params);
      setTasks(data.tasks);
      setTotal(data.total);
    } catch (e) {
      console.error("加载任务列表失败:", e);
    } finally {
      setLoading(false);
    }
  }, [workflowId, isGlobal, statusFilter, search, sortBy, sortOrder, page, pageSize]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks, refreshTrigger]);

  // 运行中任务自动轮询
  useEffect(() => {
    const hasRunning = tasks.some((t) => [
      "pending", "pre_running", "resume_pending", "running", "retry_waiting",
    ].includes(t.status));
    if (!hasRunning) return;
    const interval = setInterval(fetchTasks, 3000);
    return () => clearInterval(interval);
  }, [tasks, fetchTasks]);

  const handleSort = (key: string) => {
    if (sortBy === key) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      setSortOrder("desc");
    }
    setPage(1);
  };

  const handleStatusChange = (s: TaskStatus) => { setStatusFilter(s); setPage(1); };
  const handleSearch = (value: string) => { setSearch(value); setPage(1); };

  const toggleColumn = (key: string) => {
    if (FIXED_COLUMNS.has(key)) return;
    setVisibleKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // 当前可见的主列 + 额外列
  const visibleMainCols = ALL_COLUMNS.filter(
    (c) => visibleKeys.has(c.key) && (!c.globalOnly || isGlobal)
  );

  // 渲染单元格内容
  const renderCell = (col: ColumnDef, t: WorkflowTask) => {
    const cfg = STATUS_CONFIG[t.status] || STATUS_CONFIG.pending;
    switch (col.key) {
      case "name":
        return (
          <div>
            <div className="text-sm text-slate-200 font-medium truncate max-w-[280px]">
              {t.name || "未命名任务"}
            </div>
            <div className="text-xs text-slate-500 font-mono mt-0.5">{t.task_id}</div>
          </div>
        );
      case "workflow_name":
        return (
          <div>
            <div className="text-sm text-slate-200 truncate max-w-[180px]">
              {t.workflow_name || "-"}
            </div>
            <div className="text-xs text-slate-500 font-mono mt-0.5">{t.workflow_id}</div>
          </div>
        );
      case "workflow_id":
        return <span className="text-xs text-slate-400 font-mono">{t.workflow_id}</span>;
      case "status":
        return (
          <div className={`flex items-center gap-1.5 ${cfg.color}`}>
            {cfg.icon}
            <span className="text-xs font-medium">{cfg.label}</span>
          </div>
        );
      case "started_at":
        return <span className="text-xs text-slate-400">{formatTime(t.started_at)}</span>;
      case "completed_at":
        return <span className="text-xs text-slate-400">{formatTime(t.completed_at)}</span>;
      case "created_at":
        return <span className="text-xs text-slate-400">{formatTime(t.created_at)}</span>;
      default:
        return null;
    }
  };

  // ============ 渲染 ============

  return (
    <div className="flex-1 overflow-auto" role="main" aria-label="任务历史">
      {/* 顶部工具栏 */}
      <div className="px-6 pt-4 pb-3">
        {/* 状态筛选 Tab */}
        <div className="flex items-center gap-2 mb-3 flex-wrap" role="tablist" aria-label="任务状态筛选">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.status}
              type="button"
              role="tab"
              aria-selected={statusFilter === tab.status}
              onClick={() => handleStatusChange(tab.status)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer min-h-[44px] ${
                statusFilter === tab.status
                  ? "bg-indigo-500 text-white"
                  : "bg-slate-900 text-slate-400 hover:bg-indigo-500/10 hover:text-slate-200"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* 搜索栏 */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden="true" />
            <input
              type="text"
              value={search}
              onChange={(e) => handleSearch(e.target.value)}
              placeholder="搜索任务名称、ID、模板名..."
              aria-label="搜索任务"
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-slate-900 border border-indigo-500/10 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/40 transition-colors"
            />
          </div>
          <span className="text-xs text-slate-500">共 {total} 条记录</span>
        </div>
      </div>

      {/* 表格 */}
      <div className="px-6 pb-4">
        {loading ? (
            <div className="flex items-center justify-center py-20 text-slate-400" role="status" aria-label="加载中">
            <Loader size={20} className="animate-spin motion-reduce:animate-none mr-2" aria-hidden="true" />
            加载中...
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState hasFilter={!!statusFilter || !!search} />
        ) : (
          <>
            <div className="rounded-xl border border-indigo-500/10 overflow-hidden">
              <table className="w-full" role="table">
                <thead>
                  <tr className="bg-slate-900/50 border-b border-indigo-500/10">
                    {visibleMainCols.map((col) => (
                      <th
                        key={col.key}
                        scope="col"
                        onClick={() => col.sortable && handleSort(col.key)}
                        aria-sort={sortBy === col.key ? (sortOrder === "asc" ? "ascending" : "descending") : undefined}
                        className={`px-4 py-3 text-left text-xs font-medium text-slate-400 ${
                          col.sortable ? "cursor-pointer hover:text-slate-200 select-none" : ""
                        }`}
                      >
                        <div className="flex items-center gap-1">
                          {col.label}
                          {col.sortable && sortBy === col.key && (
                            sortOrder === "asc" ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />
                          )}
                        </div>
                      </th>
                    ))}
                    {/* 额外列 */}
                    {visibleKeys.has("node_count") && (
                      <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-400">节点</th>
                    )}
                    {visibleKeys.has("duration") && (
                      <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-400">耗时</th>
                    )}
                    {/* 列设置按钮 */}
                    <th scope="col" className="px-2 py-3 w-8">
                      <div className="relative" ref={columnMenuRef}>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); setColumnMenuOpen(!columnMenuOpen); }}
                          aria-label="列设置"
                          aria-expanded={columnMenuOpen}
                          aria-haspopup="menu"
                          className="p-1.5 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-slate-900/80 transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
                        >
                          <Settings size={14} aria-hidden="true" />
                        </button>
                        {columnMenuOpen && (
                          <div
                            className="absolute right-0 top-full mt-1 w-48 bg-slate-900 border border-indigo-500/20 rounded-xl shadow-xl z-50 py-2"
                            role="menu"
                            aria-label="列可见性设置"
                          >
                            <div className="px-3 py-1 text-xs text-slate-500 font-medium">
                              显示列
                            </div>
                            {ALL_COLUMNS.filter((c) => !c.globalOnly || isGlobal).map((col) => (
                              <label
                                key={col.key}
                                className="flex items-center gap-2 px-3 py-1.5 hover:bg-indigo-500/5 cursor-pointer"
                              >
                                <input
                                  type="checkbox"
                                  checked={visibleKeys.has(col.key)}
                                  disabled={FIXED_COLUMNS.has(col.key)}
                                  onChange={() => toggleColumn(col.key)}
                                  className="w-3.5 h-3.5 rounded accent-indigo-500"
                                />
                                <span className="text-xs text-slate-200">{col.label}</span>
                              </label>
                            ))}
                            <div className="border-t border-indigo-500/10 my-1" />
                            {EXTRA_COLUMNS.map((col) => (
                              <label
                                key={col.key}
                                className="flex items-center gap-2 px-3 py-1.5 hover:bg-indigo-500/5 cursor-pointer"
                              >
                                <input
                                  type="checkbox"
                                  checked={visibleKeys.has(col.key)}
                                  onChange={() => toggleColumn(col.key)}
                                  className="w-3.5 h-3.5 rounded accent-indigo-500"
                                />
                                <span className="text-xs text-slate-200">{col.label}</span>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                    </th>
                    {/* 操作列 */}
                    {onRedoTask && (
                      <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-slate-400">操作</th>
                    )}
                  </tr>
                </thead>

                <tbody>
                  {tasks.map((t) => (
                    <tr
                      key={`${t.workflow_id}-${t.task_id}`}
                      onClick={() => onTaskClick(t.task_id, t.workflow_id)}
                      tabIndex={0}
                      role="row"
                      aria-label={`任务: ${t.name || t.task_id}`}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onTaskClick(t.task_id, t.workflow_id); } }}
                      className="border-b border-indigo-500/5 hover:bg-slate-900/80 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
                    >
                      {visibleMainCols.map((col) => (
                        <td key={col.key} className="px-4 py-3">
                          {renderCell(col, t)}
                        </td>
                      ))}
                      {visibleKeys.has("node_count") && (
                        <td className="px-4 py-3">
                          <span className="text-xs text-slate-400">{nodeCount(t)} 节点</span>
                        </td>
                      )}
                      {visibleKeys.has("duration") && (
                        <td className="px-4 py-3">
                          <span className="text-xs text-slate-400">{calcDuration(t)}</span>
                        </td>
                      )}
                      {/* 列设置占位 */}
                      <td className="px-2 py-3" />
                      {/* 操作列 */}
                      {onRedoTask && (
                        <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                          {TERMINAL_STATUSES.has(t.status) ? (
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); onRedoTask(t.task_id, t.workflow_id); }}
                              title="使用当前任务数据（节点选择、入参）再次创建整个任务"
                              aria-label={`重做整个任务 ${t.name || t.task_id}`}
                              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 hover:text-indigo-300 text-xs font-medium transition-colors cursor-pointer min-h-[36px]"
                            >
                              <RotateCcw size={12} aria-hidden="true" />重做整个任务
                            </button>
                          ) : (
                            <span className="text-xs text-slate-600">-</span>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 分页 */}
            <Pagination
              page={page} totalPages={totalPages}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
            />
          </>
        )}
      </div>
    </div>
  );
}

// ============ 分页组件 ============

function Pagination({
  page, totalPages, pageSize, onPageChange, onPageSizeChange,
}: {
  page: number; totalPages: number; pageSize: number;
  onPageChange: (p: number) => void;
  onPageSizeChange: (s: number) => void;
}) {
  return (
    <div className="flex items-center justify-between mt-4">
      <div className="flex items-center gap-2">
        <label htmlFor="task-page-size" className="text-xs text-slate-500">每页</label>
        <select
          id="task-page-size"
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="px-2 py-1 rounded-lg bg-slate-900 border border-indigo-500/10 text-xs text-slate-200 focus:outline-none focus:border-indigo-500/40"
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>{size}</option>
          ))}
        </select>
        <span className="text-xs text-slate-500">条</span>
      </div>

      <nav className="flex items-center gap-1" aria-label="分页导航">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          aria-label="上一页"
          className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-900 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </button>

        {generatePageNumbers(page, totalPages).map((p, i) =>
          typeof p === "number" ? (
            <button
              key={i}
              type="button"
              onClick={() => onPageChange(p)}
              aria-current={p === page ? "page" : undefined}
              aria-label={`第 ${p} 页`}
              className={`w-8 h-8 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
                p === page ? "bg-indigo-500 text-white" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
              }`}
            >
              {p}
            </button>
          ) : (
            <span key={i} className="px-1 text-slate-500 text-xs" aria-hidden="true">...</span>
          )
        )}

        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          aria-label="下一页"
          className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-900 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </nav>
    </div>
  );
}

// ============ 空状态 ============

function EmptyState({ hasFilter }: { hasFilter: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-slate-500" role="status" aria-label={hasFilter ? "没有匹配的任务" : "暂无任务"}>
      <Clock size={48} className="mb-4 opacity-50" aria-hidden="true" />
      <p className="text-lg">{hasFilter ? "没有匹配的任务记录" : "暂无任务记录"}</p>
      <p className="text-sm mt-1">
        {hasFilter ? "请尝试调整筛选条件或搜索关键词" : "在编辑器中启动新任务后，记录将在此显示"}
      </p>
    </div>
  );
}

// ============ 分页页码 ============

function generatePageNumbers(current: number, total: number): (number | "...")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages: (number | "...")[] = [1];
  if (current > 3) pages.push("...");
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);
  if (current < total - 2) pages.push("...");
  pages.push(total);
  return pages;
}
