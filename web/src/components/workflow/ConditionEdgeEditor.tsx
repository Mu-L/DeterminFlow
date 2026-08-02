/**
 * ConditionEdgeEditor — 条件/循环边编辑弹出面板
 *
 * 点击条件网关或循环网关的出边即可打开。
 * 循环网关模式显示循环语法提示和校验。
 */
import { useState, useEffect, useRef, useMemo } from "react";
import type { Edge } from "reactflow";

const LOOP_PATTERN = /^for\s+\w+(?:\s*,\s*\w+)?\s+in\s+(?:range\(\s*\d+\s*(?:,\s*\d+)?\s*\)|\w+)\s*$/;

interface ConditionEdgeEditorProps {
  edge: Edge<ConditionEdgeData>;
  isLoopGate?: boolean;
  onSave: (edgeId: string, condition: { expression: string; label: string; is_default: boolean } | null) => void;
  onClose: () => void;
}

interface ConditionEdgeData {
  condition?: {
    expression?: string;
    label?: string;
    is_default?: boolean;
  };
}

export default function ConditionEdgeEditor({ edge, isLoopGate, onSave, onClose }: ConditionEdgeEditorProps) {
  const existingCondition = edge.data?.condition || {};
  const [expression, setExpression] = useState(existingCondition.expression || "");
  const [label, setLabel] = useState(existingCondition.label || "");
  const [isDefault, setIsDefault] = useState(existingCondition.is_default || false);
  const labelInputRef = useRef<HTMLInputElement>(null);

  // 循环语法校验
  const loopSyntaxError = useMemo(() => {
    if (!isLoopGate || isDefault || !expression.trim()) return null;
    if (LOOP_PATTERN.test(expression.trim())) return null;
    return "格式错误，应为: for item in list / for key, val in dict / for i in range(N[,M])";
  }, [isLoopGate, isDefault, expression]);

  useEffect(() => { labelInputRef.current?.focus(); }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const handleSave = () => {
    if (!isDefault && !expression.trim()) return;
    if (isLoopGate && !isDefault && loopSyntaxError) return;
    onSave(edge.id, {
      expression: expression.trim(),
      label: label.trim() || expression.trim(),
      is_default: isDefault,
    });
  };

  const handleClear = () => {
    onSave(edge.id, null);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose} role="dialog" aria-modal="true" aria-label="编辑分支条件">
      <div
        className="bg-slate-900 border border-blue-500/30 rounded-xl p-5 w-80 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-slate-100 mb-4">
          编辑分支条件
          <span className="text-xs text-slate-500 ml-2">
            {edge.source} → {edge.target}
          </span>
        </h3>

        {/* Label */}
        <div className="mb-3">
          <label htmlFor="condition-label" className="block text-xs text-slate-400 uppercase mb-1">条件名称</label>
          <input
            ref={labelInputRef}
            type="text"
            id="condition-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="如: 高分分支"
            disabled={isDefault}
            className="w-full px-3 py-2 text-xs bg-slate-950 border border-indigo-500/20 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500/50 disabled:opacity-40"
          />
        </div>

        {/* Expression */}
        <div className="mb-3">
          <label htmlFor="condition-expression" className="block text-xs text-slate-400 uppercase mb-1">
            {isLoopGate ? "循环表达式" : "条件表达式"}
          </label>
          <input
            type="text"
            id="condition-expression"
            value={expression}
            onChange={(e) => setExpression(e.target.value)}
            placeholder={isLoopGate ? "如: for item in chapters" : "如: {{score}} >= 60 AND {{retry}} < 3"}
            disabled={isDefault}
            className={`w-full px-3 py-2 text-xs bg-slate-950 border rounded-lg text-slate-100 placeholder-slate-500 font-mono focus:outline-none disabled:opacity-40 ${
              loopSyntaxError ? "border-red-500/50" : "border-indigo-500/20 focus:border-blue-500/50"
            }`}
          />
          {isLoopGate ? (
            <>
              <p className="text-xs text-emerald-500/70 mt-1">
                列表: for item in chapters ｜ 字典: for key, value in config ｜ Range: for i in range(5)
              </p>
              {!isDefault && loopSyntaxError && (
                <p className="text-xs text-red-500 mt-0.5">{loopSyntaxError}</p>
              )}
              {!isDefault && expression.trim() && !loopSyntaxError && (
                <p className="text-xs text-emerald-500/70 mt-0.5">✓ 语法正确</p>
              )}
            </>
          ) : (
            <p className="text-xs text-slate-500 mt-1">
              运算符: == != {">"} {"<"} {">="} {"<="} ｜ AND OR NOT ｜ ( ) ｜ 变量: {`{{key}}`}
            </p>
          )}
        </div>

        {/* Default */}
        <div className="mb-4 flex items-center gap-2">
          <input
            type="checkbox"
            id="is-default"
            checked={isDefault}
            onChange={(e) => {
              setIsDefault(e.target.checked);
              if (e.target.checked) { setExpression(""); setLabel("默认"); }
            }}
            className="w-3.5 h-3.5 rounded border-indigo-500/30 bg-slate-950 accent-blue-500"
          />
          <label htmlFor="is-default" className="text-xs text-slate-400">
            设为默认分支（所有条件不匹配时走此分支）
          </label>
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={!isDefault && !expression.trim()}
            className="flex-1 px-3 py-2.5 min-h-[44px] text-xs font-medium bg-blue-500 hover:bg-blue-600 disabled:opacity-30 text-white rounded-lg transition-colors cursor-pointer"
          >
            保存
          </button>
          <button
            type="button"
            onClick={handleClear}
            className="px-3 py-2.5 min-h-[44px] text-xs font-medium border border-red-500/30 text-red-500 hover:bg-red-500/10 rounded-lg transition-colors cursor-pointer"
          >
            清除
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2.5 min-h-[44px] text-xs font-medium border border-indigo-500/20 text-slate-400 hover:bg-indigo-500/10 rounded-lg transition-colors cursor-pointer"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
