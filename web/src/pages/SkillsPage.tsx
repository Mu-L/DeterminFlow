import { useState, useEffect, useRef, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { BookOpen, Code, Search, MessageSquare, Brain, Workflow, GraduationCap, RefreshCw, Eye, Power, PowerOff, Edit2, Check, X, Layers, Trash2, Loader2, AlertCircle, Zap, Snowflake } from 'lucide-react';
import { SkillGroup } from '../types';
import { fetchSkillGroups, createSkillGroup, updateSkillGroup, deleteSkillGroup, setSkillGroups } from '../lib/api';
import { useToast } from '@/components/ui/use-toast';

interface Skill {
  id: string;
  name: string;
  description: string;
  content: string;
  category: string;
  agent_types: string[];
  group_ids?: string[];
  priority: number;
  tags: string[];
  enabled: boolean;
  workflow_only: boolean;
  version: string;
  author: string;
  auto_inject: boolean;
  config?: {
    group_ids?: string[];
  };
}

interface Stats {
  total: number;
  enabled: number;
  disabled: number;
}

const categoryIcons: Record<string, typeof BookOpen> = {
  general: BookOpen, coding: Code, research: Search, communication: MessageSquare,
  memory: Brain, workflow: Workflow, domain: GraduationCap,
};

export default function SkillsPage() {
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  // 组管理状态
  const [groups, setGroups] = useState<SkillGroup[]>([]);
  const [showGroupDialog, setShowGroupDialog] = useState(false);
  const [editingGroup, setEditingGroup] = useState<SkillGroup | null>(null);
  const [groupForm, setGroupForm] = useState({ id: '', name: '', description: '' });

  // skill 组分配编辑状态
  const [isEditingGroups, setIsEditingGroups] = useState(false);
  const [editingGroupIds, setEditingGroupIds] = useState<string[]>([]);

  // 自定义确认对话框状态
  const [confirmDialog, setConfirmDialog] = useState<{ open: boolean; title: string; message: string; onConfirm: () => void }>({ open: false, title: '', message: '', onConfirm: () => {} });
  const groupIdInputRef = useRef<HTMLInputElement>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [isReloading, setIsReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadGroups = useCallback(async () => {
    try {
      const res = await fetchSkillGroups();
      setGroups(res.groups);
    } catch (error) {
      console.error('Failed to load skill groups:', error);
    }
  }, []);

  const loadSkills = useCallback(async () => {
    try {
      const res = await fetch('/api/skills/summary');
      const data = await res.json();
      setSkills(data.skills || []);
    } catch (error) {
      console.error('Failed to load skills:', error);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const res = await fetch('/api/skills/stats');
      setStats(await res.json());
    } catch (error) {
      console.error('Failed to load stats:', error);
    }
  }, []);

  const loadInitialData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      await Promise.all([loadSkills(), loadStats(), loadGroups()]);
    } catch (err) {
      setError('加载数据失败，请稍后重试');
      console.error('Failed to load initial data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [loadGroups, loadSkills, loadStats]);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  const loadDetail = async (id: string) => {
    const res = await fetch(`/api/skills/${id}`);
    const detail = await res.json();
    // 从config读取group_ids
    if (detail.config?.group_ids) {
      detail.group_ids = detail.config.group_ids;
    }
    setSelectedSkill(detail);
  };

  const toggleSkill = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/toggle?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      loadStats();
      if (selectedSkill?.id === id) await loadDetail(id);
    } catch (error) {
      console.error('Error toggling skill:', error);
    }
  };

  const toggleAutoInject = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/auto-inject?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      if (selectedSkill?.id === id) await loadDetail(id);
    } catch (error) {
      console.error('Error toggling auto-inject:', error);
    }
  };

  const toggleWorkflowOnly = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/workflow-only?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      if (selectedSkill?.id === id) await loadDetail(id);
    } catch (error) {
      console.error('Error toggling workflow-only:', error);
    }
  };

  // === 组管理对话框 ===
  const openCreateGroup = () => {
    setEditingGroup(null);
    setGroupForm({ id: '', name: '', description: '' });
    setShowGroupDialog(true);
  };

  const openEditGroup = (group: SkillGroup) => {
    setEditingGroup(group);
    setGroupForm({ id: group.id, name: group.name, description: group.description });
    setShowGroupDialog(true);
  };

  const saveGroup = async () => {
    try {
      if (editingGroup) {
        await updateSkillGroup(editingGroup.id, { name: groupForm.name, description: groupForm.description });
        toast({
          title: "组已更新",
          description: `已更新组 "${groupForm.name}"`,
        });
      } else {
        await createSkillGroup({ id: groupForm.id, name: groupForm.name, description: groupForm.description });
        toast({
          title: "组已创建",
          description: `已创建新组 "${groupForm.name}"`,
        });
      }
      setShowGroupDialog(false);
      await loadGroups();
    } catch (error) {
      console.error('Failed to save group:', error);
      toast({
        title: "保存失败",
        description: "无法保存组，请重试",
        variant: "destructive",
      });
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    const groupName = groups.find(g => g.id === groupId)?.name || groupId;
    setConfirmDialog({
      open: true,
      title: '删除技能组',
      message: `确定删除组 "${groupName}" 吗？组内的 skill 不会被删除，但会失去所属关系。`,
      onConfirm: async () => {
        try {
          await deleteSkillGroup(groupId);
          await loadGroups();
          toast({ title: "组已删除", description: `已删除组 "${groupName}"` });
        } catch (error) {
          console.error('Failed to delete group:', error);
          toast({ title: "删除失败", description: "无法删除组，请重试", variant: "destructive" });
        }
        setConfirmDialog(prev => ({ ...prev, open: false }));
      },
    });
  };

  // === Skill 组分配 ===
  const startEditingGroups = () => {
    setEditingGroupIds(selectedSkill?.group_ids || selectedSkill?.config?.group_ids || []);
    setIsEditingGroups(true);
  };

  const cancelEditingGroups = () => {
    setIsEditingGroups(false);
    setEditingGroupIds([]);
  };

  const saveSkillGroups = async () => {
    if (!selectedSkill) return;
    try {
      await setSkillGroups(selectedSkill.id, editingGroupIds);
      setIsEditingGroups(false);
      await loadSkills();
      await loadDetail(selectedSkill.id);
      toast({
        title: "组分配已保存",
        description: `已更新 ${selectedSkill.name} 的组分配`,
      });
    } catch (error) {
      console.error('Error saving skill groups:', error);
      toast({
        title: "保存失败",
        description: "无法保存组分配，请重试",
        variant: "destructive",
      });
    }
  };

  const toggleGroup = (groupId: string) => {
    setEditingGroupIds(prev =>
      prev.includes(groupId) ? prev.filter(g => g !== groupId) : [...prev, groupId]
    );
  };

  const getSkillGroupNames = () => {
    const gids = selectedSkill?.group_ids || selectedSkill?.config?.group_ids || [];
    return groups.filter(g => gids.includes(g.id));
  };

  // Escape 关闭对话框 + auto-focus
  useEffect(() => {
    if (!showGroupDialog) return;
    const timer = setTimeout(() => groupIdInputRef.current?.focus(), 50);
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowGroupDialog(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => { clearTimeout(timer); document.removeEventListener('keydown', handleKeyDown); };
  }, [showGroupDialog]);

  if (isLoading) {
    return (
      <div className="container mx-auto p-6 flex items-center justify-center h-[600px]" role="status">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin motion-reduce:animate-none mx-auto mb-4 text-primary" aria-hidden="true" />
          <p className="text-muted-foreground sr-only">加载技能数据中...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto p-6 flex items-center justify-center h-[600px]">
        <div className="text-center" role="alert">
          <AlertCircle className="w-8 h-8 mx-auto mb-4 text-destructive" aria-hidden="true" />
          <p className="text-destructive mb-4">{error}</p>
          <Button onClick={loadInitialData} variant="outline" aria-label="重试加载">
            <RefreshCw className="w-4 h-4 mr-2" aria-hidden="true" />重试
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6" role="main" aria-label="技能管理页面">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Skills 管理</h1>
          <p className="text-muted-foreground">管理可复用的知识和行为模块</p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" onClick={() => { setShowGroupDialog(true); openCreateGroup(); }} aria-label="管理技能组" className="focus-visible:ring-2 focus-visible:ring-primary/50">
            <Layers className="w-4 h-4 mr-2" aria-hidden="true" />管理组
          </Button>
          <Button type="button" onClick={async () => { setIsReloading(true); try { await fetch('/api/skills/reload', { method: 'POST' }); await Promise.all([loadSkills(), loadStats()]); toast({ title: "重新加载完成" }); } catch { toast({ title: "重新加载失败", variant: "destructive" }); } finally { setIsReloading(false); } }} disabled={isReloading} aria-label="重新加载技能" className="focus-visible:ring-2 focus-visible:ring-primary/50">
            <RefreshCw className={`w-4 h-4 mr-2 ${isReloading ? 'animate-spin motion-reduce:animate-none' : ''}`} aria-hidden="true" />重新加载
          </Button>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-3 gap-4" role="region" aria-label="技能统计">
          <Card><CardHeader className="pb-3"><CardTitle className="text-sm">总计</CardTitle></CardHeader>
            <CardContent><div className="text-2xl font-bold tabular-nums">{stats.total}</div></CardContent></Card>
          <Card><CardHeader className="pb-3"><CardTitle className="text-sm">已启用</CardTitle></CardHeader>
            <CardContent><div className="text-2xl font-bold text-emerald-500 tabular-nums">{stats.enabled}</div></CardContent></Card>
          <Card><CardHeader className="pb-3"><CardTitle className="text-sm">已禁用</CardTitle></CardHeader>
            <CardContent><div className="text-2xl font-bold text-slate-400 tabular-nums">{stats.disabled}</div></CardContent></Card>
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        <Card>
          <CardHeader><CardTitle>Skills 列表</CardTitle></CardHeader>
          <CardContent>
            <ScrollArea className="h-[600px]">
              <div className="space-y-2">
                {skills.map((skill) => {
                  const Icon = categoryIcons[skill.category] || BookOpen;
                  const skillGroupIds = skill.group_ids || skill.config?.group_ids || [];
                  return (
                    <div key={skill.id} className={`p-3 rounded-lg border cursor-pointer ${selectedSkill?.id === skill.id ? 'border-primary bg-primary/5' : 'hover:bg-accent'}`}
                      onClick={() => loadDetail(skill.id)}
                      role="button"
                      tabIndex={0}
                      aria-label={`查看技能 ${skill.name}`}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); loadDetail(skill.id); } }}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex items-start gap-2 flex-1">
                          <Icon className="w-5 h-5 mt-0.5" />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-sm flex items-center gap-2">
                              {skill.name}
                              {skill.auto_inject && <Zap className="w-3 h-3 text-amber-400" aria-hidden="true" />}
                            </div>
                            <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{skill.description}</div>
                            <div className="flex gap-1 mt-2 flex-wrap">
                              <Badge variant="outline" className="text-xs">P{skill.priority}</Badge>
                              {skillGroupIds.length > 0 && (
                                <Badge variant="secondary" className="text-xs">{skillGroupIds.length} 个组</Badge>
                              )}
                            </div>
                          </div>
                        </div>
                        {skill.enabled ? <><Power className="w-4 h-4 text-emerald-500" aria-hidden="true" /><span className="sr-only">已启用</span></> : <><PowerOff className="w-4 h-4 text-slate-400" aria-hidden="true" /><span className="sr-only">已禁用</span></>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <div className="col-span-2">
          {selectedSkill ? (
            <Card>
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div><CardTitle>{selectedSkill.name}</CardTitle><CardDescription>{selectedSkill.description}</CardDescription></div>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => toggleAutoInject(selectedSkill.id, !selectedSkill.auto_inject)} aria-label={selectedSkill.auto_inject ? '关闭自动注入' : '开启自动注入'} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                      {selectedSkill.auto_inject ? <><Zap className="w-4 h-4 mr-2 text-amber-400" aria-hidden="true" />自动注入</> : <><Snowflake className="w-4 h-4 mr-2" aria-hidden="true" />手动获取</>}
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => toggleWorkflowOnly(selectedSkill.id, !selectedSkill.workflow_only)} aria-label={selectedSkill.workflow_only ? '设为通用' : '设为工作流专属'} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                      {selectedSkill.workflow_only ? <><Workflow className="w-4 h-4 mr-2 text-purple-400" aria-hidden="true" />工作流专属</> : <><Workflow className="w-4 h-4 mr-2" aria-hidden="true" />通用</>}
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => toggleSkill(selectedSkill.id, !selectedSkill.enabled)} aria-label={selectedSkill.enabled ? '禁用技能' : '启用技能'} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                      {selectedSkill.enabled ? <><PowerOff className="w-4 h-4 mr-2" aria-hidden="true" />禁用</> : <><Power className="w-4 h-4 mr-2" aria-hidden="true" />启用</>}
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4 p-4 bg-muted rounded-lg text-sm">
                  <div><div className="font-medium">分类</div><div className="text-muted-foreground">{selectedSkill.category}</div></div>
                  <div><div className="font-medium">优先级</div><div className="text-muted-foreground">{selectedSkill.priority}</div></div>
                  <div><div className="font-medium">版本</div><div className="text-muted-foreground">{selectedSkill.version}</div></div>
                  <div><div className="font-medium">作者</div><div className="text-muted-foreground">{selectedSkill.author || '未知'}</div></div>
                  <div><div className="font-medium">自动注入</div><div className="text-muted-foreground">{selectedSkill.auto_inject ? '是' : '否'}</div></div>
                  <div><div className="font-medium">工作流专属</div><div className="text-muted-foreground">{selectedSkill.workflow_only ? '是' : '否'}</div></div>
                  <div><div className="font-medium">状态</div><div className="text-muted-foreground flex items-center gap-1.5"><span className={`w-2 h-2 rounded-full ${selectedSkill.enabled ? 'bg-emerald-500' : 'bg-slate-500'}`} aria-hidden="true" />{selectedSkill.enabled ? '已启用' : '已禁用'}</div></div>
                </div>

                {/* 组配置 (替代 agent_types) */}
                <div>
                  <div className="text-sm font-medium mb-2 flex items-center justify-between">
                    <span>所属组</span>
                    {!isEditingGroups ? (
                      <Button type="button" variant="ghost" size="sm" onClick={startEditingGroups} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                        <Edit2 className="w-3 h-3 mr-1" />编辑
                      </Button>
                    ) : (
                      <div className="flex gap-2">
                        <Button type="button" variant="ghost" size="sm" onClick={cancelEditingGroups} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                          <X className="w-3 h-3 mr-1" />取消
                        </Button>
                        <Button type="button" variant="default" size="sm" onClick={saveSkillGroups} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                          <Check className="w-3 h-3 mr-1" />保存
                        </Button>
                      </div>
                    )}
                  </div>
                  {!isEditingGroups ? (
                    <div className="flex flex-wrap gap-2">
                      {getSkillGroupNames().length > 0 ? (
                        getSkillGroupNames().map(g => (
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
                              className="flex items-start gap-2 p-2 border rounded cursor-pointer hover:bg-accent min-h-[44px]"
                            >
                              <input
                                type="checkbox"
                                checked={editingGroupIds.includes(group.id)}
                                onChange={() => toggleGroup(group.id)}
                                aria-label={`将技能分配到组 ${group.name}`}
                                className="mt-1"
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
                      <p className="text-xs text-muted-foreground">
                        Skill 与组是多对多关系。Agent 通过可见的组来间接访问此 Skill。
                      </p>
                    </div>
                  )}
                </div>

                {selectedSkill.tags.length > 0 && (
                  <div>
                    <div className="text-sm font-medium mb-2">标签</div>
                    <div className="flex flex-wrap gap-2">{selectedSkill.tags.map(t => <Badge key={t} variant="outline">{t}</Badge>)}</div>
                  </div>
                )}
                <div>
                  <div className="text-sm font-medium mb-2">内容</div>
                  <ScrollArea className="h-[400px] rounded-md border p-4">
                    <pre className="text-sm whitespace-pre-wrap">{selectedSkill.content}</pre>
                  </ScrollArea>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card><CardContent className="flex items-center justify-center h-[600px]">
              <div className="text-center text-muted-foreground" role="status"><Eye className="w-12 h-12 mx-auto mb-4 opacity-50" aria-hidden="true" /><p>选择一个 Skill 查看详情</p></div>
            </CardContent></Card>
          )}
        </div>
      </div>

      {/* 组管理对话框 */}
      {showGroupDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowGroupDialog(false)}>
          <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[500px] max-h-[80vh] overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="group-dialog-title" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 id="group-dialog-title" className="text-lg font-semibold text-slate-200">管理技能组</h2>
              <button type="button" onClick={() => setShowGroupDialog(false)} className="p-1 text-muted-foreground hover:text-foreground cursor-pointer" aria-label="关闭">
                <X size={16} aria-hidden="true" />
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
                    <button type="button" onClick={() => openEditGroup(group)} className="p-1.5 text-muted-foreground hover:text-indigo-400 transition-colors cursor-pointer min-w-[44px] min-h-[44px] flex items-center justify-center" aria-label={`编辑组 ${group.name}`}>
                      <Edit2 size={14} aria-hidden="true" />
                    </button>
                    <button type="button" onClick={() => handleDeleteGroup(group.id)} className="p-1.5 text-muted-foreground hover:text-red-400 transition-colors cursor-pointer min-w-[44px] min-h-[44px] flex items-center justify-center" aria-label={`删除组 ${group.name}`}>
                      <Trash2 size={14} aria-hidden="true" />
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
                    ref={groupIdInputRef}
                    id="group-id"
                    value={groupForm.id}
                    onChange={e => setGroupForm(p => ({ ...p, id: e.target.value }))}
                    disabled={!!editingGroup}
                    placeholder="unique-group-id"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                  />
                </div>
                <div>
                  <label htmlFor="group-name" className="text-xs text-muted-foreground block mb-1">组名称</label>
                  <input
                    id="group-name"
                    value={groupForm.name}
                    onChange={e => setGroupForm(p => ({ ...p, name: e.target.value }))}
                    placeholder="我的技能组"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                  />
                </div>
                <div>
                  <label htmlFor="group-desc" className="text-xs text-muted-foreground block mb-1">描述</label>
                  <input
                    id="group-desc"
                    value={groupForm.description}
                    onChange={e => setGroupForm(p => ({ ...p, description: e.target.value }))}
                    placeholder="可选描述"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" variant="outline" size="sm" onClick={() => setShowGroupDialog(false)} className="focus-visible:ring-2 focus-visible:ring-primary/50">取消</Button>
                  <Button type="button" size="sm" onClick={saveGroup} disabled={!groupForm.id || !groupForm.name} className="focus-visible:ring-2 focus-visible:ring-primary/50">
                    {editingGroup ? '保存修改' : '创建'}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 自定义确认对话框 */}
      {confirmDialog.open && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60" onClick={() => setConfirmDialog(prev => ({ ...prev, open: false }))}>
          <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[400px]" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" onClick={e => e.stopPropagation()}>
            <h2 id="confirm-dialog-title" className="text-lg font-semibold text-slate-200 mb-2">{confirmDialog.title}</h2>
            <p className="text-sm text-muted-foreground mb-6">{confirmDialog.message}</p>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => setConfirmDialog(prev => ({ ...prev, open: false }))} className="focus-visible:ring-2 focus-visible:ring-primary/50">取消</Button>
              <Button type="button" variant="destructive" size="sm" onClick={confirmDialog.onConfirm} className="focus-visible:ring-2 focus-visible:ring-primary/50">确认删除</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
