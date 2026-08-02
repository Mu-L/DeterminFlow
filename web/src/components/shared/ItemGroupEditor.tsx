import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Edit2, Check, X, Layers, Plus } from 'lucide-react';

interface Group {
  id: string;
  name: string;
  description: string;
}

interface ItemGroupEditorProps {
  groups: Group[];
  selectedGroupIds: string[];
  isEditing: boolean;
  onStartEditing: () => void;
  onCancelEditing: () => void;
  onSave: (groupIds: string[]) => Promise<void>;
  onCreateGroup?: (group: { id: string; name: string; description: string }) => Promise<void>;
  itemType: 'skill' | 'rule';
}

export function ItemGroupEditor({
  groups,
  selectedGroupIds,
  isEditing,
  onStartEditing,
  onCancelEditing,
  onSave,
  onCreateGroup,
  itemType
}: ItemGroupEditorProps) {
  const [editingGroupIds, setEditingGroupIds] = useState<string[]>(selectedGroupIds);
  const [showQuickCreate, setShowQuickCreate] = useState(false);
  const [quickCreateForm, setQuickCreateForm] = useState({ id: '', name: '', description: '' });

  const handleStartEditing = () => {
    setEditingGroupIds(selectedGroupIds);
    onStartEditing();
  };

  const handleCancelEditing = () => {
    setEditingGroupIds([]);
    onCancelEditing();
    setShowQuickCreate(false);
  };

  const handleSave = async () => {
    await onSave(editingGroupIds);
  };

  const toggleGroup = (groupId: string) => {
    setEditingGroupIds(prev =>
      prev.includes(groupId) ? prev.filter(g => g !== groupId) : [...prev, groupId]
    );
  };

  const getGroupNames = () => {
    return groups.filter(g => selectedGroupIds.includes(g.id));
  };

  const handleQuickCreate = async () => {
    if (!onCreateGroup || !quickCreateForm.id || !quickCreateForm.name) return;

    try {
      await onCreateGroup(quickCreateForm);
      // 自动选中新创建的组
      setEditingGroupIds(prev => [...prev, quickCreateForm.id]);
      setQuickCreateForm({ id: '', name: '', description: '' });
      setShowQuickCreate(false);
    } catch (error) {
      console.error('Failed to create group:', error);
    }
  };

  return (
    <div>
      <div className="text-sm font-medium mb-2 flex items-center justify-between">
        <span>所属组</span>
        {!isEditing ? (
          <Button variant="ghost" size="sm" onClick={handleStartEditing}>
            <Edit2 className="w-3 h-3 mr-1" />编辑
          </Button>
        ) : (
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleCancelEditing}>
              <X className="w-3 h-3 mr-1" />取消
            </Button>
            <Button variant="default" size="sm" onClick={handleSave}>
              <Check className="w-3 h-3 mr-1" />保存
            </Button>
          </div>
        )}
      </div>
      {!isEditing ? (
        <div className="flex flex-wrap gap-2">
          {getGroupNames().length > 0 ? (
            getGroupNames().map(g => (
              <Badge key={g.id} variant="secondary" className="flex items-center gap-1">
                <Layers className="w-3 h-3" />{g.name}
              </Badge>
            ))
          ) : (
            <Badge variant="outline">未分配到任何组</Badge>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {groups.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              {groups.map(group => (
                <label
                  key={group.id}
                  className="flex items-start gap-2 p-2 border rounded cursor-pointer hover:bg-accent"
                >
                  <input
                    type="checkbox"
                    checked={editingGroupIds.includes(group.id)}
                    onChange={() => toggleGroup(group.id)}
                    className="mt-1"
                    aria-label={`选择组 ${group.name}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm">{group.name}</div>
                    <div className="text-xs text-muted-foreground line-clamp-1">{group.description || '无描述'}</div>
                  </div>
                </label>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">暂无组，请先点击"管理组"按钮创建</p>
          )}

          {/* 快速创建组 */}
          {onCreateGroup && (
            <div className="border-t border-border/30 pt-3">
              {!showQuickCreate ? (
                <Button variant="ghost" size="sm" onClick={() => setShowQuickCreate(true)}>
                  <Plus className="w-3 h-3 mr-1" />快速创建新组
                </Button>
              ) : (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <input
                      value={quickCreateForm.id}
                      onChange={e => setQuickCreateForm(p => ({ ...p, id: e.target.value }))}
                      placeholder="组ID"
                      className="flex-1 bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                    />
                    <input
                      value={quickCreateForm.name}
                      onChange={e => setQuickCreateForm(p => ({ ...p, name: e.target.value }))}
                      placeholder="组名称"
                      className="flex-1 bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                    />
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button variant="ghost" size="sm" onClick={() => setShowQuickCreate(false)}>
                      取消
                    </Button>
                    <Button size="sm" onClick={handleQuickCreate} disabled={!quickCreateForm.id || !quickCreateForm.name}>
                      创建并选中
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          <p className="text-xs text-muted-foreground">
            {itemType === 'skill' ? 'Skill' : 'Rule'} 与组是多对多关系。Agent 通过可见的组来间接访问此 {itemType === 'skill' ? 'Skill' : 'Rule'}。
          </p>
        </div>
      )}
    </div>
  );
}