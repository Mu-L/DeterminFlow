/**
 * ExecutionSchemeDrawer - 执行方案抽屉组件
 *
 * 在节点选择页面的右侧展示：
 *   - 方案列表（可应用/删除）
 *   - 将当前勾选保存为方案
 *   - 当前应用的方案高亮
 */
import { useState } from "react";
import { Check, Plus, Trash2, X, Save, Play } from "lucide-react";
import type { ExecutionScheme } from "../../types";
import { getSchemes, createScheme, deleteScheme } from "../../lib/api";

interface Props {
  workflowId: string;
  schemes: ExecutionScheme[];
  onSchemesChange: (schemes: ExecutionScheme[]) => void;
  /** 当前选中的节点 ID 数组 */
  selectedNodeIds: string[];
  /** 应用方案：将画布选择设置为方案中的节点 */
  onApplyScheme: (scheme: ExecutionScheme) => void;
  /** 当前活动的方案 ID（选择方案后未手动修改时有效） */
  activeSchemeId: string | null;
  /** 所有业务节点 ID */
  allNodeIds: string[];
  /** 是否折叠 */
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** 当前选中的节点数量 */
  selectedCount: number;
}

export default function ExecutionSchemeDrawer({
  workflowId,
  schemes,
  onSchemesChange,
  selectedNodeIds,
  onApplyScheme,
  activeSchemeId,
  allNodeIds,
  collapsed,
  onToggleCollapse,
  selectedCount,
}: Props) {
  const [saving, setSaving] = useState(false);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState("");

  async function handleSaveAsScheme() {
    if (!newName.trim()) {
      return;
    }
    if (selectedNodeIds.length === 0) {
      alert("当前没有选中任何节点，请先在画布上勾选至少一个节点后再保存方案");
      return;
    }
    setSaving(true);
    try {
      const created = await createScheme(workflowId, newName.trim(), selectedNodeIds);
      const updated = await getSchemes(workflowId);
      onSchemesChange(updated);
      onApplyScheme(created);
      setNewName("");
      setShowNewForm(false);
    } catch (e: unknown) {
      console.error("[ExecutionSchemeDrawer] 保存方案失败:", e);
      alert("保存方案失败: " + (e instanceof Error ? e.message : "未知错误"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(schemeId: string) {
    if (!window.confirm("确认删除该执行方案？")) return;
    try {
      await deleteScheme(workflowId, schemeId);
      const updated = await getSchemes(workflowId);
      onSchemesChange(updated);
    } catch (e: unknown) {
      alert("删除方案失败: " + (e instanceof Error ? e.message : "未知错误"));
    }
  }

  const canSave = selectedNodeIds.length > 0;

  // 折叠状态：只显示展开按钮
  if (collapsed) {
    return (
      <div className="w-10 h-full flex flex-col items-center pt-3 border-l border-white/5 bg-slate-950 shrink-0">
        <button
          onClick={onToggleCollapse}
          aria-label="展开执行方案面板"
          title="执行方案"
          className="w-7 h-7 flex items-center justify-center rounded hover:bg-slate-800 text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
        >
          <Play size={14} />
        </button>
        {schemes.length > 0 && (
          <span className="text-[10px] text-indigo-400 mt-1">{schemes.length}</span>
        )}
      </div>
    );
  }

  return (
    <div className="w-64 flex flex-col border-l border-white/5 bg-slate-950 shrink-0 h-full">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-white/5">
        <h3 className="text-xs font-medium text-slate-300">执行方案</h3>
        <button
          onClick={onToggleCollapse}
          aria-label="折叠执行方案面板"
          className="w-6 h-6 flex items-center justify-center rounded hover:bg-slate-800 text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
        >
          <X size={14} />
        </button>
      </div>

      {/* 保存为新方案 */}
      <div className="px-3 py-2 border-b border-white/5">
        {showNewForm ? (
          <div className="flex gap-1.5">
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleSaveAsScheme(); if (e.key === "Escape") { setShowNewForm(false); setNewName(""); } }}
              placeholder="方案名称..."
              autoFocus
              className="flex-1 h-7 px-2 text-xs bg-slate-800 border border-slate-700 rounded focus:outline-none focus:border-indigo-500 text-slate-100"
            />
            <button
              onClick={handleSaveAsScheme}
              disabled={saving || !newName.trim() || !canSave}
              aria-label="确认保存"
              className="w-7 h-7 flex items-center justify-center rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white cursor-pointer"
            >
              <Save size={12} />
            </button>
            <button
              onClick={() => { setShowNewForm(false); setNewName(""); }}
              aria-label="取消"
              className="w-7 h-7 flex items-center justify-center rounded hover:bg-slate-800 text-slate-400 cursor-pointer"
            >
              <X size={12} />
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowNewForm(true)}
            disabled={!canSave}
            className="flex items-center gap-1.5 w-full h-7 px-2 text-xs text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 rounded transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Plus size={12} />
            将当前勾选保存为方案
          </button>
        )}
      </div>

      {/* 方案列表 */}
      <div className="flex-1 overflow-auto">
        {schemes.length === 0 ? (
          <div className="px-3 py-6 text-xs text-slate-500 text-center">
            暂无执行方案，勾选节点后可保存
          </div>
        ) : (
          schemes.map(scheme => {
            const isActive = scheme.id === activeSchemeId;
            const schemeSelectedCount = scheme.selected_node_ids.length;
            return (
              <div
                key={scheme.id}
                className={`flex items-center gap-2 px-3 py-2 border-b border-white/[0.03] transition-colors ${
                  isActive ? "bg-indigo-500/10" : "hover:bg-slate-800/30"
                }`}
              >
                <button
                  onClick={() => onApplyScheme(scheme)}
                  aria-label={`应用方案: ${scheme.name}`}
                  title={`${scheme.name} (${schemeSelectedCount} 个节点)`}
                  className="flex-1 flex items-center gap-2 min-w-0 text-left cursor-pointer"
                >
                  {isActive ? (
                    <Check size={14} className="text-indigo-400 shrink-0" />
                  ) : (
                    <Play size={14} className="text-slate-500 shrink-0" />
                  )}
                  <div className="min-w-0">
                    <div className="text-xs text-slate-200 truncate">{scheme.name}</div>
                    <div className="text-[10px] text-slate-500">{schemeSelectedCount} 个节点</div>
                  </div>
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDelete(scheme.id); }}
                  aria-label={`删除方案: ${scheme.name}`}
                  className="w-5 h-5 flex items-center justify-center rounded hover:bg-red-500/10 text-slate-600 hover:text-red-400 transition-colors cursor-pointer"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* 底部提示 */}
      <div className="px-3 py-1.5 border-t border-white/5 text-[10px] text-slate-600">
        选中 {selectedCount}/{allNodeIds.length} 个节点
      </div>
    </div>
  );
}
