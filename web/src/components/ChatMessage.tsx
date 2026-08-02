import { memo } from "react";
import { Message } from "../types";
import MessageRenderer from "./MessageRenderer";

interface ChatMessageProps {
  message: Message;
  onEdit?: (messageId: string, newContent: string) => void;
  editable?: boolean;
  streaming?: boolean;
  readonly?: boolean;
}

/**
 * ChatMessage — 消息卡片入口。
 * 所有类型路由通过 MessageRenderer 注册表处理。
 */
function ChatMessage({ message, onEdit, editable, streaming, readonly }: ChatMessageProps) {
  return (
    <MessageRenderer
      message={message}
      onEdit={onEdit}
      editable={editable}
      streaming={streaming}
      readonly={readonly}
    />
  );
}

export default memo(ChatMessage);
