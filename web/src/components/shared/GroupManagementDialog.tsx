import { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { X, Edit2, Trash2 } from 'lucide-react';

interface Group {
  id: string;
  name: string;
  description: string;
  skill_ids?: string[];
  rule_ids?: string[];
}

interface GroupManagementDialogProps {
  groups: Group[];
  show: boolean;
  onClose: () => void;
  onSave: (group: { id: string; name: string; description: string }) => Promise<void>;
  onDelete: (groupId: string) => Promise<void>;
  editingGroup: Group | null;
  setEditingGroup: (group: Group | null) => void;
  itemType: 'skill' | 'rule';
}

export function GroupManagementDialog({
  groups,
  show,
  onClose,
  onSave,
  onDelete,
  editingGroup,
  setEditingGroup,
  itemType
}: GroupManagementDialogProps) {
  const [groupForm, setGroupForm] = useState({ id: '', name: '', description: '' });
  const firstInputRef = useRef<HTMLInputElement>(null);

  const openEditGroup = (group: Group) => {
    setEditingGroup(group);
    setGroupForm({ id: group.id, name: group.name, description: group.description });
  };

  const handleSave = async () => {
    await onSave(groupForm);
    setGroupForm({ id: '', name: '', description: '' });
  };

  const handleDelete = async (groupId: string) => {
    if (!confirm(`确定删除组 "${groups.find(g => g.id === groupId)?.name}" 吗？\n组内的 ${itemType} 不会被删除，但会失去所属关系。`)) return;
    await onDelete(groupId);
  };

  // Escape 键关闭和焦点管理
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    if (show) {
      document.addEventListener('keydown', handleKeyDown);
      // 设置焦点到第一个输入框
      setTimeout(() => {
        firstInputRef.current?.focus();
      }, 100);
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [show, onClose]);

  if (!show) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="group-dialog-title"
    >
      <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[500px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 id="group-dialog-title" className="text-lg font-semibold text-slate-200">
            管理{itemType === 'skill' ? '技能' : '规则'}组
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-muted-foreground hover:text-foreground cursor-pointer"
            aria-label="关闭对话框"
          >
            <X size={16} />
          </button>
        </div>

        {/* 已有组列表 */}
        <div className="space-y-2 mb-4">
          {groups.map(group => (
            <div key={group.id} className="flex items-center justify-between p-3 bg-slate-800/60 rounded-lg border border-border/30">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-slate-200">{group.name}</div>
                <div className="text-xs text-muted-foreground truncate">{group.description || '无描述'}</div>
              </div>
              <div className="flex gap-1">
                <button
                  onClick={() => openEditGroup(group)}
                  className="p-1.5 text-muted-foreground hover:text-indigo-500 transition-colors cursor-pointer"
                  title="编辑"
                  aria-label={`编辑组 ${group.name}`}
                >
                  <Edit2 size={12} />
                </button>
                <button
                  onClick={() => handleDelete(group.id)}
                  className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors cursor-pointer"
                  title="删除"
                  aria-label={`删除组 ${group.name}`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
          {groups.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">暂无组，请创建</p>
          )}
        </div>

        {/* 创建/编辑表单 */}
        <div className="border-t border-border/30 pt-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">{editingGroup ? '编辑组' : '新建组'}</h3>
          <div className="space-y-3">
            <div>
              <label htmlFor="group-id" className="text-xs text-muted-foreground block mb-1">组 ID</label>
              <input
                ref={firstInputRef}
                id="group-id"
                value={groupForm.id}
                onChange={e => setGroupForm(p => ({ ...p, id: e.target.value }))}
                disabled={!!editingGroup}
                placeholder="unique-group-id"
                className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
              />
            </div>
            <div>
              <label htmlFor="group-name" className="text-xs text-muted-foreground block mb-1">组名称</label>
              <input
                id="group-name"
                value={groupForm.name}
                onChange={e => setGroupForm(p => ({ ...p, name: e.target.value }))}
                placeholder={`我的${itemType === 'skill' ? '技能' : '规则'}组`}
                className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
              />
            </div>
            <div>
              <label htmlFor="group-description" className="text-xs text-muted-foreground block mb-1">描述</label>
              <input
                id="group-description"
                value={groupForm.description}
                onChange={e => setGroupForm(p => ({ ...p, description: e.target.value }))}
                placeholder="可选描述"
                className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
              <Button size="sm" onClick={handleSave} disabled={!groupForm.id || !groupForm.name}>
                {editingGroup ? '保存修改' : '创建'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
