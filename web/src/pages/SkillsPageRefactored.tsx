import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { BookOpen, Code, Search, MessageSquare, Brain, Workflow, GraduationCap, RefreshCw, Eye, Power, PowerOff, Layers, Loader2, AlertCircle, Zap, Snowflake } from 'lucide-react';
import { fetchSkillGroups, createSkillGroup, updateSkillGroup, deleteSkillGroup, setSkillGroups } from '../lib/api';
import { useToast } from '@/components/ui/use-toast';
import { GroupManagementDialog, ItemGroupEditor } from '@/components/shared';
import { useGroups } from '@/hooks/useGroups';

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

export default function SkillsPageRefactored() {
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  // 使用共享的组管理hook
  const {
    groups,
    showGroupDialog,
    setShowGroupDialog,
    editingGroup,
    setEditingGroup,
    handleSaveGroup,
    handleDeleteGroup,
  } = useGroups({
    fetchGroups: fetchSkillGroups,
    createGroup: createSkillGroup,
    updateGroup: updateSkillGroup,
    deleteGroup: deleteSkillGroup,
    itemType: 'skill',
  });

  // skill 组分配编辑状态
  const [isEditingGroups, setIsEditingGroups] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isReloading, setIsReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      await Promise.all([loadSkills(), loadStats()]);
    } catch (err) {
      setError('加载数据失败，请稍后重试');
      console.error('Failed to load initial data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [loadSkills, loadStats]);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  const loadDetail = async (id: string) => {
    const res = await fetch(`/api/skills/${id}`);
    const detail = await res.json();
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

  const handleSaveSkillGroups = async (groupIds: string[]) => {
    if (!selectedSkill) return;
    try {
      await setSkillGroups(selectedSkill.id, groupIds);
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
          <Button onClick={loadInitialData} variant="outline" aria-label="重试加载" className="focus-visible:ring-2 focus-visible:ring-primary/50">
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
          <Button type="button" variant="outline" onClick={() => setShowGroupDialog(true)} aria-label="管理技能组" className="focus-visible:ring-2 focus-visible:ring-primary/50">
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

                {/* 使用共享的组编辑器 */}
                <ItemGroupEditor
                  groups={groups}
                  selectedGroupIds={selectedSkill.group_ids || selectedSkill.config?.group_ids || []}
                  isEditing={isEditingGroups}
                  onStartEditing={() => setIsEditingGroups(true)}
                  onCancelEditing={() => setIsEditingGroups(false)}
                  onSave={handleSaveSkillGroups}
                  onCreateGroup={handleSaveGroup}
                  itemType="skill"
                />

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

      {/* 使用共享的组管理对话框 */}
      <GroupManagementDialog
        groups={groups}
        show={showGroupDialog}
        onClose={() => setShowGroupDialog(false)}
        onSave={handleSaveGroup}
        onDelete={handleDeleteGroup}
        editingGroup={editingGroup}
        setEditingGroup={setEditingGroup}
        itemType="skill"
      />
    </div>
  );
}
