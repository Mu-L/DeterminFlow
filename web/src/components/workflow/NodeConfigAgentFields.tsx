import type { WorkflowNodeDef, WorkflowVariable } from "../../types";
import {
  FieldHookButton,
  VarInput,
  VarTextarea,
} from "./NodeConfigVariableInputs";
import { generateVarKey } from "./nodeConfigUtils";

interface AgentTypeOption {
  agent_type: string;
  description: string;
  template_variables?: {
    key: string;
    name: string;
    description: string;
    default: string;
    required: boolean;
  }[];
}

interface NodeConfigAgentFieldsProps {
  node: WorkflowNodeDef;
  variables: WorkflowVariable[];
  varBindings: NonNullable<WorkflowNodeDef["var_bindings"]>;
  agentTypeOptions: AgentTypeOption[];
  agentType: string;
  setAgentType: (value: string) => void;
  systemPrompt: string;
  setSystemPrompt: (value: string) => void;
  firstMessage: string;
  setFirstMessage: (value: string) => void;
  modelOverride: string;
  setModelOverride: (value: string) => void;
  modelOptions: { value: string; label: string }[];
  autoFlow: boolean;
  setAutoFlow: (value: boolean) => void;
  enableCompleteNodeTask: boolean;
  setEnableCompleteNodeTask: (value: boolean) => void;
  outputVariable: string;
  setOutputVariable: (value: string) => void;
  saveOutputToFile: boolean;
  setSaveOutputToFile: (value: boolean) => void;
  outputFilePath: string;
  setOutputFilePath: (value: string) => void;
  enableRejectUpstream: boolean;
  setEnableRejectUpstream: (value: boolean) => void;
  maxRejectCount: string;
  setMaxRejectCount: (value: string) => void;
  isReadOnly: boolean;
  readOnlyInput: string;
  baseInputClass: string;
  hookedInputClass: string;
  onUpdate: (nodeId: string, updates: Partial<WorkflowNodeDef>) => void;
  onHookToggle: (field: string, currentValue: string) => void;
  onMarkUnsaved: () => void;
}

function boundVariableValue(
  node: WorkflowNodeDef,
  variables: WorkflowVariable[],
  field: string,
): string {
  const binding = node.var_bindings?.[field];
  const variableKey = binding?.var_key
    || generateVarKey(node.id, field, variables.map((variable) => variable.key));
  return `{{${variableKey}}}`;
}

