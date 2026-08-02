/**
 * 工具函数集合
 */

// ============ 时间格式化 ============

export function formatTime(isoString: string): string {
  if (!isoString) return "-";
  const date = new Date(isoString);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelativeTime(isoString: string): string {
  if (!isoString) return "-";
  const date = new Date(isoString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  return `${Math.floor(diff / 86400000)}天前`;
}

// ============ 状态颜色映射 ============

export const statusConfig: Record<string, { color: string; bg: string; dotColor: string; label: string }> = {
  running: { color: "text-green-400", bg: "bg-green-500/20", dotColor: "bg-green-400", label: "运行中" },
  streaming: { color: "text-cyan-400", bg: "bg-cyan-500/20", dotColor: "bg-cyan-400", label: "流式传输" },
  completed: { color: "text-blue-400", bg: "bg-blue-500/20", dotColor: "bg-blue-400", label: "已完成" },
  error: { color: "text-red-400", bg: "bg-red-500/20", dotColor: "bg-red-400", label: "错误" },
  waiting: { color: "text-amber-400", bg: "bg-amber-500/20", dotColor: "bg-amber-400", label: "等待中" },
  idle: { color: "text-slate-400", bg: "bg-slate-500/20", dotColor: "bg-slate-400", label: "空闲" },
};

export function getStatusConfig(status: string) {
  return statusConfig[status] || statusConfig.error;
}

// ============ JSON 格式化 ============

export function safeJsonParse(str: string): unknown {
  try {
    return JSON.parse(str);
  } catch {
    return str;
  }
}

export function prettyJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

// ============ 截断文本 ============

export function truncate(text: string, maxLength: number): string {
  if (!text || text.length <= maxLength) return text || "";
  return text.slice(0, maxLength) + "...";
}

// ============ 工具分组映射（group_id → 显示名称/颜色）============

export const toolGroupLabel: Record<string, string> = {
  memory: "记忆管理",
  coding: "编码工具",
  session_main: "主会话管理",
  communication: "子代理通信",
  config: "配置工具",
  skills: "Skills",
};

export const toolGroupColor: Record<string, string> = {
  memory: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  coding: "bg-green-500/20 text-green-400 border-green-500/30",
  session_main: "bg-indigo-500/20 text-indigo-400 border-indigo-500/30",
  communication: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  config: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  skills: "bg-pink-500/20 text-pink-400 border-pink-500/30",
};
