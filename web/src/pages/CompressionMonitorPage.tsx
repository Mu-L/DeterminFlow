import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Activity, Zap, TrendingDown, Cpu, Clock, BarChart3, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { getCompressionTypeColor, getCompressionTypeLabel, formatTimestamp, formatNumber } from "@/lib/compression-utils";

interface CompressionStats {
  current_tokens: number;
  max_tokens: number;
  usage_ratio: number;
  message_count: number;
  tool_result_count: number;
  tool_result_tokens: number;
  model_info: {
    provider: string;
    model: string;
    maxContextTokens: number;
  };
}

interface CompressionHistory {
  id: string;
  timestamp: string;
  session_id: string;
  agent_id: string;
  compression_type: string;
  original_message_count: number;
  compressed_message_count: number;
  original_tokens: number;
  compressed_tokens: number;
  tokens_saved: number;
  reduction_ratio: number;
}

export default function CompressionMonitorPage() {
  const [stats, setStats] = useState<CompressionStats | null>(null);
  const [history, setHistory] = useState<CompressionHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // TODO: 从API加载数据
      // const statsResponse = await fetch('/api/compression/stats');
      // const statsData = await statsResponse.json();
      // setStats(statsData);

      // const historyResponse = await fetch('/api/compression/history');
      // const historyData = await historyResponse.json();
      // setHistory(historyData);

      // 模拟数据
      await new Promise(resolve => setTimeout(resolve, 500));

      setStats({
        current_tokens: 45000,
        max_tokens: 128000,
        usage_ratio: 0.35,
        message_count: 42,
        tool_result_count: 12,
        tool_result_tokens: 18000,
        model_info: {
          provider: "deepseek",
          model: "deepseek-v4-flash",
          maxContextTokens: 1000000,
        },
      });

      setHistory([
        {
          id: "1",
          timestamp: "2026-05-08T10:30:00Z",
          session_id: "session-001",
          agent_id: "main",
          compression_type: "full",
          original_message_count: 128,
          compressed_message_count: 15,
          original_tokens: 98000,
          compressed_tokens: 12000,
          tokens_saved: 86000,
          reduction_ratio: 0.88,
        },
        {
          id: "2",
          timestamp: "2026-05-08T09:15:00Z",
          session_id: "session-002",
          agent_id: "coder",
          compression_type: "micro",
          original_message_count: 85,
          compressed_message_count: 85,
          original_tokens: 65000,
          compressed_tokens: 42000,
          tokens_saved: 23000,
          reduction_ratio: 0.35,
        },
        {
          id: "3",
          timestamp: "2026-05-08T08:45:00Z",
          session_id: "session-001",
          agent_id: "main",
          compression_type: "reactive",
          original_message_count: 156,
          compressed_message_count: 98,
          original_tokens: 125000,
          compressed_tokens: 78000,
          tokens_saved: 47000,
          reduction_ratio: 0.38,
        },
      ]);
    } catch (error) {
      console.error("加载数据失败:", error);
      setError("无法加载压缩监控数据");
      toast({
        title: "加载失败",
        description: "无法加载压缩监控数据",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // getCompressionTypeColor / getCompressionTypeLabel / formatTimestamp / formatNumber 已提取到 @/lib/compression-utils

  if (loading) {
    return (
      <div className="h-[calc(100dvh-3.5rem)] flex items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse motion-reduce:animate-none" role="status" aria-label="正在加载压缩监控数据">
          <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span>加载监控数据...</span>
          <span className="sr-only">正在加载压缩监控数据，请稍候</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-[calc(100dvh-3.5rem)] overflow-auto p-6" role="main" aria-label="压缩状态监控页面">
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
              onClick={loadData}
              aria-label="重试加载监控数据"
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
              <Activity className="text-indigo-500" aria-hidden="true" />
              压缩状态监控
            </h1>
            <p className="text-muted-foreground mt-1">
              实时监控上下文压缩状态和历史记录
            </p>
          </div>
          <Button variant="outline" onClick={loadData} type="button" aria-label="刷新压缩监控数据" className="cursor-pointer min-h-[44px] min-w-[44px]">
            <RefreshCw size={16} className="mr-2" aria-hidden="true" />
            刷新数据
          </Button>
        </div>

        {/* 当前状态概览 */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card role="article" aria-label="上下文使用率">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">上下文使用率</CardTitle>
                <Zap className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold tabular-nums">
                  {(stats.usage_ratio * 100).toFixed(1)}%
                </div>
                <Progress value={stats.usage_ratio * 100} className="mt-2" aria-label={`上下文使用率 ${(stats.usage_ratio * 100).toFixed(1)}%`} />
                <p className="text-xs text-muted-foreground mt-2 tabular-nums">
                  {formatNumber(stats.current_tokens)} / {formatNumber(stats.max_tokens)} tokens
                </p>
              </CardContent>
            </Card>

            <Card role="article" aria-label="消息数量统计">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">消息数量</CardTitle>
                <BarChart3 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold tabular-nums">{stats.message_count}</div>
                <p className="text-xs text-muted-foreground">
                  当前会话中的消息总数
                </p>
              </CardContent>
            </Card>

            <Card role="article" aria-label="工具结果统计">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">工具结果</CardTitle>
                <TrendingDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold tabular-nums">{stats.tool_result_count}</div>
                <p className="text-xs text-muted-foreground tabular-nums">
                  {formatNumber(stats.tool_result_tokens)} tokens
                </p>
              </CardContent>
            </Card>

            <Card role="article" aria-label="当前模型信息">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">当前模型</CardTitle>
                <Cpu className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.model_info.model}</div>
                <p className="text-xs text-muted-foreground tabular-nums">
                  {stats.model_info.provider} · {formatNumber(stats.model_info.maxContextTokens)} tokens
                </p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* 压缩历史 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Clock className="text-purple-500" aria-hidden="true" />
              压缩历史
            </CardTitle>
            <CardDescription>
              最近的压缩操作记录
            </CardDescription>
          </CardHeader>
          <CardContent>
            {history.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground" role="status" aria-label="暂无压缩历史记录">
                暂无压缩历史记录
              </div>
            ) : (
              <div className="space-y-4" role="list" aria-label="压缩历史记录">
                {history.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors duration-200"
                    role="listitem"
                  >
                    <div className="flex items-center gap-4">
                      <Badge className={getCompressionTypeColor(item.compression_type)}>
                        {getCompressionTypeLabel(item.compression_type)}
                      </Badge>
                      <div>
                        <div className="font-medium">
                          会话: {item.session_id}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          Agent: {item.agent_id} · {formatTimestamp(item.timestamp)}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-6">
                      <div className="text-right">
                        <div className="text-sm font-medium">
                          {item.original_message_count} → {item.compressed_message_count} 条消息
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {formatNumber(item.original_tokens)} → {formatNumber(item.compressed_tokens)} tokens
                        </div>
                      </div>

                      <div className="text-right">
                        <div className="text-sm font-medium text-emerald-500">
                          -{formatNumber(item.tokens_saved)} tokens
                        </div>
                        <div className="text-sm text-muted-foreground">
                          压缩率: {(item.reduction_ratio * 100).toFixed(1)}%
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 压缩策略状态 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Zap className="text-amber-500" aria-hidden="true" />
              压缩策略状态
            </CardTitle>
            <CardDescription>
              各压缩策略的当前状态和配置
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4" role="region" aria-label="压缩策略概览">
              <div className="p-4 border rounded-lg" role="article" aria-label="MicroCompact 策略状态">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-3 h-3 rounded-full bg-emerald-500 shrink-0" aria-hidden="true"></div>
                  <span className="font-medium">MicroCompact</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  工具结果微压缩，零API调用成本
                </p>
                <div className="mt-2 text-sm">
                  <div>触发条件: 工具结果数量 &gt; 15 且 Token占比 &gt; 40%</div>
                  <div>保留最近: 5 个工具结果</div>
                </div>
              </div>

              <div className="p-4 border rounded-lg" role="article" aria-label="FullCompact 策略状态">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-3 h-3 rounded-full bg-purple-500 shrink-0" aria-hidden="true"></div>
                  <span className="font-medium">FullCompact</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  全量摘要压缩，调用模型生成结构化摘要
                </p>
                <div className="mt-2 text-sm">
                  <div>触发条件: 上下文占用率 &gt; 80%</div>
                  <div>保留最近: 51.2K tokens</div>
                </div>
              </div>

              <div className="p-4 border rounded-lg" role="article" aria-label="ReactiveCompact 策略状态">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-3 h-3 rounded-full bg-red-500 shrink-0" aria-hidden="true"></div>
                  <span className="font-medium">ReactiveCompact</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  渐进式丢弃压缩，API错误时触发
                </p>
                <div className="mt-2 text-sm">
                  <div>触发条件: API返回413/上下文超限错误</div>
                  <div>最大重试: 5 次</div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
