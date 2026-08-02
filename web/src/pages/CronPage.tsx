import { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import {
  Clock, Play, Power, PowerOff, Trash2, Edit2, Eye, RefreshCw,
  Timer, CheckCircle, XCircle, PauseCircle, FileText, Plus, AlertTriangle,
  ArrowLeft
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '@/components/ui/select';
import { useToast } from '@/components/ui/use-toast';
import {
  CronJobData, CronScheduleData, CronStatusData, CronOutputFile,
  fetchCronJobs, createCronJob, runCronJobNow, updateCronJob, deleteCronJob,
  fetchCronStatus, fetchCronOutput
} from '../lib/api';

const AGENT_TYPES = [
  { value: 'main', label: 'Main' },
  { value: 'coder', label: 'Coder' },
  { value: 'researcher', label: 'Researcher' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'reader', label: 'Reader' },
];

function formatSchedule(job: CronJobData): string {
  const s = job.schedule;
  switch (s.kind) {
    case 'once':
      return s.at ? '单次 · ' + new Date(s.at).toLocaleString() : '单次';
    case 'interval':
      return s.every_minutes ? '每 ' + s.every_minutes + ' 分钟' : '间隔';
    case 'cron':
      return s.expr || 'Cron';
    default:
      return s.kind;
  }
}

function statusBadge(status: string | null) {
  if (!status) return null;
  switch (status) {
    case 'success':
      return <Badge className="bg-green-500/20 text-green-400 border-green-500/30" aria-label="执行成功"><CheckCircle className="w-3 h-3 mr-1" aria-hidden="true" />成功</Badge>;
    case 'error':
      return <Badge className="bg-red-500/20 text-red-400 border-red-500/30" aria-label="执行出错"><XCircle className="w-3 h-3 mr-1" aria-hidden="true" />错误</Badge>;
    case 'silent':
      return <Badge className="bg-gray-500/20 text-gray-400 border-gray-500/30" aria-label="静默执行">静默</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

export default function CronPage() {
  const { toast } = useToast();
  const [jobs, setJobs] = useState<CronJobData[]>([]);
  const [status, setStatus] = useState<CronStatusData | null>(null);
  const [selectedJob, setSelectedJob] = useState<CronJobData | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingJob, setEditingJob] = useState<CronJobData | null>(null);
  const [outputFiles, setOutputFiles] = useState<CronOutputFile[]>([]);
  const [outputContent, setOutputContent] = useState<string | null>(null);
  const [outputFilename, setOutputFilename] = useState<string | null>(null);
  const [showOutput, setShowOutput] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formName, setFormName] = useState('');
  const [formPrompt, setFormPrompt] = useState('');
  const [formScheduleKind, setFormScheduleKind] = useState<'once' | 'interval' | 'cron'>('once');
  const [formAt, setFormAt] = useState('');
  const [formEveryMinutes, setFormEveryMinutes] = useState(30);
  const [formExpr, setFormExpr] = useState('');
  const [formAgentType, setFormAgentType] = useState('researcher');
  const [formSilentOnEmpty, setFormSilentOnEmpty] = useState(true);
  const [formRepeat, setFormRepeat] = useState<string>('');
  const [confirmDialog, setConfirmDialog] = useState<{ open: boolean; title: string; message: string; onConfirm: () => void }>({ open: false, title: '', message: '', onConfirm: () => {} });
  const [formErrors, setFormErrors] = useState<{ name?: string; prompt?: string }>({});
  const confirmDialogRef = useRef<HTMLDivElement>(null);
  const formNameRef = useRef<HTMLInputElement>(null);

  const loadAll = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [jobsRes, statusRes] = await Promise.all([fetchCronJobs(), fetchCronStatus()]);
      setJobs(jobsRes.jobs);
      setStatus(statusRes);
    } catch (err) {
      console.error('Failed to load cron data:', err);
      setError('加载定时任务失败，请检查服务状态后重试');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    if (!showForm) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setShowForm(false); setEditingJob(null); }
    };
    document.addEventListener('keydown', handleEscape);
    // Auto-focus the first input when form opens
    setTimeout(() => formNameRef.current?.focus(), 100);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [showForm]);

  useEffect(() => {
    if (!confirmDialog.open) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setConfirmDialog(prev => ({ ...prev, open: false }));
    };
    document.addEventListener('keydown', handleEscape);
    setTimeout(() => confirmDialogRef.current?.focus(), 50);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [confirmDialog.open]);

  const handleSelectJob = (job: CronJobData) => { setSelectedJob(job); setShowForm(false); setEditingJob(null); };

  const openCreateForm = () => {
    setFormName(''); setFormPrompt(''); setFormScheduleKind('once'); setFormAt('');
    setFormEveryMinutes(30); setFormExpr(''); setFormAgentType('researcher');
    setFormSilentOnEmpty(true); setFormRepeat(''); setEditingJob(null); setFormErrors({}); setShowForm(true);
  };

  const openEditForm = (job: CronJobData) => {
    setFormName(job.name); setFormPrompt(job.prompt);
    setFormScheduleKind(job.schedule.kind as 'once' | 'interval' | 'cron');
    setFormAt(job.schedule.at || ''); setFormEveryMinutes(job.schedule.every_minutes || 30);
    setFormExpr(job.schedule.expr || ''); setFormAgentType(job.agent_type);
    setFormSilentOnEmpty(job.silent_on_empty);
    setFormRepeat(job.repeat != null ? String(job.repeat) : '');
    setEditingJob(job); setFormErrors({}); setShowForm(true);
  };

  const validateForm = (): boolean => {
    const errors: { name?: string; prompt?: string } = {};
    if (!formName.trim()) errors.name = '请输入任务名称';
    if (!formPrompt.trim()) errors.prompt = '请输入提示词';
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSaveJob = async () => {
    if (!validateForm()) return;
    try {
      const schedule: CronScheduleData = { kind: formScheduleKind };
      if (formScheduleKind === 'once') schedule.at = formAt || null;
      if (formScheduleKind === 'interval') schedule.every_minutes = formEveryMinutes;
      if (formScheduleKind === 'cron') schedule.expr = formExpr || null;
      if (editingJob) {
        await updateCronJob(editingJob.id, { name: formName, prompt: formPrompt, schedule, agent_type: formAgentType, silent_on_empty: formSilentOnEmpty });
        toast({ title: '任务已更新', description: '已更新 "' + formName + '"' });
      } else {
        await createCronJob({ name: formName, prompt: formPrompt, schedule, agent_type: formAgentType, silent_on_empty: formSilentOnEmpty, repeat: formRepeat ? parseInt(formRepeat) : null });
        toast({ title: '任务已创建', description: '已创建 "' + formName + '"' });
      }
      setShowForm(false); setEditingJob(null); setFormErrors({}); await loadAll();
      if (editingJob?.id === selectedJob?.id) setSelectedJob(null);
    } catch (error) { console.error('Failed to save job:', error); toast({ title: '保存失败', description: '请重试', variant: 'destructive' }); }
  };

  const handleRunNow = async (jobId: string) => {
    try { await runCronJobNow(jobId); toast({ title: '已触发执行', description: 'Job ' + jobId + ' 正在执行' }); await loadAll(); }
    catch (error) { console.error('Failed to run job:', error); toast({ title: '执行失败', description: '请重试', variant: 'destructive' }); }
  };

  const handleToggleEnabled = async (job: CronJobData) => {
    try { await updateCronJob(job.id, { enabled: !job.enabled }); await loadAll(); toast({ title: job.enabled ? '已暂停' : '已启用', description: job.name }); }
    catch (error) { console.error('Failed to toggle job:', error); }
  };

  const handleDeleteJob = (job: CronJobData) => {
    setConfirmDialog({
      open: true,
      title: '删除任务',
      message: `确定删除任务 "${job.name}" 吗？此操作不可撤消。`,
      onConfirm: async () => {
        try { await deleteCronJob(job.id); if (selectedJob?.id === job.id) setSelectedJob(null); await loadAll(); toast({ title: '已删除', description: '任务 "' + job.name + '" 已删除' }); }
        catch (error) { console.error('Failed to delete job:', error); toast({ title: '删除失败', description: '请重试', variant: 'destructive' }); }
      }
    });
  };

  const handleViewOutput = async (jobId: string) => {
    try { const res = await fetchCronOutput(jobId) as { job_id: string; files: CronOutputFile[]; total: number }; setOutputFiles(res.files); setOutputContent(null); setOutputFilename(null); setShowOutput(true); }
    catch (error) { console.error('Failed to load output:', error); }
  };

  const handleViewFile = async (filename: string) => {
    if (!selectedJob) return;
    try { const res = await fetchCronOutput(selectedJob.id, filename) as { job_id: string; filename: string; content: string }; setOutputContent(res.content); setOutputFilename(filename); }
    catch (error) { console.error('Failed to load file:', error); }
  };

  return (
    <div className="container mx-auto p-6 space-y-6">
      <section aria-label="页面标题" className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Cron 定时任务</h1>
          <p className="text-muted-foreground">管理自动化定时任务</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" type="button" onClick={loadAll} disabled={loading} aria-label="刷新任务列表">
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin motion-reduce:animate-none' : ''}`} aria-hidden="true" />刷新
          </Button>
          <Button type="button" onClick={openCreateForm} aria-label="新建定时任务">
            <Plus className="w-4 h-4 mr-2" aria-hidden="true" />新建任务
          </Button>
        </div>
      </section>

      {/* Status Cards */}
      {status && (
        <section aria-label="调度器状态概览" className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${status.running ? 'bg-green-500' : 'bg-gray-500'}`} aria-hidden="true" />
                调度器状态
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${status.running ? 'text-green-500' : 'text-gray-400'}`}>
                {status.running ? '运行中' : '已停止'}
                <span className="sr-only">{status.running ? '调度器正在运行' : '调度器已停止'}</span>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">任务总数</CardTitle></CardHeader>
            <CardContent><div className="text-2xl font-bold tabular-nums">{status.total_jobs}</div></CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">已启用</CardTitle></CardHeader>
            <CardContent><div className="text-2xl font-bold text-green-500 tabular-nums">{status.enabled_jobs}</div></CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">待执行</CardTitle></CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-amber-500 tabular-nums">{status.due_jobs}</div>
            </CardContent>
          </Card>
        </section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Job List */}
        <section aria-label="任务列表">
          <Card>
            <CardHeader><CardTitle>任务列表</CardTitle></CardHeader>
            <CardContent>
              <ScrollArea className="h-[600px]">
                <div className="space-y-2" role="listbox" aria-label="定时任务列表">
                  {jobs.length === 0 && !loading && (
                    <div className="text-center text-muted-foreground py-8">
                      <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" aria-hidden="true" />
                      <p>暂无定时任务</p>
                      <p className="text-xs mt-1">点击"新建任务"创建第一个定时任务</p>
                    </div>
                  )}
                  {loading && jobs.length === 0 && (
                    <div className="space-y-2" role="status">
                      <span className="sr-only">加载定时任务列表中</span>
                      {[1, 2, 3].map(i => (
                        <div key={i} className="h-20 rounded-lg bg-muted animate-pulse motion-reduce:animate-none" />
                      ))}
                    </div>
                  )}
                  {jobs.map((job) => (
                    <div
                      key={job.id}
                      role="option"
                      aria-selected={selectedJob?.id === job.id}
                      tabIndex={0}
                      className={`p-3 rounded-lg border cursor-pointer transition-all duration-200 min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                        selectedJob?.id === job.id
                          ? 'border-primary bg-primary/5 shadow-sm'
                          : 'border-transparent hover:bg-accent hover:border-border/50'
                      }`}
                      onClick={() => handleSelectJob(job)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSelectJob(job); } }}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-sm flex items-center gap-2">
                            {job.name}
                            {!job.enabled && (
                              <Badge variant="outline" className="text-xs">已暂停</Badge>
                            )}
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5">
                            {formatSchedule(job)}
                          </div>
                          <div className="flex gap-2 mt-2 flex-wrap">
                            <Badge variant="outline" className="text-xs">{job.agent_type}</Badge>
                            {job.next_run_at && (
                              <Badge variant="secondary" className="text-xs">
                                <Timer className="w-3 h-3 mr-1" aria-hidden="true" />
                                {new Date(job.next_run_at).toLocaleString()}
                              </Badge>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-1">
                          {statusBadge(job.last_status)}
                          {job.enabled ? (
                            <Power className="w-4 h-4 text-green-500" aria-label="已启用" />
                          ) : (
                            <PowerOff className="w-4 h-4 text-gray-400" aria-label="已暂停" />
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </section>

        <div className="col-span-1 lg:col-span-2">
          {showForm ? (
            <Card
              role="dialog"
              aria-modal="true"
              aria-label={editingJob ? `编辑任务 ${editingJob.name}` : '新建定时任务'}
            >
              <CardHeader>
                <CardTitle>{editingJob ? '编辑任务' : '新建任务'}</CardTitle>
                <CardDescription>配置定时任务的参数</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="cron-form-name" className="text-sm font-medium block mb-1">任务名称 <span className="text-red-400" aria-hidden="true">*</span></label>
                    <Input
                      ref={formNameRef}
                      id="cron-form-name"
                      value={formName}
                      onChange={e => { setFormName(e.target.value); if (formErrors.name) setFormErrors(prev => ({ ...prev, name: undefined })); }}
                      placeholder="每日记忆整理"
                      required
                      aria-required="true"
                      aria-invalid={!!formErrors.name}
                      aria-describedby={formErrors.name ? 'cron-form-name-error' : undefined}
                      className="min-h-[44px]"
                    />
                    {formErrors.name && (
                      <p id="cron-form-name-error" className="text-xs text-red-400 mt-1" role="alert">{formErrors.name}</p>
                    )}
                  </div>
                  <div>
                    <label htmlFor="cron-form-agent" className="text-sm font-medium block mb-1">Agent 类型</label>
                    <Select value={formAgentType} onValueChange={setFormAgentType}>
                      <SelectTrigger id="cron-form-agent" aria-label="选择 Agent 类型" className="min-h-[44px]">
                        <SelectValue placeholder="选择 Agent 类型" />
                      </SelectTrigger>
                      <SelectContent>
                        {AGENT_TYPES.map(t => (
                          <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div>
                  <label htmlFor="cron-form-prompt" className="text-sm font-medium block mb-1">提示词 <span className="text-red-400" aria-hidden="true">*</span></label>
                  <Textarea
                    id="cron-form-prompt"
                    value={formPrompt}
                    onChange={e => { setFormPrompt(e.target.value); if (formErrors.prompt) setFormErrors(prev => ({ ...prev, prompt: undefined })); }}
                    placeholder="回顾今天的对话..."
                    rows={4}
                    required
                    aria-required="true"
                    aria-invalid={!!formErrors.prompt}
                    aria-describedby={formErrors.prompt ? 'cron-form-prompt-error' : undefined}
                    className="resize-none"
                  />
                  {formErrors.prompt && (
                    <p id="cron-form-prompt-error" className="text-xs text-red-400 mt-1" role="alert">{formErrors.prompt}</p>
                  )}
                </div>

                <fieldset>
                  <legend className="text-sm font-medium mb-2">调度类型</legend>
                  <div className="flex gap-2" role="radiogroup" aria-label="调度类型选择">
                    {(['once', 'interval', 'cron'] as const).map(kind => (
                      <Button
                        key={kind}
                        type="button"
                        variant={formScheduleKind === kind ? 'default' : 'outline'}
                        size="sm"
                        role="radio"
                        aria-checked={formScheduleKind === kind}
                        onClick={() => setFormScheduleKind(kind)}
                        className="min-h-[44px]"
                      >
                        {kind === 'once' ? '单次' : kind === 'interval' ? '间隔' : 'Cron'}
                      </Button>
                    ))}
                  </div>
                </fieldset>

                {formScheduleKind === 'once' && (
                  <div>
                    <label htmlFor="cron-form-at" className="text-sm font-medium block mb-1">执行时间</label>
                    <Input
                      id="cron-form-at"
                      type="datetime-local"
                      value={formAt ? formAt.slice(0, 16) : ''}
                      onChange={e => setFormAt(e.target.value ? new Date(e.target.value).toISOString() : '')}
                      className="min-h-[44px]"
                    />
                  </div>
                )}

                {formScheduleKind === 'interval' && (
                  <div>
                    <label htmlFor="cron-form-interval" className="text-sm font-medium block mb-1">间隔（分钟）</label>
                    <Input
                      id="cron-form-interval"
                      type="number"
                      value={formEveryMinutes}
                      onChange={e => setFormEveryMinutes(parseInt(e.target.value) || 1)}
                      min={1}
                      className="w-48 min-h-[44px]"
                    />
                  </div>
                )}

                {formScheduleKind === 'cron' && (
                  <div>
                    <label htmlFor="cron-form-expr" className="text-sm font-medium block mb-1">Cron 表达式</label>
                    <Input
                      id="cron-form-expr"
                      value={formExpr}
                      onChange={e => setFormExpr(e.target.value)}
                      placeholder="0 9 * * *"
                      className="font-mono min-h-[44px]"
                    />
                    <p className="text-xs text-muted-foreground mt-1">分 时 日 月 周（如 0 9 * * * 表示每天9点）</p>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="flex items-center gap-3">
                    <label htmlFor="cron-form-silent" className="text-sm font-medium cursor-pointer">静默模式（无输出不通知）</label>
                    <Switch
                      id="cron-form-silent"
                      checked={formSilentOnEmpty}
                      onCheckedChange={setFormSilentOnEmpty}
                      aria-label="静默模式：启用后无输出时不发送通知"
                    />
                  </div>
                  <div>
                    <label htmlFor="cron-form-repeat" className="text-sm font-medium block mb-1">执行次数限制（空=无限）</label>
                    <Input
                      id="cron-form-repeat"
                      type="number"
                      value={formRepeat}
                      onChange={e => setFormRepeat(e.target.value)}
                      placeholder="无限"
                      min={1}
                      className="w-32 min-h-[44px]"
                    />
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" type="button" className="min-h-[44px]" onClick={() => { setShowForm(false); setEditingJob(null); }}>
                    取消
                  </Button>
                  <Button type="button" className="min-h-[44px]" onClick={handleSaveJob}>
                    {editingJob ? '保存修改' : '创建任务'}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : selectedJob ? (
            <Card>
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle>{selectedJob.name}</CardTitle>
                    <CardDescription>{formatSchedule(selectedJob)}</CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" type="button" aria-label={`立即运行任务 ${selectedJob.name}`} onClick={() => handleRunNow(selectedJob.id)}>
                      <Play className="w-4 h-4 mr-1" aria-hidden="true" />立即运行
                    </Button>
                    <Button variant="outline" size="sm" type="button" aria-label={selectedJob.enabled ? `暂停任务 ${selectedJob.name}` : `启用任务 ${selectedJob.name}`} onClick={() => handleToggleEnabled(selectedJob)}>
                      {selectedJob.enabled ? <><PowerOff className="w-4 h-4 mr-1" aria-hidden="true" />暂停</> : <><Power className="w-4 h-4 mr-1" aria-hidden="true" />启用</>}
                    </Button>
                    <Button variant="outline" size="sm" type="button" aria-label={`编辑任务 ${selectedJob.name}`} onClick={() => openEditForm(selectedJob)}>
                      <Edit2 className="w-4 h-4 mr-1" aria-hidden="true" />编辑
                    </Button>
                    <Button variant="outline" size="sm" type="button" aria-label={`查看任务 ${selectedJob.name} 的输出`} onClick={() => { handleViewOutput(selectedJob.id); }}>
                      <FileText className="w-4 h-4 mr-1" aria-hidden="true" />输出
                    </Button>
                    <Button variant="outline" size="sm" type="button" className="text-red-400 hover:text-red-300" aria-label={`删除任务 ${selectedJob.name}`} onClick={() => handleDeleteJob(selectedJob)}>
                      <Trash2 className="w-4 h-4 mr-1" aria-hidden="true" />删除
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <section aria-label="任务详细信息" className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-4 bg-muted rounded-lg text-sm">
                  <div><div className="font-medium">ID</div><div className="text-muted-foreground font-mono">{selectedJob.id}</div></div>
                  <div><div className="font-medium">Agent 类型</div><div className="text-muted-foreground">{selectedJob.agent_type}</div></div>
                  <div><div className="font-medium">状态</div><div className="text-muted-foreground flex items-center gap-1.5">
                    {selectedJob.enabled
                      ? <><CheckCircle className="w-3.5 h-3.5 text-green-500" aria-hidden="true" />已启用</>
                      : <><PauseCircle className="w-3.5 h-3.5 text-gray-400" aria-hidden="true" />已暂停</>
                    }
                  </div></div>
                  <div><div className="font-medium">静默模式</div><div className="text-muted-foreground flex items-center gap-1.5">
                    {selectedJob.silent_on_empty
                      ? <><CheckCircle className="w-3.5 h-3.5 text-green-500" aria-hidden="true" />启用</>
                      : <><XCircle className="w-3.5 h-3.5 text-gray-400" aria-hidden="true" />关闭</>
                    }
                  </div></div>
                  <div><div className="font-medium">上次状态</div><div>{statusBadge(selectedJob.last_status) || <span className="text-muted-foreground">未执行</span>}</div></div>
                  <div><div className="font-medium">已完成次数</div><div className="text-muted-foreground tabular-nums">{selectedJob.completed}{selectedJob.repeat ? ` / ${selectedJob.repeat}` : ''}</div></div>
                  <div><div className="font-medium">上次运行</div><div className="text-muted-foreground">{selectedJob.last_run_at ? new Date(selectedJob.last_run_at).toLocaleString() : '-'}</div></div>
                  <div><div className="font-medium">下次运行</div><div className="text-muted-foreground">{selectedJob.next_run_at ? new Date(selectedJob.next_run_at).toLocaleString() : '-'}</div></div>
                </section>

                <section aria-label="任务提示词">
                  <div className="text-sm font-medium mb-2">提示词</div>
                  <ScrollArea className="h-[200px] rounded-md border p-4">
                    <pre className="text-sm whitespace-pre-wrap outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/30 rounded" tabIndex={0} role="region" aria-label="任务提示词内容">{selectedJob.prompt}</pre>
                  </ScrollArea>
                </section>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="flex items-center justify-center h-[600px]">
                <div className="text-center text-muted-foreground">
                  <Eye className="w-12 h-12 mx-auto mb-4 opacity-50" aria-hidden="true" />
                  <p>选择一个任务查看详情</p>
                  <p className="text-sm mt-1">或点击"新建任务"创建定时任务</p>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="fixed bottom-4 right-4 z-50 bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-lg flex items-center gap-3 shadow-lg" role="alert" aria-live="polite">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
          <span className="text-sm">{error}</span>
          <Button variant="ghost" size="sm" type="button" aria-label="重试加载" className="text-red-400 hover:text-red-300 cursor-pointer min-h-[44px]" onClick={() => { setError(null); loadAll(); }}>
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="sm" type="button" onClick={() => setError(null)} aria-label="关闭错误提示" className="text-red-400 hover:text-red-300 min-h-[44px]">
            <XCircle className="w-4 h-4" aria-hidden="true" />
          </Button>
        </div>
      )}

      {/* Confirm Dialog */}
      {confirmDialog.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setConfirmDialog(prev => ({ ...prev, open: false }))} onKeyDown={(e) => { if (e.key === 'Escape') setConfirmDialog(prev => ({ ...prev, open: false })); }}>
          <div ref={confirmDialogRef} role="dialog" aria-modal="true" aria-label={confirmDialog.title} className="bg-slate-800 border border-border/50 rounded-xl p-6 w-full max-w-md" onClick={e => e.stopPropagation()} tabIndex={-1}>
            <h3 className="text-lg font-semibold text-slate-200 mb-2">{confirmDialog.title}</h3>
            <p className="text-sm text-muted-foreground mb-6">{confirmDialog.message}</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" type="button" className="min-h-[44px] cursor-pointer" onClick={() => setConfirmDialog(prev => ({ ...prev, open: false }))}>取消</Button>
              <Button variant="destructive" type="button" className="min-h-[44px] cursor-pointer" onClick={() => { confirmDialog.onConfirm(); setConfirmDialog(prev => ({ ...prev, open: false })); }}>确定删除</Button>
            </div>
          </div>
        </div>
      )}

      {/* Output Viewer Modal */}
      {showOutput && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label="任务执行输出" onClick={() => { setShowOutput(false); setOutputContent(null); }} onKeyDown={(e) => { if (e.key === 'Escape') { setShowOutput(false); setOutputContent(null); } }}>
          <div className="bg-slate-800 border border-border/50 rounded-xl p-6 w-full max-w-[700px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-200">执行输出</h2>
              <Button variant="ghost" size="sm" type="button" onClick={() => { setShowOutput(false); setOutputContent(null); }} aria-label="关闭输出面板" className="min-h-[44px] min-w-[44px]"><XCircle className="w-4 h-4" aria-hidden="true" /></Button>
            </div>
            {outputContent ? (
              <div>
                <div className="flex items-center gap-2 mb-3"><Button variant="outline" size="sm" type="button" onClick={() => { setOutputContent(null); setOutputFilename(null); }} aria-label="返回文件列表"><ArrowLeft className="w-4 h-4 mr-1" aria-hidden="true" />返回列表</Button><span className="text-sm text-muted-foreground">{outputFilename}</span></div>
                <ScrollArea className="h-[500px] rounded-md border p-4 bg-slate-900"><pre className="text-sm whitespace-pre-wrap font-mono text-slate-300">{outputContent}</pre></ScrollArea>
              </div>
            ) : (
              <div className="space-y-2" role="list" aria-label="输出文件列表">
                {outputFiles.length === 0 && <p className="text-sm text-muted-foreground text-center py-4">暂无输出记录</p>}
                {outputFiles.map((file) => (
                  <div key={file.filename} role="listitem" tabIndex={0} className="flex items-center justify-between p-3 bg-slate-800/60 rounded-lg border border-border/30 cursor-pointer hover:bg-accent transition-colors duration-200 min-h-[44px]" onClick={() => handleViewFile(file.filename)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleViewFile(file.filename); } }} aria-label={'查看文件 ' + file.filename}>
                    <div className="flex items-center gap-3"><FileText className="w-4 h-4 text-muted-foreground" aria-hidden="true" /><div><div className="text-sm font-medium text-slate-200">{file.filename}</div><div className="text-xs text-muted-foreground">{new Date(file.created_at).toLocaleString()} · {(file.size / 1024).toFixed(1)} KB</div></div></div>
                    <Eye className="w-4 h-4 text-muted-foreground" aria-hidden="true" />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
