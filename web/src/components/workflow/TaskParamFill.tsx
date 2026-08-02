/**
 * TaskParamFill - 任务创建时填参页面（左右双栏布局 + 可拖拽分隔线）
 * 左侧：参数填写表单  右侧：WorkflowMainDrawer（inline 模式，不可收起）
 */
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { ArrowLeft, Play, Loader, Variable, AlertCircle, GripVertical, ChevronDown, ChevronRight, FolderOpen } from "lucide-react";
import { getWorkflowVariables, getVariableReferences } from "../../lib/api";
import WorkflowMainDrawer from "./WorkflowMainDrawer";
import type { WorkflowVariable } from "../../types";

interface TaskParamFillProps {
  workflowId: string;
  workflowName?: string;
  nodeCount?: number;
  onBack: () => void;
  onTaskStarted: (taskId: string, openMainDrawer?: boolean) => void;
  /** 用户选中的节点ID列表（用于变量过滤） */
  selectedNodeIds?: string[];
  /** 被取消的节点ID列表（用于创建任务时传递给后端） */
  disabledNodeIds?: string[];
  /** 执行方案 ID（选择了方案且未修改时） */
  schemeId?: string;
  /** 最终选中的节点 ID 列表（方案修改后或手动选择时） */
  effectiveSelectedNodeIds?: string[];
  /** 重做时预填的参数值（来自原任务） */
  prefillValues?: Record<string, string> | null;
  /** 重做时预填的工作空间覆盖路径 */
  prefillWorkspace?: string | null;
}

