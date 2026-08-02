import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { RefreshCw, FileText, Search, Filter, Download, Trash2, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { getCompressionTypeColor, getCompressionTypeLabel, formatTimestamp, formatNumber } from "@/lib/compression-utils";

interface CompressionLog {
  id: string;
  timestamp: string;
  session_id: string;
  agent_id: string;
  compression_type: string;
  message_count: number;
  total_tokens: number;
  log_file: string;
}

export default function CompressionLogsPage() {
  const [logs, setLogs] = useState<CompressionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [filterType, setFilterType] = useState<string>("all");
  const [filterSession, setFilterSession] = useState<string>("all");
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; label: string } | null>(null);
  const deleteDialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const { toast } = useToast();

  const loadLogs = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // TODO: 从API加载日志
      // const response = await fetch('/api/compression/logs');
      // const data = await response.json();
      // setLogs(data);

      // 模拟数据
      await new Promise(resolve => setTimeout(resolve, 500));

      setLogs([
        {
          id: "1",
          timestamp: "2026-05-08T10:30:00Z",
          session_id: "session-001",
          agent_id: "main",
          compression_type: "full",
          message_count: 128,
          total_tokens: 98000,
          log_file: "logs/compression/session-001/20260508_103000-pre-compact.jsonl",
        },
        {
          id: "2",
          timestamp: "2026-05-08T09:15:00Z",
          session_id: "session-002",
          agent_id: "coder",
          compression_type: "micro",
          message_count: 85,
          total_tokens: 65000,
          log_file: "logs/compression/session-002/20260508_091500-pre-compact.jsonl",
        },
        {
          id: "3",
          timestamp: "2026-05-08T08:45:00Z",
          session_id: "session-001",
          agent_id: "main",
          compression_type: "reactive",
          message_count: 156,
          total_tokens: 125000,
          log_file: "logs/compression/session-001/20260508_084500-pre-compact.jsonl",
        },
        {
          id: "4",
          timestamp: "2026-05-08T07:30:00Z",
          session_id: "session-003",
          agent_id: "researcher",
          compression_type: "full",
          message_count: 92,
          total_tokens: 72000,
          log_file: "logs/compression/session-003/20260508_073000-pre-compact.jsonl",
        },
        {
          id: "5",
          timestamp: "2026-05-08T06:15:00Z",
          session_id: "session-001",
          agent_id: "main",
          compression_type: "micro",
          message_count: 68,
          total_tokens: 45000,
          log_file: "logs/compression/session-001/20260508_061500-pre-compact.jsonl",
        },
      ]);
    } catch (error) {
      console.error("加载日志失败:", error);
      setError("无法加载压缩日志");
      toast({
        title: "加载失败",
        description: "无法加载压缩日志",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  // getCompressionTypeColor / getCompressionTypeLabel / formatTimestamp / formatNumber 已提取到 @/lib/compression-utils

  const filteredLogs = logs.filter(log => {
    const matchesSearch =
      log.session_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.agent_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.log_file.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesType = filterType === "all" || log.compression_type === filterType;
    const matchesSession = filterSession === "all" || log.session_id === filterSession;

    return matchesSearch && matchesType && matchesSession;
  });

  const handleDownloadLog = (logFile: string) => {
    // TODO: 实现日志下载功能
    toast({
      title: "下载日志",
      description: `正在下载: ${logFile}`,
    });
  };

  const handleDeleteLog = (id: string) => {
    const log = logs.find(l => l.id === id);
    setDeleteTarget({ id, label: log ? `${log.session_id} - ${log.agent_id}` : id });
  };

  const confirmDelete = () => {
    if (!deleteTarget) return;
    setLogs(prev => prev.filter(log => log.id !== deleteTarget.id));
    setDeleteTarget(null);
    toast({
      title: "删除成功",
      description: "日志已删除",
    });
  };

  // 删除确认对话框焦点管理
  useEffect(() => {
    if (!deleteTarget || !deleteDialogRef.current) return;
    const el = deleteDialogRef.current;
    confirmBtnRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setDeleteTarget(null); return; }
      if (e.key !== "Tab") return;
      const focusable = el.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [deleteTarget]);

  const uniqueSessions = useMemo(() => {
    return Array.from(new Set(logs.map(log => log.session_id)));
  }, [logs]);

  if (loading) {
    return (
      <div className="h-[calc(100dvh-3.5rem)] flex items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse motion-reduce:animate-none" role="status" aria-label="正在加载日志数据">
          <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span>加载日志数据...</span>
          <span className="sr-only">正在加载压缩日志，请稍候</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-[calc(100dvh-3.5rem)] overflow-auto p-6" role="main" aria-label="压缩日志查看页面">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* 错误提示 */}
        {error && (
          <div className="flex items-center gap-3 p-4 border border-red-500/20 bg-red-500/5 rounded-lg" role="alert" aria-live="polite">
            <AlertTriangle size={16} className="text-red-500 shrink-0" aria-hidden="true" />
            <span className="text-sm flex-1">{error}</span>
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={loadLogs}
              aria-label="重试加载日志"
              className="cursor-pointer min-h-[44px]"
            >
              <RefreshCw size={14} className="mr-1" aria-hidden="true" />
              重试
            </Button>
          </div>
        )}

        {/* 页面标题 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <FileText className="text-indigo-500" aria-hidden="true" />
              压缩日志查看
            </h1>
            <p className="text-muted-foreground mt-1">
              查看和管理压缩历史日志
            </p>
          </div>
          <Button variant="outline" onClick={loadLogs} type="button" aria-label="刷新日志数据" className="cursor-pointer min-h-[44px]">
            <RefreshCw size={16} className="mr-2" aria-hidden="true" />
            刷新日志
          </Button>
        </div>

        {/* 筛选器 */}
        <Card aria-label="日志筛选条件">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Filter className="text-amber-500" aria-hidden="true" />
              筛选条件
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label htmlFor="log-search">搜索</Label>
                <div className="relative">
                  <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  <Input
                    id="log-search"
                    placeholder="搜索会话ID、Agent ID或日志路径..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="pl-10 min-h-[44px]"
                    aria-label="搜索压缩日志"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="log-filter-type">压缩类型</Label>
                <Select value={filterType} onValueChange={setFilterType}>
                  <SelectTrigger id="log-filter-type" aria-label="筛选压缩类型">
                    <SelectValue placeholder="选择压缩类型" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部类型</SelectItem>
                    <SelectItem value="micro">MicroCompact</SelectItem>
                    <SelectItem value="full">FullCompact</SelectItem>
                    <SelectItem value="reactive">ReactiveCompact</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="log-filter-session">会话ID</Label>
                <Select value={filterSession} onValueChange={setFilterSession}>
                  <SelectTrigger id="log-filter-session" aria-label="筛选会话">
                    <SelectValue placeholder="选择会话" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部会话</SelectItem>
                    {uniqueSessions.map(session => (
                      <SelectItem key={session} value={session}>
                        {session}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 日志统计 */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card role="article" aria-label="总日志数统计">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">总日志数</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">{logs.length}</div>
              <p className="text-xs text-muted-foreground">
                压缩操作总记录数
              </p>
            </CardContent>
          </Card>

          <Card role="article" aria-label="筛选结果统计">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">筛选结果</CardTitle>
              <Search className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">{filteredLogs.length}</div>
              <p className="text-xs text-muted-foreground">
                符合条件的日志数
              </p>
            </CardContent>
          </Card>

          <Card role="article" aria-label="会话数统计">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">会话数</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">{uniqueSessions.length}</div>
              <p className="text-xs text-muted-foreground">
                涉及的会话数量
              </p>
            </CardContent>
          </Card>

          <Card role="article" aria-label="总Token数统计">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">总Token数</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">
                {formatNumber(logs.reduce((sum, log) => sum + log.total_tokens, 0))}
              </div>
              <p className="text-xs text-muted-foreground">
                压缩前的总Token数
              </p>
            </CardContent>
          </Card>
        </div>

        {/* 日志列表 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="text-purple-500" aria-hidden="true" />
              日志列表
            </CardTitle>
            <CardDescription>
              压缩操作的历史记录
            </CardDescription>
          </CardHeader>
          <CardContent>
            {filteredLogs.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground" role="status" aria-label={logs.length === 0 ? "暂无日志记录" : "没有符合筛选条件的日志"}>
                {logs.length === 0 ? "暂无日志记录" : "没有符合筛选条件的日志"}
              </div>
            ) : (
              <div className="space-y-2" role="list" aria-label="压缩日志列表">
                {filteredLogs.map((log) => (
                  <div
                    key={log.id}
                    className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors duration-200"
                    role="listitem"
                  >
                    <div className="flex items-center gap-4">
                      <Badge className={getCompressionTypeColor(log.compression_type)}>
                        {getCompressionTypeLabel(log.compression_type)}
                      </Badge>
                      <div>
                        <div className="font-medium">
                          {log.session_id} · {log.agent_id}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {formatTimestamp(log.timestamp, { includeYear: true })}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-6">
                      <div className="text-right">
                        <div className="text-sm font-medium">
                          {log.message_count} 条消息
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {formatNumber(log.total_tokens)} tokens
                        </div>
                      </div>

                      <div className="text-right">
                        <div className="text-sm text-muted-foreground max-w-[200px] truncate" title={log.log_file}>
                          {log.log_file}
                        </div>
                      </div>

                      <div className="flex gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => handleDownloadLog(log.log_file)}
                          aria-label={`下载日志文件: ${log.log_file}`}
                          className="cursor-pointer min-h-[44px] min-w-[44px]"
                        >
                          <Download size={16} aria-hidden="true" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => handleDeleteLog(log.id)}
                          aria-label={`删除日志: ${log.session_id} - ${log.agent_id}`}
                          className="cursor-pointer min-h-[44px] min-w-[44px] transition-colors duration-200"
                        >
                          <Trash2 size={16} aria-hidden="true" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 删除确认对话框 */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => { if (e.target === e.currentTarget) setDeleteTarget(null); }}
        >
          <div
            ref={deleteDialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="确认删除日志"
            className="bg-slate-900 border border-slate-700 rounded-lg p-6 max-w-md w-full mx-4 shadow-xl"
          >
            <div className="flex items-start gap-3 mb-4">
              <AlertTriangle size={20} className="text-red-500 shrink-0 mt-0.5" aria-hidden="true" />
              <div>
                <h2 className="text-lg font-semibold">确认删除日志</h2>
                <p className="text-sm text-muted-foreground mt-1">
                  确定要删除 <span className="font-medium text-foreground">{deleteTarget.label}</span> 的压缩日志吗？此操作不可撤销。
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                type="button"
                onClick={() => setDeleteTarget(null)}
                aria-label="取消删除"
                className="cursor-pointer min-h-[44px]"
              >
                取消
              </Button>
              <Button
                ref={confirmBtnRef}
                variant="destructive"
                type="button"
                onClick={confirmDelete}
                aria-label="确认删除日志"
                className="cursor-pointer min-h-[44px]"
              >
                确认删除
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
