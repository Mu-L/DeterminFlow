import { ToolInfo, EventBusStats } from "../types";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Badge } from "@/components/ui/badge";
import { toolGroupLabel, toolGroupColor } from "../lib/utils-helpers";
import { Wrench, BarChart3 } from "lucide-react";

interface ToolStatsPanelProps {
  tools: ToolInfo[];
  stats: EventBusStats;
}

export default function ToolStatsPanel({ tools, stats }: ToolStatsPanelProps) {
  // Tool call frequency data
  const freqData = Object.entries(stats.tool_call_counts)
    .sort(([, a], [, b]) => b - a)
    .map(([name, count]) => ({ name, count }));

  return (
    <section aria-label="工具统计" className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Tool Call Frequency */}
      <div className="bg-slate-800/50 border border-slate-700/50 rounded-lg p-4">
        <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4" aria-hidden="true" />
          工具调用频率
        </h3>
        {freqData.length > 0 ? (
          <div className="h-64" role="img" aria-label="工具调用频率柱状图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={freqData} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 12, fill: "#94a3b8" }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 12, fill: "#94a3b8" }} width={120} />
                <Tooltip
                  contentStyle={{
                    background: "#1e293b",
                    border: "1px solid #475569",
                    borderRadius: "8px",
                    color: "#f1f5f9",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="count" fill="#F59E0B" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="h-64 flex items-center justify-center text-muted-foreground text-sm" role="status">
            暂无工具调用记录
          </div>
        )}
      </div>

      {/* Registered Tools List */}
      <div className="bg-slate-800/50 border border-slate-700/50 rounded-lg p-4">
        <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
          <Wrench className="w-4 h-4" aria-hidden="true" />
          已注册工具 ({tools.length})
        </h3>
        <div className="space-y-2 max-h-64 overflow-y-auto" role="list" aria-label="已注册工具列表">
          {tools.map((tool) => (
            <div
              key={tool.name}
              role="listitem"
              className="flex items-start gap-2 p-2 rounded-lg bg-slate-900/40 hover:bg-slate-900/60 transition-colors duration-200"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono font-medium text-foreground">{tool.name}</span>
                  <Badge
                    variant="outline"
                    className={`text-xs px-1 py-0 ${toolGroupColor[tool.group_id] || ""}`}
                  >
                    {toolGroupLabel[tool.group_id] || tool.group_id}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 truncate" title={tool.description}>
                  {tool.description}
                </p>
              </div>
              {stats.tool_call_counts[tool.name] && (
                <span className="text-xs text-amber-400 font-medium">
                  ×{stats.tool_call_counts[tool.name]}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
