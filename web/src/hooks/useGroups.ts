import { useState, useEffect, useCallback } from 'react';
import { useToast } from '@/components/ui/use-toast';

interface Group {
  id: string;
  name: string;
  description: string;
  skill_ids?: string[];
  rule_ids?: string[];
}

interface UseGroupsOptions {
  fetchGroups: () => Promise<{ groups: Group[] }>;
  createGroup: (group: { id: string; name: string; description: string }) => Promise<{ success: boolean; group: Group }>;
  updateGroup: (groupId: string, updates: { name?: string; description?: string }) => Promise<{ success: boolean }>;
  deleteGroup: (groupId: string) => Promise<{ success: boolean; message: string }>;
  itemType: 'skill' | 'rule';
}

export function useGroups({
  fetchGroups,
  createGroup,
  updateGroup,
  deleteGroup,
  itemType
}: UseGroupsOptions) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [showGroupDialog, setShowGroupDialog] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const { toast } = useToast();

  const loadGroups = useCallback(async () => {
    try {
      const res = await fetchGroups();
      setGroups(res.groups);
    } catch (error) {
      console.error(`Failed to load ${itemType} groups:`, error);
    }
  }, [fetchGroups, itemType]);

  useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  const handleSaveGroup = useCallback(async (groupForm: { id: string; name: string; description: string }) => {
    try {
      if (editingGroup) {
        await updateGroup(editingGroup.id, { name: groupForm.name, description: groupForm.description });
        toast({
          title: "组已更新",
          description: `已更新组 "${groupForm.name}"`,
        });
      } else {
        await createGroup({ id: groupForm.id, name: groupForm.name, description: groupForm.description });
        toast({
          title: "组已创建",
          description: `已创建新组 "${groupForm.name}"`,
        });
      }
      setShowGroupDialog(false);
      await loadGroups();
    } catch (error) {
      console.error(`Failed to save ${itemType} group:`, error);
      toast({
        title: "保存失败",
        description: "无法保存组，请重试",
        variant: "destructive",
      });
    }
  }, [editingGroup, updateGroup, createGroup, toast, loadGroups, itemType]);

  const handleDeleteGroup = useCallback(async (groupId: string) => {
    try {
      await deleteGroup(groupId);
      await loadGroups();
      toast({
        title: "组已删除",
        description: `已删除组 "${groups.find(g => g.id === groupId)?.name}"`,
      });
    } catch (error) {
      console.error(`Failed to delete ${itemType} group:`, error);
      toast({
        title: "删除失败",
        description: "无法删除组，请重试",
        variant: "destructive",
      });
    }
  }, [deleteGroup, loadGroups, groups, toast, itemType]);

  return {
    groups,
    showGroupDialog,
    setShowGroupDialog,
    editingGroup,
    setEditingGroup,
    loadGroups,
    handleSaveGroup,
    handleDeleteGroup,
  };
}