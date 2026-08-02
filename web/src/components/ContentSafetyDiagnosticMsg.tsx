import { useState } from "react";
import { Message } from "../types";
import {
  Microscope,
  ScrollText,
  MessageCircle,
  Pin,
  BookOpen,
  HelpCircle,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

/**
 * 内容安全诊断结果组件
 *
 * 展示二分排除法的诊断结果：触发审查的消息类别、详细步骤、消息预览。
 */
export default function ContentSafetyDiagnosticMsg({ message }: { message: Message }) {
  const [stepsExpanded, setStepsExpanded] = useState(false);

  const result = message.diagnostic_result;
  if (!result) {
    return (
      <div className="flex justify-start mb-4">
        <div className="px-4 py-2 rounded-lg glass-card border border-red-500/30" role="alert">
          <span className="text-xs text-red-400">诊断结果数据缺失</span>
        </div>
      </div>
    );
  }

  const { triggered_by, identified_message_type, message_preview, summary, diagnostic_steps } =
    result;

  // 根据触发类别配置样式
  const triggeredConfig: Record<string, { label: string; color: string; Icon: React.ElementType }> = {
    system_prompt: {
      label: "系统提示词 (System Prompt)",
      color: "text-purple-400",
      Icon: ScrollText,
    },
    user_message: {
      label: "用户消息",
      color: "text-blue-400",
      Icon: MessageCircle,
    },
    injection_content: {
      label: "系统注入内容 (规则/技能/工作流)",
      color: "text-cyan-400",
      Icon: Pin,
    },
    conversation_history: {
      label: "对话历史消息",
      color: "text-orange-400",
      Icon: BookOpen,
    },
    unknown: {
      label: "未能定位",
      color: "text-slate-400",
      Icon: HelpCircle,
    },
  };

  const config = triggeredConfig[triggered_by] || triggeredConfig.unknown;
  const steps = diagnostic_steps || [];
  const hasSteps = steps.length > 0;
  const passedSteps = steps.filter((s) => s.result === "通过").length;
  const blockedSteps = steps.filter((s) => s.result === "拦截").length;

  return (
    <div className="flex items-center gap-2 my-4">
      {/* 分割线 */}
      <div className="flex-1 h-px bg-emerald-500/30" />

      {/* 诊断结果卡片 */}
      <div className="flex-shrink-0 max-w-[85%]">
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3" role="article" aria-label="详细诊断结果">
          {/* 标题行 */}
          <div className="flex items-center gap-2 mb-3">
            <Microscope size={16} className="text-emerald-400" aria-hidden="true" />
            <span className="text-sm font-medium text-emerald-400">详细诊断结果</span>
          </div>

          {/* 触发类别 */}
          <div className="mb-3 p-3 rounded-md bg-slate-800/60 border border-slate-700/40">
            <div className="flex items-center gap-2 mb-1">
              <config.Icon size={14} className={config.color} aria-hidden="true" />
              <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
            </div>
            {identified_message_type && (
              <p className="text-xs text-slate-500 ml-7">
                消息类型: {identified_message_type}
              </p>
            )}
          </div>

          {/* 诊断摘要 */}
          {summary && (
            <div className="mb-3">
              <p className="text-xs text-slate-300 leading-relaxed">{summary}</p>
            </div>
          )}

          {/* 消息预览 */}
          {message_preview && (
            <div className="mb-3">
              <p className="text-xs text-slate-500 mb-1">问题消息预览 (前 200 字符)：</p>
              <div className="rounded bg-slate-900/60 border border-slate-700/30 p-2 max-h-24 overflow-y-auto">
                <pre className="text-xs text-slate-400 whitespace-pre-wrap break-all font-mono">
                  {message_preview}
                </pre>
              </div>
            </div>
          )}

          {/* 诊断步骤详情 */}
          {hasSteps && (
            <div>
              <button
                type="button"
                onClick={() => setStepsExpanded(!stepsExpanded)}
                aria-expanded={stepsExpanded}
                aria-controls="diagnostic-steps-detail"
                aria-label={stepsExpanded ? "收起诊断步骤详情" : "展开诊断步骤详情"}
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-400 transition-colors cursor-pointer mb-2 min-h-[44px]"
              >
                {stepsExpanded ? (
                  <ChevronDown size={12} aria-hidden="true" />
                ) : (
                  <ChevronRight size={12} aria-hidden="true" />
                )}
                <span>
                  诊断步骤详情 (共 {steps.length} 步，
                  <span className="text-emerald-400 ml-0.5">{passedSteps} 通过</span>
                  <span className="text-slate-500">, </span>
                  <span className="text-red-400">{blockedSteps} 拦截</span>
                  )
                </span>
              </button>

              {stepsExpanded && (
                <div id="diagnostic-steps-detail" role="region" aria-label="诊断步骤详情" className="space-y-1.5">
                  {steps.map((s) => {
                    const isBlocked = s.result === "拦截";
                    return (
                      <div
                        key={s.step}
                        className={`flex items-start gap-2 px-2 py-1 rounded text-xs ${
                          isBlocked ? "bg-red-500/5" : "bg-slate-800/40"
                        }`}
                      >
                        <span className="text-slate-500 font-mono w-5 flex-shrink-0">
                          #{s.step}
                        </span>
                        <span className="flex-1 text-slate-400 break-all">{s.subset}</span>
                        <span
                          className={`flex-shrink-0 font-medium ${
                            isBlocked ? "text-red-400" : "text-emerald-400"
                          }`}
                        >
                          {s.result}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 分割线 */}
      <div className="flex-1 h-px bg-emerald-500/30" />
    </div>
  );
}
