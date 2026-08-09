export type RoundtableAction =
  | "create"
  | "start"
  | "stop"
  | "delete"
  | "pause"
  | "resume"
  | "inject"
  | "nominate"
  | "addSeat"
  | "removeSeat";

interface RoundtableActionFeedback {
  successTitle: string;
  successDescription: string;
  failureTitle: string;
  failureDescription: string;
}

export const ROUNDTABLE_ACTION_FEEDBACK: Record<
  RoundtableAction,
  RoundtableActionFeedback
> = {
  create: {
    successTitle: "圆桌已创建",
    successDescription: "会议配置已保存，可以开始讨论。",
    failureTitle: "创建失败",
    failureDescription: "无法创建圆桌会议，请重试。",
  },
  start: {
    successTitle: "讨论已开始",
    successDescription: "参与者正在进入讨论。",
    failureTitle: "启动失败",
    failureDescription: "无法开始讨论，请重试。",
  },
  stop: {
    successTitle: "会议已终止",
    successDescription: "当前讨论已停止。",
    failureTitle: "终止失败",
    failureDescription: "无法终止会议，请重试。",
  },
  delete: {
    successTitle: "会议已删除",
    successDescription: "讨论记录和结论已永久删除。",
    failureTitle: "删除失败",
    failureDescription: "无法删除会议，请重试。",
  },
  pause: {
    successTitle: "讨论已暂停",
    successDescription: "当前讨论已暂停。",
    failureTitle: "暂停失败",
    failureDescription: "无法暂停讨论，请重试。",
  },
  resume: {
    successTitle: "讨论已继续",
    successDescription: "参与者将继续讨论。",
    failureTitle: "继续失败",
    failureDescription: "无法继续讨论，请重试。",
  },
  inject: {
    successTitle: "插话已提交",
    successDescription: "内容将在下一次调度时加入讨论。",
    failureTitle: "插话失败",
    failureDescription: "内容未提交，请重试。",
  },
  nominate: {
    successTitle: "点名已提交",
    successDescription: "该席位将在调度时优先发言。",
    failureTitle: "点名失败",
    failureDescription: "无法点名该席位，请重试。",
  },
  addSeat: {
    successTitle: "席位添加已提交",
    successDescription: "新席位将在调度间隙加入讨论。",
    failureTitle: "添加失败",
    failureDescription: "无法添加席位，请重试。",
  },
  removeSeat: {
    successTitle: "席位移除已提交",
    successDescription: "该席位将在调度间隙移除。",
    failureTitle: "移除失败",
    failureDescription: "无法移除席位，请重试。",
  },
};

export function getRoundtableErrorDescription(
  error: unknown,
  fallback: string,
): string {
  if (!(error instanceof Error)) return fallback;

  if (/failed to fetch|networkerror|load failed/i.test(error.message)) {
    return "无法连接到服务，请检查连接后重试。";
  }

  const jsonStart = error.message.indexOf("{");
  if (jsonStart >= 0) {
    try {
      const body = JSON.parse(error.message.slice(jsonStart)) as {
        detail?: unknown;
      };
      if (typeof body.detail === "string" && body.detail.trim()) {
        return body.detail.trim();
      }
    } catch {
      return fallback;
    }
  }

  return fallback;
}