export default function TaskParamFill({
  workflowId,
  workflowName,
  nodeCount,
  onBack,
  onTaskStarted,
  selectedNodeIds,
  disabledNodeIds,
  schemeId,
  effectiveSelectedNodeIds,
  prefillValues,
  prefillWorkspace,
}: TaskParamFillProps) {
  // ---- 表单状态 ----
  const [variables, setVariables] = useState<WorkflowVariable[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // ---- 隐藏变量展开状态 ----
  const [expandedHiddenVars, setExpandedHiddenVars] = useState<Set<string>>(new Set());

  // ---- 工作空间覆盖 ----
  const [workspaceOverride, setWorkspaceOverride] = useState("");

  // 重做模式：应用预填的工作空间
  useEffect(() => {
    if (prefillWorkspace) setWorkspaceOverride(prefillWorkspace);
  }, [prefillWorkspace]);

  // ---- 变量引用映射（用于过滤） ----
  const [varRefs, setVarRefs] = useState<Record<string, string[]>>({});

  // 过滤后的变量：仅展示选中节点引用的输入变量（输出变量单独分开展示）
  const { inputVariables, outputVariables } = useMemo(() => {
    let base = variables;
    // 如果有选中的节点和引用映射，按引用过滤
    if (selectedNodeIds && selectedNodeIds.length > 0 && Object.keys(varRefs).length > 0) {
      const selectedSet = new Set(selectedNodeIds);
      base = variables.filter((v) => {
        const refNodes = varRefs[v.key] || [];
        return refNodes.some((nid) => selectedSet.has(nid));
      });
    }
    return {
      inputVariables: base.filter((v) => v.source_type !== "output"),
      outputVariables: base.filter((v) => v.source_type === "output"),
    };
  }, [variables, selectedNodeIds, varRefs]);

  // ---- Main 接管状态（从 WorkflowMainDrawer 回调接收） ----
  const [internalTaskId, setInternalTaskId] = useState<string | null>(null);
  const [internalSessionId, setInternalSessionId] = useState<string | null>(null);
  const [highlightedKey, setHighlightedKey] = useState<string | null>(null);

  // ---- 拖拽分隔线 ----
  const [splitRatio, setSplitRatio] = useState(0.55);
  const [isDraggingSplit, setIsDraggingSplit] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // highlightKey 1.5s 后自动清除
  useEffect(() => {
    if (highlightedKey) {
      const t = setTimeout(() => setHighlightedKey(null), 1500);
      return () => clearTimeout(t);
    }
  }, [highlightedKey]);

  // 加载变量 + 引用映射
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getWorkflowVariables(workflowId),
      getVariableReferences(workflowId).catch(() => ({})),
    ])
      .then(([vars, refs]) => {
        if (!cancelled) {
          const cleanVars = Array.isArray(vars) ? vars : [];
          setVariables(cleanVars);
          setVarRefs(refs || {});
          const initial: Record<string, string> = {};
          for (const v of cleanVars) {
            // 输出变量不纳入用户填写表单
            if (v.source_type === "output") continue;
            // 重做模式：优先使用原任务参数值，否则使用默认值
            if (prefillValues && prefillValues[v.key] !== undefined) {
              initial[v.key] = prefillValues[v.key];
            } else {
              initial[v.key] = v.default || "";
            }
          }
          setValues(initial);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          console.error("加载变量失败:", e);
          setError("加载工作流变量失败");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workflowId, prefillValues]);

  // ---- 表单处理 ----

  const handleValueChange = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setValidationErrors((prev) => {
      const n = { ...prev };
      delete n[key];
      return n;
    });
  };

  const validate = (): boolean => {
    const errs: Record<string, string> = {};
    for (const v of inputVariables)
      if (v.required && !values[v.key]?.trim())
        errs[v.key] = `${v.name} 为必填项`;
    setValidationErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    setSubmitting(true);
    setError(null);

    if (internalTaskId && internalSessionId) {
      // Main 接管模式：更新变量 → 启动任务 → 跳转
      try {
        // 1. 更新变量值
        const updateRes = await fetch(
          `/api/workflows/${workflowId}/tasks/${internalTaskId}/variables`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ parameter_values: values }),
          },
        );
        if (!updateRes.ok) {
          setError("更新参数失败");
          setSubmitting(false);
          return;
        }

        // 2. 启动任务（pre_running → running）
        const { startPreRunningTask } = await import("../../lib/api");
        const startRes = await startPreRunningTask(workflowId, internalTaskId);
        if (!startRes.success) {
          setError(startRes.message || "启动任务失败");
          setSubmitting(false);
          return;
        }

        onTaskStarted(internalTaskId);
      } catch (e) {
        console.error("启动任务失败:", e);
        setError("启动任务失败，请重试");
      } finally {
        setSubmitting(false);
      }
      return;
    }

    // 普通模式：创建任务并启动
    try {
      const { createTask, runTask } = await import("../../lib/api");
      const cr = await createTask(
        workflowId, values, undefined,
        disabledNodeIds, workspaceOverride.trim() || undefined,
        schemeId, effectiveSelectedNodeIds,
      );
      if (!cr?.task_id) {
        setError("创建任务失败");
        setSubmitting(false);
        return;
      }
      const rr = await runTask(workflowId, cr.task_id);
      if (!rr.success) {
        setError(rr.message || "启动任务失败");
        setSubmitting(false);
        return;
      }
      onTaskStarted(cr.task_id);
    } catch (e) {
      console.error("启动任务失败:", e);
      setError("启动任务失败，请重试");
      setSubmitting(false);
    }
  };

  // ---- WorkflowMainDrawer 回调 ----

  const handleMainStarted = useCallback(
    (sessionId: string, taskId: string) => {
      setInternalSessionId(sessionId);
      setInternalTaskId(taskId);
    },
    [],
  );

  const handleVariableUpdate = useCallback((key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setHighlightedKey(key);
  }, []);

  const handleMainAutoNavigate = useCallback(
    (taskId: string) => {
      onTaskStarted(taskId, true); // 工具启动跳转，抽屉默认打开
    },
    [onTaskStarted],
  );

  // ---- 分隔线拖拽 ----

  const handleSplitMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingSplit(true);
  };

  const handleSplitKeyDown = (e: React.KeyboardEvent) => {
    // 左箭头 = 缩小左侧，右箭头 = 扩大左侧
    const step = 0.02;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setSplitRatio((prev) => Math.max(0.2, prev - step));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setSplitRatio((prev) => Math.min(0.8, prev + step));
    }
  };

  useEffect(() => {
    if (!isDraggingSplit) return;
    const handleMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const ratio = x / rect.width;
      // 限制：左侧最小 300px，右侧最小 300px
      const minRatio = 300 / rect.width;
      const maxRatio = 1 - 300 / rect.width;
      setSplitRatio(Math.max(minRatio, Math.min(maxRatio, ratio)));
    };
    const handleMouseUp = () => {
      setIsDraggingSplit(false);
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
  }, [isDraggingSplit]);

  // ---- 渲染工具 ----

  const inputClass =
    "w-full px-3 py-2.5 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-200 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors placeholder-slate-500";
  const selectClass = `${inputClass} appearance-none`;
  const errorInputClass = "border-red-500/50 focus:border-red-500";
  const highlightClass = (key: string) =>
    highlightedKey === key
      ? "ring-2 ring-green-500 transition-all duration-300"
      : "";

  // ============ 渲染 ============

  return (
    <div className="h-[calc(100dvh-3.5rem)] bg-slate-950 flex flex-col">
      {/* Top Bar */}
      <div className="h-12 px-4 bg-slate-900 border-b border-indigo-500/10 flex items-center shrink-0">
        <button
          type="button"
          onClick={onBack}
          aria-label="返回"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-indigo-500/10 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px]"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          返回
        </button>
        <span className="text-sm text-slate-200 ml-4">启动新任务</span>
        {workflowName && (
          <span className="text-xs text-slate-500 ml-3">{workflowName}</span>
        )}
      </div>

      <div className="flex-1 flex min-h-0" ref={containerRef}>
        {/* Left: Form */}
        <div
          className="min-w-0 overflow-y-auto flex items-start justify-center p-6 border-r border-indigo-500/10"
          style={{ width: `${splitRatio * 100}%` }}
        >
          <div className="w-full max-w-md mt-4">
            {/* 工作空间覆盖卡片（独立于任务参数卡片） */}
            <div className="bg-slate-900 border border-indigo-500/10 rounded-2xl overflow-hidden shadow-2xl mb-4">
              <div className="px-6 py-5 border-b border-indigo-500/10">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-lg bg-green-500/10 flex items-center justify-center" aria-hidden="true">
                    <FolderOpen size={14} className="text-green-500" />
                  </div>
                  <div>
                    <h2 className="text-base font-semibold text-slate-200">
                      工作空间
                    </h2>
                    <p className="text-xs text-slate-500">
                      覆盖默认工作空间路径
                    </p>
                  </div>
                </div>
              </div>
              <div className="px-6 py-5">
                <label htmlFor="ws-override" className="text-xs font-medium text-slate-400 mb-1.5 block">
                  覆盖路径
                  <span className="text-xs text-slate-500 font-normal ml-2">（可选）</span>
                </label>
                <input
                  id="ws-override"
                  type="text"
                  value={workspaceOverride}
                  onChange={(e) => setWorkspaceOverride(e.target.value)}
                  placeholder={`data/workspaces/${workflowId}/`}
                  aria-label="覆盖工作空间路径"
                  className="w-full px-3 py-2.5 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-200 text-sm focus:outline-none focus:border-green-500/50 transition-colors placeholder-slate-500 font-mono"
                />
                <p className="text-xs text-slate-500 mt-2">
                  支持绝对路径或相对路径（相对项目根目录）。留空使用默认路径。
                </p>
              </div>
            </div>

            <div className="bg-slate-900 border border-indigo-500/10 rounded-2xl overflow-hidden shadow-2xl">
              <div className="px-6 py-5 border-b border-indigo-500/10">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center" aria-hidden="true">
                    <Variable size={14} className="text-indigo-500" />
                  </div>
                  <div>
                    <h2 className="text-base font-semibold text-slate-200">
                      任务参数
                    </h2>
                    {nodeCount != null && (
                      <p className="text-xs text-slate-500">
                        {nodeCount} 个节点
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="px-6 py-5">
                {loading ? (
                  <div className="flex items-center justify-center py-12 text-slate-500" role="status" aria-label="加载变量中">
                    <Loader size={20} className="animate-spin motion-reduce:animate-none mr-2" aria-hidden="true" />
                    加载变量...
                  </div>
                ) : error && !submitting ? (
                  <div className="flex flex-col items-center justify-center py-8 text-red-500" role="alert" aria-live="polite">
                    <AlertCircle size={24} className="mb-2 opacity-60" aria-hidden="true" />
                    <p className="text-sm">{error}</p>
                    <button
                      type="button"
                      onClick={onBack}
                      className="mt-3 px-4 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-xs transition-colors cursor-pointer min-h-[36px]"
                    >
                      返回
                    </button>
                  </div>
                ) : inputVariables.length === 0 && outputVariables.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-10 text-slate-500">
                    <div className="w-16 h-16 rounded-full bg-indigo-500/5 flex items-center justify-center mb-4" aria-hidden="true">
                      <Variable size={28} className="text-indigo-500/30" />
                    </div>
                    <p className="text-sm font-medium text-slate-400 mb-1">
                      无需填写参数
                    </p>
                    <p className="text-xs text-slate-500">
                      此工作流没有定义参数变量，点击下方按钮直接启动
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* 可见变量 */}
                    {inputVariables.filter(v => !v.hidden).map((v) => (
                      <div key={v.key}>
                        <label htmlFor={`var-${v.key}`} className="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">
                          {v.name}
                          {v.required && (
                            <span className="text-red-500">*</span>
                          )}
                          <span className="text-xs text-slate-500 font-mono ml-2">
                            {`{{${v.key}}}`}
                          </span>
                        </label>
                        {v.type === "select" ? (
                          <select
                            id={`var-${v.key}`}
                            value={values[v.key] || ""}
                            onChange={(e) =>
                              handleValueChange(v.key, e.target.value)
                            }
                            aria-label={v.name}
                            className={`${selectClass} ${
                              validationErrors[v.key] ? errorInputClass : ""
                            } ${highlightClass(v.key)}`}
                          >
                            <option value="">
                              {v.required ? "-- 请选择 --" : "无默认值"}
                            </option>
                            {v.options.map((opt, i) => (
                              <option key={i} value={opt.value}>
                                {opt.name} ({opt.value})
                              </option>
                            ))}
                          </select>
                        ) : v.type === "file" ? (
                          <div>
                            <input
                              id={`var-${v.key}`}
                              type="text"
                              value={values[v.key] || ""}
                              onChange={(e) =>
                                handleValueChange(v.key, e.target.value)
                              }
                              placeholder={v.default || `请输入文件路径`}
                              aria-label={v.name}
                              className={`${inputClass} ${
                                validationErrors[v.key] ? errorInputClass : ""
                              } ${highlightClass(v.key)}`}
                            />
                            <p className="text-xs text-slate-500 mt-1">
                              相对路径基于 workspace: data/workspaces/{workflowId}/<br />
                              以 / 开头则为绝对路径
                            </p>
                          </div>
                        ) : v.type === "textarea" ? (
                          <textarea
                            id={`var-${v.key}`}
                            value={values[v.key] || ""}
                            onChange={(e) =>
                              handleValueChange(v.key, e.target.value)
                            }
                            placeholder={v.default || `请输入${v.name}`}
                            rows={4}
                            aria-label={v.name}
                            className={`${inputClass} resize-y min-h-[80px] ${
                              validationErrors[v.key] ? errorInputClass : ""
                            } ${highlightClass(v.key)}`}
                          />
                        ) : (
                          <input
                            id={`var-${v.key}`}
                            type="text"
                            value={values[v.key] || ""}
                            onChange={(e) =>
                              handleValueChange(v.key, e.target.value)
                            }
                            placeholder={v.default || `请输入${v.name}`}
                            aria-label={v.name}
                            className={`${inputClass} ${
                              validationErrors[v.key] ? errorInputClass : ""
                            } ${highlightClass(v.key)}`}
                          />
                        )}
                        {v.description && (
                          <p className="text-xs text-slate-500 mt-1">
                            {v.description}
                          </p>
                        )}
                        {validationErrors[v.key] && (
                          <p className="text-xs text-red-500 mt-1">
                            {validationErrors[v.key]}
                          </p>
                        )}
                      </div>
                    ))}

                    {/* 隐藏变量（可折叠） */}
                    {inputVariables.filter(v => v.hidden).length > 0 && (
                      <div className="mt-4 pt-4 border-t border-indigo-500/10">
                        <button
                          type="button"
                          aria-expanded={expandedHiddenVars.size > 0}
                          aria-label="展开/折叠隐藏变量"
                          onClick={() => {
                            const hiddenKeys = inputVariables.filter(v => v.hidden).map(v => v.key);
                            const allExpanded = hiddenKeys.every(k => expandedHiddenVars.has(k));
                            if (allExpanded) {
                              setExpandedHiddenVars(new Set());
                            } else {
                              setExpandedHiddenVars(new Set(hiddenKeys));
                            }
                          }}
                          className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 transition-colors mb-3 min-h-[44px] cursor-pointer"
                        >
                          <span className="text-xs text-slate-500">
                            隐藏变量（点击展开）
                          </span>
                          <span className="text-xs text-slate-500">
                            ({inputVariables.filter(v => v.hidden).length})
                          </span>
                        </button>

                        {inputVariables.filter(v => v.hidden).map((v) => {
                          const isExpanded = expandedHiddenVars.has(v.key);
                          return (
                            <div key={v.key} className="mb-3">
                              <button
                                type="button"
                                aria-expanded={isExpanded}
                                onClick={() => {
                                  setExpandedHiddenVars(prev => {
                                    const next = new Set(prev);
                                    if (next.has(v.key)) {
                                      next.delete(v.key);
                                    } else {
                                      next.add(v.key);
                                    }
                                    return next;
                                  });
                                }}
                                className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 transition-colors w-full min-h-[44px] cursor-pointer"
                              >
                                {isExpanded ? (
                                  <ChevronDown size={14} className="text-slate-500" aria-hidden="true" />
                                ) : (
                                  <ChevronRight size={14} className="text-slate-500" aria-hidden="true" />
                                )}
                                <span className="text-sm text-slate-200">{v.name}</span>
                                {v.required && (
                                  <span className="text-red-500">*</span>
                                )}
                                <span className="text-xs text-slate-500 font-mono ml-auto">
                                  {`{{${v.key}}}`}
                                </span>
                              </button>

                              {isExpanded && (
                                <div className="mt-2 ml-4">
                                  {v.type === "select" ? (
                                    <select
                                      value={values[v.key] || ""}
                                      onChange={(e) =>
                                        handleValueChange(v.key, e.target.value)
                                      }
                                      aria-label={v.name}
                                      className={`${selectClass} ${
                                        validationErrors[v.key] ? errorInputClass : ""
                                      } ${highlightClass(v.key)}`}
                                    >
                                      <option value="">
                                        {v.required ? "-- 请选择 --" : "无默认值"}
                                      </option>
                                      {v.options.map((opt, i) => (
                                        <option key={i} value={opt.value}>
                                          {opt.name} ({opt.value})
                                        </option>
                                      ))}
                                    </select>
                                  ) : v.type === "file" ? (
                                    <div>
                                      <input
                                        type="text"
                                        value={values[v.key] || ""}
                                        onChange={(e) =>
                                          handleValueChange(v.key, e.target.value)
                                        }
                                        placeholder={v.default || `请输入文件路径`}
                                        aria-label={v.name}
                                        className={`${inputClass} ${
                                          validationErrors[v.key] ? errorInputClass : ""
                                        } ${highlightClass(v.key)}`}
                                      />
                                      <p className="text-xs text-slate-500 mt-1">
                                        相对路径基于 workspace: data/workspaces/{workflowId}/<br />
                                        以 / 开头则为绝对路径
                                      </p>
                                    </div>
                                  ) : v.type === "textarea" ? (
                                    <textarea
                                      value={values[v.key] || ""}
                                      onChange={(e) =>
                                        handleValueChange(v.key, e.target.value)
                                      }
                                      placeholder={v.default || `请输入${v.name}`}
                                      rows={4}
                                      aria-label={v.name}
                                      className={`${inputClass} resize-y min-h-[80px] ${
                                        validationErrors[v.key] ? errorInputClass : ""
                                      } ${highlightClass(v.key)}`}
                                    />
                                  ) : (
                                    <input
                                      type="text"
                                      value={values[v.key] || ""}
                                      onChange={(e) =>
                                        handleValueChange(v.key, e.target.value)
                                      }
                                      placeholder={v.default || `请输入${v.name}`}
                                      aria-label={v.name}
                                      className={`${inputClass} ${
                                        validationErrors[v.key] ? errorInputClass : ""
                                      } ${highlightClass(v.key)}`}
                                    />
                                  )}
                                  {v.description && (
                                    <p className="text-xs text-slate-500 mt-1">
                                      {v.description}
                                    </p>
                                  )}
                                  {validationErrors[v.key] && (
                                    <p className="text-xs text-red-500 mt-1">
                                      {validationErrors[v.key]}
                                    </p>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {/* 输出变量：只读展示 */}
                    {outputVariables.length > 0 && (
                      <div className="mt-4 pt-4 border-t border-indigo-500/10">
                        <div className="flex items-center gap-1.5 mb-2">
                          <span className="text-xs text-slate-500">
                            输出变量（运行时自动写入）
                          </span>
                        </div>
                        {outputVariables.map((v) => (
                          <div key={v.key} className="mb-2 last:mb-0">
                            <label className="flex items-center gap-1 text-xs text-slate-500 mb-1">
                              {v.name}
                              <span className="text-xs text-indigo-500 font-mono ml-2">
                                {`{{${v.key}}}`}
                              </span>
                            </label>
                          <div className="w-full px-3 py-2 rounded-lg bg-slate-950/40 border border-indigo-500/10 text-sm text-slate-500 font-mono" role="status">
                      (由节点运行时生成)
                            </div>
                            {v.description && (
                              <p className="text-xs text-slate-500 mt-0.5">
                                {v.description}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="px-6 py-4 border-t border-indigo-500/10 bg-slate-950/50">
                {loading ? null : error && !submitting ? null : (
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={submitting}
                    aria-label="启动任务"
                    className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-green-500 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-all cursor-pointer min-h-[44px]"
                  >
                    {submitting ? (
                      <>
                        <Loader size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                        启动中...
                      </>
                    ) : (
                      <>
                        <Play size={16} aria-hidden="true" />
                        启动任务
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
            <p className="text-center text-xs text-slate-500 mt-4 px-6">
              参数值将在任务启动时替换节点中对应的 {"{{key}}"} 占位符。
            </p>
          </div>
        </div>

        {/* Divider Handle */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整左右面板比例"
          tabIndex={0}
          onMouseDown={handleSplitMouseDown}
          onKeyDown={handleSplitKeyDown}
          className={`w-1 shrink-0 cursor-col-resize transition-colors relative z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
            isDraggingSplit
              ? "bg-indigo-500/60"
              : "bg-indigo-500/10 hover:bg-indigo-500/30"
          }`}
        >
          <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 flex items-center opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true">
            <GripVertical size={16} className="text-indigo-500" />
          </div>
        </div>

        {/* Right: WorkflowMainDrawer (inline) */}
        <WorkflowMainDrawer
          mode="inline"
          workflowId={workflowId}
          taskId={internalTaskId}
          mainSessionId={internalSessionId}
          workflowName={workflowName}
          nodeCount={nodeCount}
          onMainStarted={handleMainStarted}
          onVariableUpdate={handleVariableUpdate}
          onTaskStarted={handleMainAutoNavigate}
        />
      </div>
    </div>
  );
}
