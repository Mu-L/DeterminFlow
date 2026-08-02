import { Message } from "../types";
import { AlertTriangle } from "lucide-react";

/**
 * 递归上限到达提示组件。
 *
 * 当 LangGraph 图递归达到 recursion_limit 上限时，
 * 后端在 record 中追加此类型的 display 消息，
 * 提示用户部分结果可能不完整，可继续对话。
 */
export default function RecursionLimitMsg({ message }: { message: Message }) {
  const toolRounds = message.tool_rounds ?? 0;
  const limit = message.limit ?? 0;

  return (
    <div className="flex justify-start mb-4" role="alert" aria-label="递归限制已达">
      <div className="max-w-[85%]">
        <div className="px-4 py-3 rounded-2xl rounded-bl-md border border-amber-500/30 bg-amber-500/10">
          <div className="flex items-center gap-2 mb-1.5">
            <AlertTriangle size={16} className="text-amber-400" aria-hidden="true" />
            <span className="text-sm font-medium text-amber-400">递归限制已达</span>
          </div>
          <p className="text-xs text-amber-300/80">
            {message.content || `当前对话已达递归上限（已执行 ${toolRounds} 次工具调用，上限 ${limit}），部分结果可能不完整。您可以继续发送消息继续对话。`}
          </p>
        </div>
      </div>
    </div>
  );
}
