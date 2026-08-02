import { useState, useEffect, useRef } from "react";
import { X, Edit2, Trash2, Check, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ToolInfo, ToolGroup } from "../../types";
import { createToolGroup, updateToolGroup, deleteToolGroup } from "../../lib/api";

interface Props {
  tools: ToolInfo[];
  groups: ToolGroup[];
  open: boolean;
  onClose: () => void;
  onGroupsChange: () => void;
}

export default function ToolGroupEditor({ tools, groups, open, onClose, onGroupsChange }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [formId, setFormId] = useState("");
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // Reset form when dialog opens + auto-focus
  useEffect(() => {
    if (open) {
      setEditingId(null);
      setEditingName("");
      setFormId("");
      setFormName("");
      setFormDesc("");
      setError(null);
      setTimeout(() => closeBtnRef.current?.focus(), 50);
    }
  }, [open]);

  const getToolCount = (groupId: string) => tools.filter((t) => t.group_id === groupId).length;

  const startEdit = (id: string, name: string) => {
    setEditingId(id);
    setEditingName(name);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingName("");
  };

  const saveEdit = async (id: string) => {
    if (!editingName.trim()) return;
    setSaving(true);
    try {
      await updateToolGroup(id, { name: editingName.trim() });
      setEditingId(null);
      setEditingName("");
      onGroupsChange();
    } catch (e) {
      console.error("Failed to update group:", e);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    const count = getToolCount(id);
    if (count > 0) return; // should be disabled, but safety check

    setSaving(true);
    setError(null);
    try {
      await deleteToolGroup(id);
      onGroupsChange();
    } catch (e) {
      setError(`删除失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  const handleCreate = async () => {
    if (!formId.trim() || !formName.trim()) return;

    setSaving(true);
    setError(null);
    try {
      await createToolGroup({ id: formId.trim(), name: formName.trim(), description: formDesc.trim() });
      setFormId("");
      setFormName("");
      setFormDesc("");
      onGroupsChange();
    } catch (e) {
      setError(`创建失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div role="presentation" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose} onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="管理工具分组"
        className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[520px] max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-200">管理工具分组</h2>
          <button ref={closeBtnRef} type="button" onClick={onClose} aria-label="关闭" className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none">
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Error */}
        {error && (
          <div role="alert" className="mb-3 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-xs text-red-500">
            {error}
          </div>
        )}

        {/* Existing Groups */}
        <div className="space-y-2 mb-4">
          <p className="text-xs text-muted-foreground mb-1">{groups.length} 个分组</p>
          {groups.map((group) => {
            const toolCount = getToolCount(group.id);
            const isEditing = editingId === group.id;
            return (
              <div key={group.id} className="flex items-center justify-between p-3 bg-slate-800/60 rounded-lg border border-border/30">
                <div className="flex-1 min-w-0">
                  {isEditing ? (
                    <div className="flex items-center gap-2">
                      <input
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        className="flex-1 bg-slate-900/60 border border-indigo-500/50 rounded px-2 py-1 text-xs text-slate-200 outline-none"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveEdit(group.id);
                          if (e.key === "Escape") cancelEdit();
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => saveEdit(group.id)}
                        disabled={saving || !editingName.trim()}
                        aria-label="确认重命名"
                        className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-green-500 hover:text-green-500/80 cursor-pointer disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-green-500/30 focus-visible:outline-none"
                      >
                        <Check size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={cancelEdit}
                        aria-label="取消重命名"
                        className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                      >
                        <X size={14} aria-hidden="true" />
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="text-xs font-medium text-slate-200">{group.name}</div>
                      <div className="text-xs text-muted-foreground truncate">{group.description || "无描述"}</div>
                    </>
                  )}
                </div>
                <div className="flex items-center gap-2 ml-3">
                  <Badge
                    variant="outline"
                    className={`text-xs ${toolCount > 0 ? "text-cyan-400 border-cyan-500/30" : "text-muted-foreground border-muted-foreground/30"}`}
                  >
                    {toolCount} 个工具
                  </Badge>
                  {!isEditing && (
                    <button
                      type="button"
                      onClick={() => startEdit(group.id, group.name)}
                      aria-label={`编辑 ${group.name}`}
                      className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-indigo-500 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                    >
                      <Edit2 size={12} aria-hidden="true" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleDelete(group.id)}
                    disabled={toolCount > 0 || saving}
                    aria-label={toolCount > 0 ? `无法删除 ${group.name}，组内有 ${toolCount} 个工具` : `删除 ${group.name}`}
                    className={`p-1 min-w-[44px] min-h-[44px] flex items-center justify-center transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-red-500/30 focus-visible:outline-none ${
                      toolCount > 0
                        ? "text-muted-foreground/30 cursor-not-allowed"
                        : "text-muted-foreground hover:text-red-500"
                    }`}
                  >
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                </div>
              </div>
            );
          })}
          {groups.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-4">暂无分组，请创建</p>
          )}
        </div>

        {/* Create Form */}
        <div className="border-t border-border/30 pt-4">
          <h3 className="text-xs font-medium text-slate-300 mb-3 flex items-center gap-1">
            <Plus size={12} className="text-amber-500" aria-hidden="true" />
            新建分组
          </h3>
          <div className="space-y-2.5">
            <div className="flex gap-2">
              <div className="flex-1">
                <label htmlFor="tge-form-id" className="text-xs text-muted-foreground block mb-1">组 ID</label>
                <input
                  id="tge-form-id"
                  value={formId}
                  onChange={(e) => setFormId(e.target.value)}
                  placeholder="unique-group-id"
                  className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                />
              </div>
              <div className="flex-[2]">
                <label htmlFor="tge-form-name" className="text-xs text-muted-foreground block mb-1">组名称</label>
                <input
                  id="tge-form-name"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="分组名称"
                  className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                />
              </div>
            </div>
            <div>
              <label htmlFor="tge-form-desc" className="text-xs text-muted-foreground block mb-1">描述（可选）</label>
              <input
                id="tge-form-desc"
                value={formDesc}
                onChange={(e) => setFormDesc(e.target.value)}
                placeholder="简短描述"
                className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
              />
            </div>
            <div className="flex justify-end pt-1">
              <button
                type="button"
                onClick={handleCreate}
                disabled={saving || !formId.trim() || !formName.trim()}
                aria-label="添加新分组"
                className="flex items-center gap-1 px-3 py-1.5 min-h-[44px] text-xs rounded-md bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none"
              >
                <Plus size={12} aria-hidden="true" />
                {saving ? "创建中..." : "添加分组"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
