import { useState, useEffect } from "react";
import { RefreshCw, Zap, TrendingDown, BarChart3, Cpu } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatNumber } from "@/lib/compression-utils";

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

interface Props {
  compact?: boolean;
}

export default function CompressionMonitorPanel({ compact = false }: Props) {
  const [stats, setStats] = useState<CompressionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/compression/stats');
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      } else {
        setError(`加载失败 (${response.status})`);
      }
    } catch (err) {
      console.error("加载压缩监控数据失败:", err);
      setError("网络错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  // formatNumber 已提取到 @/lib/compression-utils

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8" role="status" aria-label="正在加载压缩监控数据">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse">
          <RefreshCw size={16} className="animate-spin" aria-hidden="true" />
          加载监控数据...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-8 gap-3" role="alert" aria-live="polite">
        <p className="text-sm text-red-400">{error}</p>
        <Button variant="outline" size="sm" type="button" onClick={loadData} className="min-h-[44px] cursor-pointer">
          <RefreshCw size={14} className="mr-2" aria-hidden="true" />
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4" role="main" aria-label="压缩监控面板">
      {/* 当前状态概览 */}
      {stats && (
        <section aria-label="压缩状态概览" className={`grid gap-4 ${compact ? "grid-cols-2" : "grid-cols-1 md:grid-cols-2 lg:grid-cols-4"}`}>
          <Card role="article" aria-label={`上下文使用率: ${(stats.usage_ratio * 100).toFixed(1)}%`}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">上下文使用率</CardTitle>
              <Zap className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">
                {(stats.usage_ratio * 100).toFixed(1)}%
              </div>
              <Progress
                value={stats.usage_ratio * 100}
                className="mt-2"
                aria-label={`上下文使用率: ${(stats.usage_ratio * 100).toFixed(1)}%`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(stats.usage_ratio * 100)}
              />
              <p className="text-xs text-muted-foreground mt-2 tabular-nums">
                {formatNumber(stats.current_tokens)} / {formatNumber(stats.max_tokens)} tokens
              </p>
            </CardContent>
          </Card>

          <Card role="article" aria-label={`消息数量: ${stats.message_count}`}>
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

          <Card role="article" aria-label={`工具结果: ${stats.tool_result_count}, 占用 ${formatNumber(stats.tool_result_tokens)} tokens`}>
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

          <Card role="article" aria-label={`当前模型: ${stats.model_info.model}`}>
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
        </section>
      )}

      {/* 压缩策略状态 */}
      {!compact && (
        <section aria-label="压缩策略状态">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Zap className="h-4 w-4 text-amber-500" aria-hidden="true" />
                压缩策略状态
              </CardTitle>
              <CardDescription>
                各压缩策略的当前状态和配置
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-4 border rounded-lg" role="article" aria-label="MicroCompact 策略">
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

                <div className="p-4 border rounded-lg" role="article" aria-label="FullCompact 策略">
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

                <div className="p-4 border rounded-lg" role="article" aria-label="ReactiveCompact 策略">
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
        </section>
      )}
    </div>
  );
}