export default function NodeConfigAgentFields({
  node,
  variables,
  varBindings,
  agentTypeOptions,
  agentType,
  setAgentType,
  systemPrompt,
  setSystemPrompt,
  firstMessage,
  setFirstMessage,
  modelOverride,
  setModelOverride,
  modelOptions,
  autoFlow,
  setAutoFlow,
  enableCompleteNodeTask,
  setEnableCompleteNodeTask,
  outputVariable,
  setOutputVariable,
  saveOutputToFile,
  setSaveOutputToFile,
  outputFilePath,
  setOutputFilePath,
  enableRejectUpstream,
  setEnableRejectUpstream,
  maxRejectCount,
  setMaxRejectCount,
  isReadOnly,
  readOnlyInput,
  baseInputClass,
  hookedInputClass,
  onUpdate,
  onHookToggle,
  onMarkUnsaved,
}: NodeConfigAgentFieldsProps) {
  const selectedAgent = agentTypeOptions.find((option) => option.agent_type === agentType);
  const templateVariables = selectedAgent?.template_variables || [];

  return (
    <>
      <div>
        <label htmlFor="agent-type-select" className="block text-xs font-medium text-slate-400 mb-1.5">
          Agent 类型
        </label>
        <div className="relative">
          {varBindings.agent_type ? (
            <input
              type="text"
              id="agent-type-select"
              value={boundVariableValue(node, variables, "agent_type")}
              readOnly
              aria-label="Agent 类型（已绑定变量）"
              className={`${baseInputClass} ${hookedInputClass} pr-9 w-full`}
            />
          ) : (
            <select
              id="agent-type-select"
              value={agentType}
              onChange={(event) => setAgentType(event.target.value)}
              disabled={isReadOnly}
              aria-label="选择 Agent 类型"
              className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none pr-9 ${
                isReadOnly ? "pointer-events-none opacity-60" : ""
              }`}
            >
              {agentTypeOptions.length > 0
                ? agentTypeOptions.map((option) => (
                    <option key={option.agent_type} value={option.agent_type}>
                      {option.agent_type}{option.description ? ` — ${option.description}` : ""}
                    </option>
                  ))
                : ["default", "coder", "reviewer", "researcher", "reader"].map((type) => (
                    <option key={type} value={type}>{type}</option>
                  ))}
            </select>
          )}
          <FieldHookButton
            field="agent_type"
            currentValue={agentType}
            isHooked={Boolean(varBindings.agent_type)}
            isReadOnly={isReadOnly}
            onToggle={onHookToggle}
          />
        </div>
      </div>

      {templateVariables.length > 0 && (
        <div className="space-y-3 pt-3 border-t border-indigo-500/10">
          <label className="block text-xs font-medium text-slate-400 mb-1.5">
            自定义变量块
            <span className="text-slate-500"> (该 Agent 模板声明的可填充变量)</span>
          </label>
          {templateVariables.map((templateVariable) => {
            let templateValues: Record<string, string> = {};
            try {
              const raw: unknown = node.node_params?.template_values;
              if (typeof raw === "string") {
                templateValues = JSON.parse(raw) as Record<string, string>;
              } else if (raw && typeof raw === "object") {
                templateValues = raw as Record<string, string>;
              }
            } catch {
              templateValues = {};
            }
            const currentValue = templateValues[templateVariable.key]
              || templateVariable.default
              || "";
            return (
              <div key={templateVariable.key}>
                <div className="flex items-center gap-2 mb-1">
                  <code className="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-500 text-xs font-mono">
                    {`{{${templateVariable.key}}}`}
                  </code>
                  <span className="text-xs text-slate-400">{templateVariable.name}</span>
                  {templateVariable.required && <span className="text-xs text-red-400">必填</span>}
                </div>
                {templateVariable.description && (
                  <p className="text-xs text-slate-500 mb-1.5">{templateVariable.description}</p>
                )}
                <VarTextarea
                  value={currentValue}
                  onChange={(value) => {
                    const nextValues = { ...templateValues, [templateVariable.key]: value };
                    onUpdate(node.id, {
                      node_params: {
                        ...node.node_params,
                        template_values: JSON.stringify(nextValues),
                      },
                    });
                  }}
                  placeholder={`输入 ${templateVariable.name} 内容，可使用 {{key}} 引用工作流变量...`}
                  readOnly={isReadOnly}
                  readOnlyClass={readOnlyInput}
                  inputClass={baseInputClass}
                  rows={3}
                  variables={variables}
                />
              </div>
            );
          })}
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">
          System Prompt 补充
          <span className="text-slate-500"> (可选)</span>
        </label>
        <div className="relative">
          {varBindings.system_prompt_template ? (
            <textarea
              value={boundVariableValue(node, variables, "system_prompt_template")}
              readOnly
              rows={4}
              className={`${baseInputClass} ${hookedInputClass} pr-9 resize-none w-full`}
            />
          ) : (
            <VarTextarea
              value={systemPrompt}
              onChange={setSystemPrompt}
              placeholder="注入到 Agent system prompt 的额外指令，可使用 {{key}} 引用变量..."
              readOnly={isReadOnly}
              readOnlyClass={readOnlyInput}
              inputClass={`${baseInputClass} pr-9`}
              rows={4}
              variables={variables}
            />
          )}
          <FieldHookButton
            field="system_prompt_template"
            currentValue={systemPrompt}
            isHooked={Boolean(varBindings.system_prompt_template)}
            isReadOnly={isReadOnly}
            onToggle={onHookToggle}
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">
          任务消息 {!isReadOnly && <span className="text-red-400">*</span>}
        </label>
        <div className="relative">
          {varBindings.first_message ? (
            <textarea
              value={boundVariableValue(node, variables, "first_message")}
              readOnly
              rows={5}
              className={`${baseInputClass} ${hookedInputClass} pr-9 resize-none w-full`}
            />
          ) : (
            <VarTextarea
              value={firstMessage}
              onChange={setFirstMessage}
              placeholder="Agent 将收到的首条任务消息，可使用 {{key}} 引用变量..."
              readOnly={isReadOnly}
              readOnlyClass={readOnlyInput}
              inputClass={`${baseInputClass} pr-9`}
              rows={5}
              variables={variables}
            />
          )}
          <FieldHookButton
            field="first_message"
            currentValue={firstMessage}
            isHooked={Boolean(varBindings.first_message)}
            isReadOnly={isReadOnly}
            onToggle={onHookToggle}
          />
        </div>
      </div>

      <div className="space-y-3 pt-3 border-t border-indigo-500/10">
        <div>
          <label htmlFor="model-override-select" className="block text-xs font-medium text-slate-400 mb-1.5">
            模型覆盖
            <span className="text-slate-500"> (可选，覆盖 Agent 类型的默认模型)</span>
          </label>
          <div className="relative">
            {varBindings.model_override ? (
              <input
                type="text"
                id="model-override-select"
                value={boundVariableValue(node, variables, "model_override")}
                readOnly
                aria-label="模型覆盖（已绑定变量）"
                className={`${baseInputClass} ${hookedInputClass} pr-9 w-full`}
              />
            ) : (
              <select
                id="model-override-select"
                value={modelOverride}
                onChange={(event) => setModelOverride(event.target.value)}
                disabled={isReadOnly}
                aria-label="选择覆盖模型"
                className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none pr-9 ${
                  isReadOnly ? "pointer-events-none opacity-60" : ""
                }`}
              >
                <option value="">使用 Agent 类型默认模型</option>
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            )}
            <FieldHookButton
              field="model_override"
              currentValue={modelOverride}
              isHooked={Boolean(varBindings.model_override)}
              isReadOnly={isReadOnly}
              onToggle={onHookToggle}
            />
          </div>
        </div>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={autoFlow}
            onChange={(event) => {
              setAutoFlow(event.target.checked);
              onMarkUnsaved();
            }}
            disabled={isReadOnly}
            className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
          />
          <div>
            <span className="text-sm text-slate-100">自动流转</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              开启后 Agent 输出完成即视为成功，无需调用 complete_node_task 工具。
              LLM 仍可显式调用工具标记失败。
            </p>
          </div>
        </label>

        {autoFlow && (
          <label className="flex items-start gap-3 cursor-pointer ml-1">
            <input
              type="checkbox"
              checked={enableCompleteNodeTask}
              onChange={(event) => {
                setEnableCompleteNodeTask(event.target.checked);
                onMarkUnsaved();
              }}
              disabled={isReadOnly}
              className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
            />
            <div>
              <span className="text-sm text-slate-100">注入 complete_node_task 工具</span>
              <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
                关闭后 Agent 无法显式调用完成/失败工具。
              </p>
            </div>
          </label>
        )}

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={Boolean(outputVariable)}
            onChange={(event) => {
              if (!event.target.checked) {
                setOutputVariable("");
              } else {
                const nodeIdPart = node.id.length > 8 ? node.id.slice(-8) : node.id;
                setOutputVariable(`agent_${nodeIdPart}_output`);
              }
              onMarkUnsaved();
            }}
            disabled={isReadOnly}
            className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
          />
          <div>
            <span className="text-sm text-slate-100">最后输出加载为变量</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              开启后，Agent 最后一轮回复文本将写入指定变量，供后续节点通过
              {"{{变量名}}"} 引用。
            </p>
          </div>
        </label>

        {outputVariable && (
          <div className="ml-7">
            <label htmlFor="output-variable-name" className="block text-xs font-medium text-slate-400 mb-1">
              变量名
            </label>
            <input
              type="text"
              id="output-variable-name"
              value={outputVariable}
              onChange={(event) => {
                setOutputVariable(event.target.value);
                onMarkUnsaved();
              }}
              disabled={isReadOnly}
              aria-label="输出变量名"
              className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors ${
                isReadOnly ? "pointer-events-none opacity-60" : ""
              }`}
              placeholder="agent_nodeid_output"
            />
          </div>
        )}
      </div>

      <div className="space-y-3 pt-3 border-t border-indigo-500/10">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={saveOutputToFile}
            onChange={(event) => {
              setSaveOutputToFile(event.target.checked);
              onMarkUnsaved();
            }}
            disabled={isReadOnly}
            className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
          />
          <div>
            <span className="text-sm text-slate-100">保存最后输出到文件</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              开启后，Agent 最后一轮回复文本将保存到指定文件。
              支持绝对路径，或以 workspace 为基准的相对路径。
            </p>
          </div>
        </label>

        {saveOutputToFile && (
          <div className="ml-7">
            <label htmlFor="output-file-path" className="block text-xs font-medium text-slate-400 mb-1">
              文件路径
            </label>
            <VarInput
              value={outputFilePath}
              onChange={(value) => {
                setOutputFilePath(value);
                onMarkUnsaved();
              }}
              placeholder="例如: /absolute/path/output.txt 或 relative/path/result.md"
              readOnly={isReadOnly}
              readOnlyClass={readOnlyInput}
              inputClass={baseInputClass}
              variables={variables}
            />
            <p className="text-xs text-slate-500 mt-1">
              支持 {"{{key}}"} 占位符引用工作流变量。绝对路径直接使用，相对路径以 workspace 为基准。
            </p>
          </div>
        )}
      </div>

      <div className="space-y-3 pt-3 border-t border-indigo-500/10">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={enableRejectUpstream}
            onChange={(event) => {
              setEnableRejectUpstream(event.target.checked);
              onMarkUnsaved();
            }}
            disabled={isReadOnly}
            className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
          />
          <div>
            <span className="text-sm text-slate-100">注入 reject_upstream 工具</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              开启后 Agent 可以拒绝上游节点的产出，触发工作流回滚重试。
            </p>
          </div>
        </label>

        {enableRejectUpstream && (
          <div className="ml-7">
            <label htmlFor="max-reject-count" className="block text-xs font-medium text-slate-400 mb-1">
              最大拒绝次数
            </label>
            <input
              type="number"
              id="max-reject-count"
              min="1"
              max="100"
              value={maxRejectCount}
              onChange={(event) => {
                setMaxRejectCount(event.target.value);
                onMarkUnsaved();
              }}
              disabled={isReadOnly}
              className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors ${
                isReadOnly ? "pointer-events-none opacity-60" : ""
              }`}
              placeholder="3"
            />
            <p className="text-xs text-slate-500 mt-1">
              允许上游节点被拒绝的最大次数，超过后将无法继续拒绝。
            </p>
          </div>
        )}
      </div>
    </>
  );
}
