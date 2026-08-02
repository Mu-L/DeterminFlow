import { Message } from "../types";
import CompressionDivider from "./CompressionDivider";

// ============================================================
// Display 消息注册表 — 新增 display 类型只需在此注册一行
// ============================================================

/**
 * Display 消息渲染组件的统一接口：
 * 每个注册的组件接收完整 Message、自行提取所需字段。
 */
type DisplayMessageComponent = React.ComponentType<{ message: Message }>;

/**
 * display 类型 → 渲染组件的映射注册表。
 *
 * 扩展方式（只需两步）：
 * 1. 创建组件（如 PlanProgress.tsx），实现 DisplayMessageComponent 接口
 * 2. 在此注册表中添加一行映射
 *
 * 无需修改 ChatMessage.tsx 或 SessionDetailPanel.tsx。
 */
const CompressionDividerWrapper: DisplayMessageComponent = ({ message }) => {
  if (!message.compression_event) return null;
  return <CompressionDivider event={message.compression_event} strategy={message.strategy} />;
};

const displayMessageRegistry: Record<string, DisplayMessageComponent> = {
  compression_divider: CompressionDividerWrapper,
  // 后续新增示例：
  // plan_progress: PlanProgressWrapper,
  // day_divider: DayDividerWrapper,
};

// ============================================================
// Display 消息路由组件
// ============================================================

interface DisplayMessageRouterProps {
  message: Message;
}

/**
 * 根据 msg.display 字段查找注册表并渲染对应的组件。
 *
 * 使用方式：
 * - ChatMessage.tsx: 在 role 分支之前调用，如果命中则直接返回
 * - SessionDetailPanel.tsx: 同上
 */
export default function DisplayMessageRouter({ message }: DisplayMessageRouterProps) {
  // 1. 标准路径：通过 display 字段查找
  let displayType = message.display;

  // 2. 兜底兼容：旧持久化消息可能无 display 但有 compression_event
  if (!displayType && message.compression_event) {
    displayType = "compression_divider";
  }

  if (!displayType) return null;

  const Component = displayMessageRegistry[displayType];
  if (!Component) return null;

  return <Component message={message} />;
}
