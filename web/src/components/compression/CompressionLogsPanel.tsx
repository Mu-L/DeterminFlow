import { useState, useEffect, useMemo } from "react";
import { RefreshCw, FileText, Search, Filter, Download, Trash2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
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

interface RawCompressionLog {
  ts?: string;
  timestamp?: string;
  sessionId?: string;
  session_id?: string;
  agentId?: string;
  agent_id?: string;
  compressionType?: string;
  compression_type?: string;
  messageCount?: number;
  message_count?: number;
  totalTokens?: number;
  total_tokens?: number;
  log_file?: string;
}

interface Props {
  compact?: boolean;
}

export default function CompressionLogsPanel({ compact = false }: Props) {
  const [logs, setLogs] = useState<CompressionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [filterType, setFilterType] = useState<string>("all");
  const [filterSession, setFilterSession] = useState<string>("all");

  useEffect(() => {
    loadLogs();
  }, []);

  const loadLogs = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/compression/logs');
      if (response.ok) {
        const data = await response.json();
        // 转换API返回的数据格式
        const formattedLogs = ((data.logs || []) as RawCompressionLog[]).map((log, index) => ({
          id: index.toString(),
          timestamp: log.ts || log.timestamp || "",
          session_id: log.sessionId || log.session_id || "unknown",
          agent_id: log.agentId || log.agent_id || "unknown",
          compression_type: log.compressionType || log.compression_type || "unknown",
          message_count: log.messageCount || log.message_count || 0,
          total_tokens: log.totalTokens || log.total_tokens || 0,
          log_file: log.log_file || "",
        }));
        setLogs(formattedLogs);
      } else {
        setError(`加载失败 (${response.status})`);
      }
    } catch (err) {
      console.error("加载日志失败:", err);
      setError("网络错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const filteredLogs = logs.filter(log => {
    const matchesSearch =
      log.session_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.agent_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      log.log_file.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesType = filterType === "all" || log.compression_type === filterType;
    const matchesSession = filterSession === "all" || log.session_id === filterSession;

    return matchesSearch && matchesType && matchesSession;
  });

  // getCompressionTypeColor / getCompressionTypeLabel / formatTimestamp / formatNumber 已提取到 @/lib/compression-utils

  const handleDownloadLog = async (logFile: string) => {
    if (!logFile) return;
    try {
      const response = await fetch(`/api/compression/logs/download?file=${encodeURIComponent(logFile)}`);
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = logFile.split("/").pop() || "log.json";
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error("下载日志失败:", err);
    }
  };

  const handleDeleteLog = (id: string) => {
    setLogs(prev => prev.filter(log => log.id !== id));
  };

  const uniqueSessions = useMemo(() => {
    return Array.from(new Set(logs.map(log => log.session_id)));
  }, [logs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8" role="status" aria-label="正在加载日志数据">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse">
          <RefreshCw size={16} className="animate-spin" aria-hidden="true" />
          加载日志数据...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-8 gap-3" role="alert" aria-live="polite">
        <p className="text-sm text-red-400">{error}</p>
        <Button variant="outline" size="sm" type="button" onClick={loadLogs} className="min-h-[44px] cursor-pointer">
          <RefreshCw size={14} className="mr-2" aria-hidden="true" />
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4" role="main" aria-label="压缩日志管理">
      {/* 筛选器 */}
      {!compact && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-amber-500" aria-hidden="true" />
              筛选条件
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label htmlFor="compression-log-search">搜索</Label>
                <div className="relative">
                  <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  <Input
                    id="compression-log-search"
                    aria-label="搜索会话ID、Agent ID或日志路径"
                    placeholder="搜索会话ID、Agent ID或日志路径..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="pl-10 min-h-[44px]"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="compression-log-type-filter">压缩类型</Label>
                <Select value={filterType} onValueChange={setFilterType}>
                  <SelectTrigger id="compression-log-type-filter">
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
                <Label htmlFor="compression-log-session-filter">会话ID</Label>
                <Select value={filterSession} onValueChange={setFilterSession}>
                  <SelectTrigger id="compression-log-session-filter">
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
      )}

      {/* 日志统计 */}
      {!compact && (
        <section aria-label="日志统计" className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card role="article" aria-label={`总日志数: ${logs.length}`}>
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

          <Card role="article" aria-label={`筛选结果: ${filteredLogs.length}`}>
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

          <Card role="article" aria-label={`会话数: ${uniqueSessions.length}`}>
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

          <Card role="article" aria-label={`总Token数: ${formatNumber(logs.reduce((sum, log) => sum + log.total_tokens, 0))}`}>
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
        </section>
      )}

      {/* 日志列表 */}
      <section aria-label="压缩日志列表">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-purple-500" aria-hidden="true" />
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
                    className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors"
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
                        <div className="text-sm font-medium tabular-nums">
                          {log.message_count} 条消息
                        </div>
                        <div className="text-sm text-muted-foreground tabular-nums">
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
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDownloadLog(log.log_file)}
                          aria-label={`下载日志文件: ${log.log_file}`}
                          className="min-h-[44px] min-w-[44px]"
                        >
                          <Download size={16} aria-hidden="true" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeleteLog(log.id)}
                          aria-label={`删除日志: ${log.session_id} - ${log.agent_id}`}
                          className="min-h-[44px] min-w-[44px]"
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
      </section>
    </div>
  );
}
