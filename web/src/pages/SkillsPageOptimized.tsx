import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  BookOpen, Code, Search, MessageSquare, Brain, Workflow, GraduationCap,
  RefreshCw, Eye, Power, PowerOff, Layers,
  Loader2, AlertCircle, Info, Zap, Snowflake
} from 'lucide-react';
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

const categoryLabels: Record<string, string> = {
  general: '通用', coding: '编程', research: '研究', communication: '通信',
  memory: '记忆', workflow: '工作流', domain: '领域',
};

export default function SkillsPageOptimized() {
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

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

  // 过滤技能列表
  const filteredSkills = skills.filter(skill =>
    skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    skill.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
    skill.category.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const loadSkills = useCallback(async () => {
    const res = await fetch('/api/skills/summary');
    const data = await res.json();
    setSkills(data.skills || []);
  }, []);

  const loadStats = useCallback(async () => {
    const res = await fetch('/api/skills/stats');
    setStats(await res.json());
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
    setIsDetailLoading(true);
    try {
      const res = await fetch(`/api/skills/${id}`);
      const detail = await res.json();
      setSelectedSkill(detail);
    } catch (error) {
      console.error('Failed to load skill detail:', error);
      toast({
        title: "加载失败",
        description: "无法加载技能详情",
        variant: "destructive",
      });
    } finally {
      setIsDetailLoading(false);
    }
  };

  const toggleSkill = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/toggle?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      loadStats();
      if (selectedSkill?.id === id) await loadDetail(id);
      toast({
        title: enabled ? "技能已启用" : "技能已禁用",
        description: `已${enabled ? '启用' : '禁用'}技能`,
      });
    } catch (error) {
      console.error('Error toggling skill:', error);
      toast({
        title: "操作失败",
        description: "无法切换技能状态",
        variant: "destructive",
      });
    }
  };

  const toggleAutoInject = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/auto-inject?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      if (selectedSkill?.id === id) await loadDetail(id);
      toast({
        title: enabled ? "已开启自动注入" : "已关闭自动注入",
        description: `已${enabled ? '开启' : '关闭'}自动注入`,
      });
    } catch (error) {
      console.error('Error toggling auto-inject:', error);
      toast({
        title: "操作失败",
        description: "无法切换自动注入状态",
        variant: "destructive",
      });
    }
  };

  const toggleWorkflowOnly = async (id: string, enabled: boolean) => {
    try {
      await fetch(`/api/skills/${id}/workflow-only?enabled=${enabled}`, { method: 'POST' });
      await loadSkills();
      if (selectedSkill?.id === id) await loadDetail(id);
      toast({
        title: enabled ? "已设为工作流专属" : "已设为通用",
        description: `已将技能设为${enabled ? '工作流专属' : '通用'}`,
      });
    } catch (error) {
      console.error('Error toggling workflow-only:', error);
      toast({
        title: "操作失败",
        description: "无法切换工作流专属状态",
        variant: "destructive",
      });
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

  const handleRefresh = async () => {
    await loadInitialData();
    toast({
      title: "刷新完成",
      description: "数据已更新",
    });
  };

  const getCategoryIcon = (category: string) => {
    const Icon = categoryIcons[category] || BookOpen;
    return <Icon className="w-4 h-4" />;
  };

  const getCategoryLabel = (category: string) => {
    return categoryLabels[category] || category;
  };

  const getPriorityColor = (priority: number) => {
    if (priority >= 8) return 'bg-red-500/20 text-red-400 border-red-500/30';
    if (priority >= 5) return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
    return 'bg-green-500/20 text-green-400 border-green-500/30';
  };

  const getGroupNames = (skill: Skill) => {
    const gids = skill.group_ids || skill.config?.group_ids || [];
    return groups.filter(g => gids.includes(g.id));
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
      {/* 头部区域 */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">技能管理</h1>
          <p className="text-muted-foreground text-sm mt-1">
            管理可复用的知识和行为模块，配置 Agent 的能力边界
          </p>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <Button type="button" variant="outline" size="sm" onClick={() => setShowGroupDialog(true)} aria-label="管理技能组" className="focus-visible:ring-2 focus-visible:ring-primary/50">
            <Layers className="w-4 h-4 mr-2" aria-hidden="true" />管理组
          </Button>
          <Button type="button" size="sm" onClick={handleRefresh} aria-label="刷新技能列表" className="focus-visible:ring-2 focus-visible:ring-primary/50">
            <RefreshCw className="w-4 h-4 mr-2" aria-hidden="true" />刷新
          </Button>
        </div>
      </div>

      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Card className="bg-slate-800/50 border-slate-700">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">总计技能</p>
                  <p className="text-2xl font-bold text-white tabular-nums">{stats.total}</p>
                </div>
                <div className="p-2 bg-primary/10 rounded-lg">
                  <Layers className="w-5 h-5 text-primary" aria-hidden="true" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-slate-800/50 border-slate-700">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">已启用</p>
                  <p className="text-2xl font-bold text-emerald-400 tabular-nums">{stats.enabled}</p>
                </div>
                <div className="p-2 bg-emerald-500/10 rounded-lg">
                  <Power className="w-5 h-5 text-emerald-400" aria-hidden="true" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-slate-800/50 border-slate-700">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">已禁用</p>
                  <p className="text-2xl font-bold text-slate-400 tabular-nums">{stats.disabled}</p>
                </div>
                <div className="p-2 bg-slate-500/10 rounded-lg">
                  <PowerOff className="w-5 h-5 text-slate-400" aria-hidden="true" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-slate-800/50 border-slate-700">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">分组数</p>
                  <p className="text-2xl font-bold text-slate-200 tabular-nums">{groups.length}</p>
                </div>
                <div className="p-2 bg-slate-500/10 rounded-lg">
                  <Layers className="w-5 h-5 text-slate-400" aria-hidden="true" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 搜索栏 */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-4 h-4" />
        <input
          type="text"
          placeholder="搜索技能名称、描述或分类..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          aria-label="搜索技能"
          className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50 min-h-[44px]"
        />
      </div>

      {/* 主要内容区域 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧：技能列表 */}
        <Card className="lg:col-span-1 bg-slate-900 border-slate-700">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg">技能列表</CardTitle>
              <Badge variant="secondary" className="text-xs">
                {filteredSkills.length} / {skills.length}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <ScrollArea className="h-[600px]">
              <div className="p-4 space-y-2">
                {filteredSkills.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Info className="w-8 h-8 mx-auto mb-2 opacity-50" aria-hidden="true" />
                    <p className="text-sm">
                      {searchQuery ? '没有匹配的技能' : '暂无技能数据'}
                    </p>
                  </div>
                ) : (
                  filteredSkills.map((skill) => {
                    const skillGroups = getGroupNames(skill);
                    return (
                      <div
                        key={skill.id}
                        className={`p-3 rounded-lg border cursor-pointer transition-all duration-200 ${
                          selectedSkill?.id === skill.id
                            ? 'border-primary bg-primary/10 shadow-lg motion-reduce:shadow-none shadow-primary/10'
                            : 'border-slate-700 hover:border-slate-600 hover:bg-slate-800/50'
                        }`}
                        onClick={() => loadDetail(skill.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            loadDetail(skill.id);
                          }
                        }}
                      >
                        <div className="flex items-start justify-between">
                          <div className="flex items-start gap-3 flex-1 min-w-0">
                            <div className="p-2 bg-slate-800 rounded-lg border border-slate-700">
                              {getCategoryIcon(skill.category)}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <h3 className="font-medium text-sm text-slate-200 truncate">
                                  {skill.name}
                                </h3>
                                {skill.auto_inject && (
                                  <Zap className="w-3 h-3 text-amber-400" aria-hidden="true" />
                                )}
                              </div>
                              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                {skill.description}
                              </p>
                              <div className="flex items-center gap-2 mt-2 flex-wrap">
                                <Badge
                                  variant="outline"
                                  className={`text-xs ${getPriorityColor(skill.priority)}`}
                                >
                                  P{skill.priority}
                                </Badge>
                                <Badge variant="secondary" className="text-xs">
                                  {getCategoryLabel(skill.category)}
                                </Badge>
                                {skillGroups.length > 0 && (
                                  <Badge variant="secondary" className="text-xs">
                                    {skillGroups.length} 组
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2 ml-2">
                            <div
                              className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                skill.enabled ? 'bg-emerald-500' : 'bg-slate-500'
                              }`}
                              title={skill.enabled ? '已启用' : '已禁用'}
                              aria-hidden="true"
                            />
                            <span className="sr-only">{skill.enabled ? '已启用' : '已禁用'}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* 右侧：技能详情 */}
        <div className="lg:col-span-2">
          {selectedSkill ? (
            <Card className="bg-slate-900 border-slate-700">
              <CardHeader className="pb-4">
                <div className="flex flex-col sm:flex-row items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="p-2 bg-primary/10 rounded-lg border border-primary/20">
                        {getCategoryIcon(selectedSkill.category)}
                      </div>
                      <div>
                        <CardTitle className="text-xl text-white">
                          {selectedSkill.name}
                        </CardTitle>
                        <CardDescription className="mt-1">
                          {selectedSkill.description}
                        </CardDescription>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => toggleAutoInject(selectedSkill.id, !selectedSkill.auto_inject)}
                      className={`focus-visible:ring-2 focus-visible:ring-primary/50 ${selectedSkill.auto_inject ? 'border-amber-500/50 text-amber-400' : ''}`}
                      aria-label={selectedSkill.auto_inject ? '关闭自动注入' : '开启自动注入'}
                    >
                      {selectedSkill.auto_inject ? (
                        <>
                          <Zap className="w-4 h-4 mr-1 text-amber-400" aria-hidden="true" />自动注入
                        </>
                      ) : (
                        <>
                          <Snowflake className="w-4 h-4 mr-1" aria-hidden="true" />手动获取
                        </>
                      )}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => toggleWorkflowOnly(selectedSkill.id, !selectedSkill.workflow_only)}
                      className={`focus-visible:ring-2 focus-visible:ring-primary/50 ${selectedSkill.workflow_only ? 'border-purple-500/50 text-purple-400' : ''}`}
                      aria-label={selectedSkill.workflow_only ? '设为通用' : '设为工作流专属'}
                    >
                      <Workflow className="w-4 h-4 mr-2" aria-hidden="true" />
                      {selectedSkill.workflow_only ? '工作流专属' : '通用'}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => toggleSkill(selectedSkill.id, !selectedSkill.enabled)}
                      className={`focus-visible:ring-2 focus-visible:ring-primary/50 ${selectedSkill.enabled ? 'border-emerald-500/50 text-emerald-400' : 'border-slate-600'}`}
                      aria-label={selectedSkill.enabled ? '禁用技能' : '启用技能'}
                    >
                      {selectedSkill.enabled ? (
                        <>
                          <PowerOff className="w-4 h-4 mr-2" aria-hidden="true" />禁用
                        </>
                      ) : (
                        <>
                          <Power className="w-4 h-4 mr-2" aria-hidden="true" />启用
                        </>
                      )}
                    </Button>
                  </div>
                </div>
              </CardHeader>

              <CardContent className="space-y-6">
                {isDetailLoading ? (
                  <div className="flex items-center justify-center py-12" role="status">
                    <Loader2 className="w-6 h-6 animate-spin motion-reduce:animate-none text-primary" aria-hidden="true" />
                    <span className="ml-2 text-muted-foreground sr-only">加载技能详情中...</span>
                  </div>
                ) : (
                  <>
                    {/* 技能信息网格 */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">分类</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {getCategoryLabel(selectedSkill.category)}
                        </p>
                      </div>
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">优先级</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {selectedSkill.priority}
                        </p>
                      </div>
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">版本</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {selectedSkill.version}
                        </p>
                      </div>
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">作者</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {selectedSkill.author || '未知'}
                        </p>
                      </div>
                    </div>

                    {/* 状态信息 */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">状态</p>
                        <div className="flex items-center gap-2 mt-1">
                          <div className={`w-2 h-2 rounded-full ${selectedSkill.enabled ? 'bg-emerald-500' : 'bg-slate-500'}`} aria-hidden="true" />
                          <p className="text-sm font-medium text-white">
                            {selectedSkill.enabled ? '已启用' : '已禁用'}
                          </p>
                        </div>
                      </div>
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">自动注入</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {selectedSkill.auto_inject ? '是' : '否'}
                        </p>
                      </div>
                      <div className="p-3 bg-slate-800 rounded-lg border border-slate-700">
                        <p className="text-xs font-medium text-muted-foreground">工作流专属</p>
                        <p className="text-sm font-medium text-white mt-1">
                          {selectedSkill.workflow_only ? '是' : '否'}
                        </p>
                      </div>
                    </div>

                    {/* 组配置 */}
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

                    {/* 标签 */}
                    {selectedSkill.tags.length > 0 && (
                      <div>
                        <h4 className="text-sm font-medium text-slate-300 mb-2">标签</h4>
                        <div className="flex flex-wrap gap-2">
                          {selectedSkill.tags.map(tag => (
                            <Badge key={tag} variant="secondary" className="text-xs">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 内容预览 */}
                    <div>
                      <h4 className="text-sm font-medium text-slate-300 mb-2">内容预览</h4>
                      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
                        <ScrollArea className="h-[300px]">
                          <pre className="text-sm text-slate-300 whitespace-pre-wrap font-mono">
                            {selectedSkill.content}
                          </pre>
                        </ScrollArea>
                      </div>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card className="bg-slate-900 border-slate-700 h-[600px]">
              <CardContent className="flex items-center justify-center h-full">
                <div className="text-center">
                  <div className="p-4 bg-slate-800 rounded-full border border-slate-700 w-16 h-16 flex items-center justify-center mx-auto mb-4">
                    <Eye className="w-8 h-8 text-slate-500" aria-hidden="true" />
                  </div>
                  <h3 className="text-lg font-medium text-slate-300 mb-2">
                    选择技能查看详情
                  </h3>
                  <p className="text-sm text-muted-foreground max-w-sm">
                    从左侧列表中选择一个技能，查看其详细信息、配置选项和内容预览
                  </p>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* 组管理对话框 */}
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
