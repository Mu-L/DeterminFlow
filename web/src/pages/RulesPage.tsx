import { useState, useEffect, useRef } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { AlertTriangle, RefreshCw, Eye, Edit2, Check, X, Layers, Trash2, Workflow, Loader2 } from 'lucide-react';
import { RuleGroup } from '../types';
import { fetchRuleGroups, createRuleGroup, updateRuleGroup, deleteRuleGroup, setRuleGroups } from '../lib/api';
import { useToast } from '@/components/ui/use-toast';

interface Rule {
  id: string;
  name: string;
  description: string;
  content: string;
  enabled: boolean;
  workflow_only: boolean;
  agent_types: string[];
  group_ids?: string[];
  version: string;
  author: string;
  config?: {
    agent_types?: string[];
    group_ids?: string[];
  };
}

export default function RulesPage() {
  const { toast } = useToast();
  const [rules, setRules] = useState<Rule[]>([]);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);
  const [stats, setStats] = useState<{ total: number; enabled?: number; workflow_only?: number; groups?: number } | null>(null);

  // 组管理状态
  const [groups, setGroups] = useState<RuleGroup[]>([]);
  const [showGroupDialog, setShowGroupDialog] = useState(false);
  const [editingGroup, setEditingGroup] = useState<RuleGroup | null>(null);
  const [groupForm, setGroupForm] = useState({ id: '', name: '', description: '' });

  // rule 组分配编辑状态
  const [isEditingGroups, setIsEditingGroups] = useState(false);
  const [editingGroupIds, setEditingGroupIds] = useState<string[]>([]);

  // Loading 状态
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isReloading, setIsReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 自定义确认对话框状态
  const [confirmDialog, setConfirmDialog] = useState<{ title: string; message: string; onConfirm: () => void } | null>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      setError(null);
      try {
        await Promise.all([
          loadRules(),
          loadStats(),
          loadGroups()
        ]);
      } catch (err) {
        setError('加载数据失败，请刷新页面重试');
        console.error('Failed to load initial data:', err);
      } finally {
        setIsLoading(false);
      }
    };
    loadData();
  }, []);

  const loadGroups = async () => {
    try {
      const res = await fetchRuleGroups();
      setGroups(res.groups);
    } catch (error) {
      console.error('Failed to load rule groups:', error);
      throw error;
    }
  };

  const loadRules = async () => {
    try {
      const res = await fetch('/api/rules/summary');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRules(data.rules || []);
    } catch (error) {
      console.error('Failed to load rules:', error);
      throw error;
    }
  };

  const loadStats = async () => {
    try {
      const res = await fetch('/api/rules/stats');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStats(await res.json());
    } catch (error) {
      console.error('Failed to load stats:', error);
      throw error;
    }
  };

  const loadDetail = async (id: string) => {
    setIsLoadingDetail(true);
    try {
      const res = await fetch(`/api/rules/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = await res.json();
      if (detail.config?.group_ids) {
        detail.group_ids = detail.config.group_ids;
      }
      setSelectedRule(detail);
    } catch (error) {
      console.error('Failed to load rule detail:', error);
      toast({
        title: "加载详情失败",
        description: "无法加载规则详情，请重试",
        variant: "destructive",
      });
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const reloadRules = async () => {
    setIsReloading(true);
    try {
      const res = await fetch('/api/rules/reload', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await Promise.all([loadRules(), loadStats()]);
      toast({
        title: "重新加载成功",
        description: "规则已重新加载",
      });
    } catch (error) {
      console.error('Failed to reload rules:', error);
      toast({
        title: "重新加载失败",
        description: "无法重新加载规则，请重试",
        variant: "destructive",
      });
    } finally {
      setIsReloading(false);
    }
  };

  const toggleWorkflowOnly = async (id: string, enabled: boolean) => {
    try {
      const res = await fetch(`/api/rules/${id}/workflow-only?enabled=${enabled}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadRules();
      if (selectedRule?.id === id) await loadDetail(id);
    } catch (error) {
      console.error('Error toggling workflow-only:', error);
      toast({
        title: "切换失败",
        description: "无法切换工作流专属状态，请重试",
        variant: "destructive",
      });
    }
  };

  // === 组管理对话框 ===
  const openCreateGroup = () => {
    setEditingGroup(null);
    setGroupForm({ id: '', name: '', description: '' });
    setShowGroupDialog(true);
  };

  const openEditGroup = (group: RuleGroup) => {
    setEditingGroup(group);
    setGroupForm({ id: group.id, name: group.name, description: group.description });
    setShowGroupDialog(true);
  };

  const saveGroup = async () => {
    try {
      if (editingGroup) {
        await updateRuleGroup(editingGroup.id, { name: groupForm.name, description: groupForm.description });
        toast({
          title: "组已更新",
          description: `已更新组 "${groupForm.name}"`,
        });
      } else {
        await createRuleGroup({ id: groupForm.id, name: groupForm.name, description: groupForm.description });
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
    const groupName = groups.find(g => g.id === groupId)?.name || '';
    setConfirmDialog({
      title: '删除规则组',
      message: `确定删除组 "${groupName}" 吗？此操作不可撤销。`,
      onConfirm: async () => {
        try {
          await deleteRuleGroup(groupId);
          await loadGroups();
          toast({ title: "组已删除", description: `已删除组 "${groupName}"` });
        } catch (error) {
          console.error('Failed to delete group:', error);
          toast({ title: "删除失败", description: "无法删除组，请重试", variant: "destructive" });
        }
      },
    });
  };

  // === Rule 组分配 ===
  const startEditingGroups = () => {
    setEditingGroupIds(selectedRule?.group_ids || selectedRule?.config?.group_ids || []);
    setIsEditingGroups(true);
  };

  const cancelEditingGroups = () => {
    setIsEditingGroups(false);
    setEditingGroupIds([]);
  };

  const saveRuleGroups = async () => {
    if (!selectedRule) return;
    try {
      await setRuleGroups(selectedRule.id, editingGroupIds);
      setIsEditingGroups(false);
      await loadRules();
      await loadDetail(selectedRule.id);
      toast({
        title: "组分配已保存",
        description: `已更新 ${selectedRule.name} 的组分配`,
      });
    } catch (error) {
      console.error('Error saving rule groups:', error);
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

  const getRuleGroupNames = () => {
    const gids = selectedRule?.group_ids || selectedRule?.config?.group_ids || [];
    return groups.filter(g => gids.includes(g.id));
  };

  if (isLoading) {
    return (
      <div className="container mx-auto p-6">
        <div className="flex items-center justify-center h-[600px]">
          <div className="text-center" role="status" aria-label="加载规则中">
            <Loader2 className="w-8 h-8 animate-spin motion-reduce:animate-none mx-auto mb-4" aria-hidden="true" />
            <p className="text-muted-foreground">加载规则中...</p>
            <span className="sr-only">正在加载规则列表</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="flex items-center justify-center h-[600px]">
          <div className="text-center" role="alert" aria-live="assertive">
            <AlertTriangle className="w-8 h-8 text-destructive mx-auto mb-4" aria-hidden="true" />
            <p className="text-destructive mb-4">{error}</p>
            <Button onClick={() => window.location.reload()} aria-label="刷新页面重试">刷新页面</Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Rules 管理</h1>
          <p className="text-muted-foreground">管理必须遵守的规则</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={openCreateGroup} aria-label="管理规则组" type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
            <Layers className="w-4 h-4 mr-2" />管理组
          </Button>
          <Button onClick={reloadRules} disabled={isReloading} aria-label="重新加载规则" type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
            {isReloading ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin motion-reduce:animate-none" />
            ) : (
              <RefreshCw className="w-4 h-4 mr-2" />
            )}
            重新加载
          </Button>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="bg-slate-800/50 border-slate-700">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium text-blue-400">总计规则</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-300 tabular-nums">{stats.total}</div>
            </CardContent>
          </Card>
          {stats.enabled !== undefined && (
            <Card className="bg-slate-800/50 border-slate-700">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-green-400">已启用</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-green-300 tabular-nums">{stats.enabled}</div>
              </CardContent>
            </Card>
          )}
          {stats.workflow_only !== undefined && (
            <Card className="bg-slate-800/50 border-slate-700">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-purple-400">工作流专属</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-purple-300 tabular-nums">{stats.workflow_only}</div>
              </CardContent>
            </Card>
          )}
          {stats.groups !== undefined && (
            <Card className="bg-slate-800/50 border-slate-700">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-orange-400">规则组</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-orange-300 tabular-nums">{stats.groups}</div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle>Rules 列表</CardTitle></CardHeader>
          <CardContent>
            <ScrollArea className="h-[600px]">
              <div className="space-y-2">
                {rules.map((rule) => {
                  const ruleGroupIds = rule.group_ids || rule.config?.group_ids || [];
                  return (
                    <div
                      key={rule.id}
                      className={`p-3 rounded-lg border cursor-pointer transition-colors duration-200 ${selectedRule?.id === rule.id ? 'border-primary bg-primary/5' : 'hover:bg-accent'}`}
                      onClick={() => loadDetail(rule.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          loadDetail(rule.id);
                        }
                      }}
                      aria-label={`查看规则 ${rule.name}`}
                      aria-pressed={selectedRule?.id === rule.id}
                    >
                      <div className="flex items-start gap-2">
                        <AlertTriangle
                          className={`w-5 h-5 mt-0.5 ${!rule.enabled ? 'text-muted-foreground' : ''}`}
                          aria-hidden="true"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-sm flex items-center gap-2">
                            {rule.name}
                            {!rule.enabled && <span className="text-xs text-muted-foreground">(已禁用)</span>}
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{rule.description}</div>
                          <div className="flex gap-1 mt-2 flex-wrap">
                            <Badge variant="outline" className="text-xs">
                              {rule.workflow_only ? '工作流' : '通用'}
                              <span className="sr-only">{rule.workflow_only ? '工作流专属规则' : '通用规则'}</span>
                            </Badge>
                            {rule.agent_types?.length > 0 && (
                              <Badge variant="outline" className="text-xs">
                                {rule.agent_types.length} 个 Agent
                                <span className="sr-only">关联 {rule.agent_types.length} 个 Agent 类型</span>
                              </Badge>
                            )}
                            {ruleGroupIds.length > 0 && (
                              <Badge variant="secondary" className="text-xs">
                                {ruleGroupIds.length} 个组
                                <span className="sr-only">属于 {ruleGroupIds.length} 个规则组</span>
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <div className="lg:col-span-2">
          {isLoadingDetail ? (
            <Card>
              <CardContent className="flex items-center justify-center h-[600px]">
                <div className="text-center" role="status" aria-label="加载规则详情中">
                  <Loader2 className="w-8 h-8 animate-spin motion-reduce:animate-none mx-auto mb-4" aria-hidden="true" />
                  <p className="text-muted-foreground">加载规则详情...</p>
                  <span className="sr-only">正在加载规则详情内容</span>
                </div>
              </CardContent>
            </Card>
          ) : selectedRule ? (
            <Card>
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="flex items-center gap-2">
                      {selectedRule.name}
                    </CardTitle>
                    <CardDescription>{selectedRule.description}</CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => toggleWorkflowOnly(selectedRule.id, !selectedRule.workflow_only)}
                      aria-label={selectedRule.workflow_only ? '切换为通用规则' : '切换为工作流专属规则'}
                      type="button"
                      className="focus-visible:ring-2 focus-visible:ring-indigo-500/30"
                    >
                      {selectedRule.workflow_only ? (
                        <><Workflow className="w-4 h-4 mr-2 text-purple-500" />工作流专属</>
                      ) : (
                        <><Workflow className="w-4 h-4 mr-2" />通用</>
                      )}
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4 p-4 bg-muted rounded-lg text-sm">
                  <div><div className="font-medium">版本</div><div className="text-muted-foreground">{selectedRule.version}</div></div>
                  <div><div className="font-medium">作者</div><div className="text-muted-foreground">{selectedRule.author || '未知'}</div></div>
                  <div>
                    <div className="font-medium">启用状态</div>
                    <div className="text-muted-foreground flex items-center gap-2">
                      <span className={`inline-block w-2 h-2 rounded-full ${selectedRule.enabled ? 'bg-green-500' : 'bg-red-500'}`} aria-hidden="true" />
                      {selectedRule.enabled ? '已启用' : '已禁用'}
                      <span className="sr-only">{selectedRule.enabled ? '规则已启用' : '规则已禁用'}</span>
                    </div>
                  </div>
                  <div>
                    <div className="font-medium">工作流专属</div>
                    <div className="text-muted-foreground flex items-center gap-2">
                      <span className={`inline-block w-2 h-2 rounded-full ${selectedRule.workflow_only ? 'bg-blue-500' : 'bg-gray-500'}`} aria-hidden="true" />
                      {selectedRule.workflow_only ? '是' : '否'}
                      <span className="sr-only">{selectedRule.workflow_only ? '规则仅限工作流使用' : '规则通用'}</span>
                    </div>
                  </div>
                </div>

                {/* 组配置 (替代 agent_types) */}
                <div>
                  <div className="text-sm font-medium mb-2 flex items-center justify-between">
                    <span>所属组</span>
                    {!isEditingGroups ? (
                      <Button variant="ghost" size="sm" onClick={startEditingGroups} aria-label="编辑规则组分配" type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
                        <Edit2 className="w-3 h-3 mr-1" />编辑
                      </Button>
                    ) : (
                      <div className="flex gap-2">
                        <Button variant="ghost" size="sm" onClick={cancelEditingGroups} aria-label="取消编辑组分配" type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
                          <X className="w-3 h-3 mr-1" />取消
                        </Button>
                        <Button variant="default" size="sm" onClick={saveRuleGroups} aria-label="保存组分配" type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
                          <Check className="w-3 h-3 mr-1" />保存
                        </Button>
                      </div>
                    )}
                  </div>
                  {!isEditingGroups ? (
                    <div className="flex flex-wrap gap-2">
                      {getRuleGroupNames().length > 0 ? (
                        getRuleGroupNames().map(g => (
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
                        Rule 与组是多对多关系。Agent 通过可见的组来间接访问此 Rule。
                      </p>
                    </div>
                  )}
                </div>

                <div>
                  <div className="text-sm font-medium mb-2">规则内容</div>
                  <ScrollArea className="h-[400px] rounded-md border p-4 bg-muted/50">
                    <pre className="text-sm whitespace-pre-wrap font-mono">{selectedRule.content}</pre>
                  </ScrollArea>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="flex items-center justify-center h-[600px]">
                <div className="text-center text-muted-foreground">
                  <Eye className="w-12 h-12 mx-auto mb-4 opacity-50" aria-hidden="true" />
                  <p className="text-lg font-medium mb-2">选择一个 Rule 查看详情</p>
                  <p className="text-sm">从左侧列表中选择一个规则</p>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* 确认对话框 */}
      {confirmDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setConfirmDialog(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-dialog-title"
          onKeyDown={(e) => {
            if (e.key === 'Escape') setConfirmDialog(null);
          }}
        >
          <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[400px]" onClick={e => e.stopPropagation()}>
            <h2 id="confirm-dialog-title" className="text-lg font-semibold text-slate-200 mb-2">{confirmDialog.title}</h2>
            <p className="text-sm text-muted-foreground mb-6">{confirmDialog.message}</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmDialog(null)} type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
                取消
              </Button>
              <Button
                ref={confirmBtnRef}
                variant="destructive"
                size="sm"
                onClick={() => { confirmDialog.onConfirm(); setConfirmDialog(null); }}
                type="button"
                className="focus-visible:ring-2 focus-visible:ring-red-500/30"
              >
                确认删除
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 组管理对话框 */}
      {showGroupDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setShowGroupDialog(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="group-dialog-title"
          onKeyDown={(e) => {
            if (e.key === 'Escape') setShowGroupDialog(false);
          }}
        >
          <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-[500px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 id="group-dialog-title" className="text-lg font-semibold text-slate-200">管理规则组</h2>
              <button
                onClick={() => setShowGroupDialog(false)}
                className="p-1 text-muted-foreground hover:text-foreground cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded"
                aria-label="关闭对话框"
                type="button"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>

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
                      className="p-1.5 text-muted-foreground hover:text-indigo-400 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-indigo-500/30 rounded"
                      aria-label={`编辑组 ${group.name}`}
                      type="button"
                    >
                      <Edit2 size={12} aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => handleDeleteGroup(group.id)}
                      className="p-1.5 text-muted-foreground hover:text-red-400 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-red-500/30 rounded"
                      aria-label={`删除组 ${group.name}`}
                      type="button"
                    >
                      <Trash2 size={12} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))}
              {groups.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-4">暂无组，请创建</p>
              )}
            </div>

            <div className="border-t border-border/30 pt-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">{editingGroup ? '编辑组' : '新建组'}</h3>
              <div className="space-y-3">
                <div>
                  <label htmlFor="group-id" className="text-xs text-muted-foreground block mb-1">组 ID</label>
                  <input
                    id="group-id"
                    value={groupForm.id}
                    onChange={e => setGroupForm(p => ({ ...p, id: e.target.value }))}
                    disabled={!!editingGroup}
                    placeholder="unique-group-id"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                    autoFocus={!editingGroup}
                  />
                </div>
                <div>
                  <label htmlFor="group-name" className="text-xs text-muted-foreground block mb-1">组名称</label>
                  <input
                    id="group-name"
                    value={groupForm.name}
                    onChange={e => setGroupForm(p => ({ ...p, name: e.target.value }))}
                    placeholder="我的规则组"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                    autoFocus={!!editingGroup}
                  />
                </div>
                <div>
                  <label htmlFor="group-description" className="text-xs text-muted-foreground block mb-1">描述</label>
                  <input
                    id="group-description"
                    value={groupForm.description}
                    onChange={e => setGroupForm(p => ({ ...p, description: e.target.value }))}
                    placeholder="可选描述"
                    className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 min-h-[44px]"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" size="sm" onClick={() => setShowGroupDialog(false)} type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">取消</Button>
                  <Button size="sm" onClick={saveGroup} disabled={!groupForm.id || !groupForm.name} type="button" className="focus-visible:ring-2 focus-visible:ring-indigo-500/30">
                    {editingGroup ? '保存修改' : '创建'}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
