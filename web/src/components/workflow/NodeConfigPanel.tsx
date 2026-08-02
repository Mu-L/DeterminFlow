/**
 * NodeConfigPanel - 节点属性编辑/查看侧边面板
 *
 * - Agent 类型选项从 /api/workflows/agent-types/list 动态获取
 * - 只读模式（运行中）：可查看不可编辑，显示当前配置
 * - 编辑模式：可修改并保存
 * - 支持变量占位符 {{key}} 高亮 + 自动补全
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { X, Eye, EyeOff, GripVertical } from "lucide-react";
import type {
  ExecutionScheme,
  ScriptLibraryGroup,
  ScriptLibraryScript,
  WorkflowNodeDef,
  WorkflowSummary,
  WorkflowVariable,
} from "../../types";
import { useAgentTypes } from "../../hooks/useAgentTypes";
import { fetchScriptLibraryGroups, fetchScriptLibraryScripts, getAllModels } from "../../lib/api";
import NodeConfigAgentFields from "./NodeConfigAgentFields";
import NodeConfigScriptFields from "./NodeConfigScriptFields";
import NodeFailurePolicyFields from "./NodeFailurePolicyFields";
import {
  FieldHookButton,
  VarInput,
} from "./NodeConfigVariableInputs";
import {
  buildScriptNodeParams,
  generateVarKey,
  nodeParamString,
  scriptArgvParam,
} from "./nodeConfigUtils";

interface NodeConfigPanelProps {
  node: WorkflowNodeDef;
  isReadOnly?: boolean;
  onClose: () => void;
  onUpdate: (nodeId: string, updates: Partial<WorkflowNodeDef>) => void;
  onDelete?: (nodeId: string) => void;
  /** 工作流 ID（脚本节点需要用于加载/保存脚本文件） */
  workflowId?: string;
  /** 工作流定义的变量列表（用于 {{key}} 自动补全） */
  variables?: WorkflowVariable[];
  /** 当前节点已转为变量的字段绑定 */
  varBindings?: Record<string, { original_value: string; var_key?: string }>;
  /** 变量变更回调：创建或删除全局变量 */
  onVarChange?: (action: "create" | "delete", variable: WorkflowVariable, field: string, originalValue: string) => void;
}

// 字段中文名映射
const FIELD_LABELS: Record<string, string> = {
  label: "节点名称",
  agent_type: "Agent 类型",
  system_prompt_template: "System Prompt 补充",
  first_message: "任务消息",
  model_override: "模型覆盖",
};

/** 生成唯一变量展示名 */
function generateVarName(nodeLabel: string, fieldLabel: string, existingNames: string[]): string {
  let name = `${nodeLabel} - ${fieldLabel}`;
  let counter = 1;
  while (existingNames.includes(name)) {
    name = `${nodeLabel} - ${fieldLabel} (${counter})`;
    counter++;
  }
  return name;
}

// ============ Main Component ============

export default function NodeConfigPanel({
  node,
  isReadOnly = false,
  onClose,
  onUpdate,
  onDelete,
  workflowId = "",
  variables = [],
  varBindings = {},
  onVarChange,
}: NodeConfigPanelProps) {
  const [width, setWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);

  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;
      const newWidth = e.clientX;
      setWidth(Math.max(280, Math.min(700, newWidth)));
    };
    const handleMouseUp = () => setIsResizing(false);
    if (isResizing) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    }
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

  const nt = node.node_type || "agent";
  const [label, setLabel] = useState(node.label);
  const [agentType, setAgentType] = useState(node.agent_type);
  const [systemPrompt, setSystemPrompt] = useState(node.system_prompt_template);
  const [firstMessage, setFirstMessage] = useState(node.first_message);
  // Agent auto-flow / output variable
  const [autoFlow, setAutoFlow] = useState(node.auto_flow || false);
  const [enableCompleteNodeTask, setEnableCompleteNodeTask] = useState(
    node.enable_complete_node_task !== false,
  );
  const [outputVariable, setOutputVariable] = useState(node.output_variable || "");
  // Agent reject_upstream
  const [enableRejectUpstream, setEnableRejectUpstream] = useState(
    node.enable_reject_upstream || false,
  );
  const [maxRejectCount, setMaxRejectCount] = useState(
    node.max_reject_count != null ? String(node.max_reject_count) : "3",
  );
  // Agent save output to file
  const [saveOutputToFile, setSaveOutputToFile] = useState(
    node.save_output_to_file || false,
  );
  const [outputFilePath, setOutputFilePath] = useState(
    node.output_file_path || "",
  );
  // Model override
  const [modelOverride, setModelOverride] = useState(node.model_override || "");
  const [modelOptions, setModelOptions] = useState<{ value: string; label: string }[]>([]);
  // Approval node specific
  const [filePaths, setFilePaths] = useState(nodeParamString(node.node_params, "file_paths"));
  const [rejectionPlaceholder, setRejectionPlaceholder] = useState(
    nodeParamString(node.node_params, "rejection_reason_placeholder", "请输入驳回原因..."),
  );
  // Script node specific
  const initialScriptArgv = scriptArgvParam(node.node_params);
  const [scriptSource, setScriptSource] = useState(nodeParamString(node.node_params, "script_source", "inline"));
  const [scriptType, setScriptType] = useState(nodeParamString(node.node_params, "script_type", "shell"));
  const [scriptName, setScriptName] = useState(nodeParamString(node.node_params, "script_name"));
  const [scriptGroup, setScriptGroup] = useState(nodeParamString(node.node_params, "script_group"));
  const [scriptArgs, setScriptArgs] = useState(nodeParamString(node.node_params, "script_args"));
  const [scriptArgv, setScriptArgv] = useState<string[]>(initialScriptArgv || []);
  const [useScriptArgv, setUseScriptArgv] = useState(
    initialScriptArgv !== null || typeof node.node_params?.script_args !== "string",
  );
  const [timeout, setTimeout_] = useState(nodeParamString(node.node_params, "timeout", "300"));
  const [scriptContent, setScriptContent] = useState("");
  const [scriptLoaded, setScriptLoaded] = useState(false);
  // Script library data
  const [libGroups, setLibGroups] = useState<ScriptLibraryGroup[]>([]);
  const [libScripts, setLibScripts] = useState<ScriptLibraryScript[]>([]);
  // Subprocess node specific
  const [subWorkflowId, setSubWorkflowId] = useState(node.sub_workflow_id || "");
  const [subSchemeId, setSubSchemeId] = useState(node.sub_scheme_id || "");
  const [subWorkflowParams, setSubWorkflowParams] = useState<Record<string, { value: string; use_default: boolean }>>(
    node.sub_workflow_params || {}
  );
  const [autoRetryCount, setAutoRetryCount] = useState(node.auto_retry_count ?? 0);
  const [autoRetryIntervalSeconds, setAutoRetryIntervalSeconds] = useState(
    node.auto_retry_interval_seconds ?? 0,
  );
  const [failAutoSkip, setFailAutoSkip] = useState(node.fail_auto_skip || false);
  const [subVisibleVars, setSubVisibleVars] = useState<WorkflowVariable[]>([]);
  const [subSchemesOptions, setSubSchemesOptions] = useState<{ id: string; name: string; count: number }[]>([]);
  const [workflowOptions, setWorkflowOptions] = useState<{ workflow_id: string; name: string }[]>([]);
  const [saved, setSaved] = useState(false);
  const markUnsaved = useCallback(() => setSaved(false), []);
  const { agentTypes: agentTypeOptions } = useAgentTypes();
  // 加载可用模型列表
  useEffect(() => {
    getAllModels().then((res) => {
      if (res.models && Array.isArray(res.models)) {
        setModelOptions(res.models.map((m) => ({
          value: m.value,
          label: m.label,
        })));
      }
    }).catch(() => {
      // 保持空列表，用户可手动输入
    });
  }, []);
  // 跟踪面板打开时的初始 outputVariable，用于保存时检测变更并同步到变量列表
  const initialOutputVarRef = useRef<string>('');
  // 跟踪当前加载的脚本名，用于判断 node 变更时是否需要重新加载脚本
  const loadedScriptNameRef = useRef<string>('');

  // Sync state when node changes
  useEffect(() => {
    setLabel(node.label);
    setAgentType(node.agent_type);
    setSystemPrompt(node.system_prompt_template);
    setFirstMessage(node.first_message);
    setAutoFlow(node.auto_flow || false);
    setEnableCompleteNodeTask(node.enable_complete_node_task !== false);
    const initOut = node.output_variable || "";
    setOutputVariable(initOut);
    initialOutputVarRef.current = initOut;
    const currentNodeType = node.node_type || "agent";
    const currentNodeParams = node.node_params || {};
    setEnableRejectUpstream(
      currentNodeType === "script"
        ? !!currentNodeParams.enable_reject_upstream
        : node.enable_reject_upstream || false
    );
    setMaxRejectCount(
      currentNodeType === "script"
        ? String(currentNodeParams.max_reject_count ?? "3")
        : (node.max_reject_count != null ? String(node.max_reject_count) : "3")
    );
    setSaveOutputToFile(node.save_output_to_file || false);
    setOutputFilePath(node.output_file_path || "");
    setModelOverride(node.model_override || "");
    setFilePaths(nodeParamString(node.node_params, "file_paths"));
    setRejectionPlaceholder(nodeParamString(node.node_params, "rejection_reason_placeholder", "请输入驳回原因..."));
    setScriptSource(nodeParamString(node.node_params, "script_source", "inline"));
    setScriptType(nodeParamString(node.node_params, "script_type", "shell"));
    setScriptArgs(nodeParamString(node.node_params, "script_args"));
    const nextScriptArgv = scriptArgvParam(node.node_params);
    setScriptArgv(nextScriptArgv || []);
    setUseScriptArgv(nextScriptArgv !== null || typeof node.node_params?.script_args !== "string");
    setTimeout_(nodeParamString(node.node_params, "timeout", "300"));

    // Sync script_group for library scripts
    setScriptGroup(nodeParamString(node.node_params, "script_group"));

    const newScriptName = nodeParamString(node.node_params, "script_name");
    setScriptName(newScriptName);
    // 仅当脚本名变更时才重置加载状态，避免保存后 node 引用变更导致闪烁
    if (newScriptName !== loadedScriptNameRef.current) {
      setScriptContent("");
      setScriptLoaded(false);
      loadedScriptNameRef.current = newScriptName;
    }
    // Subprocess fields
    const newSubWfId = node.sub_workflow_id || "";
    setSubWorkflowId(newSubWfId);
    setSubSchemeId(node.sub_scheme_id || "");
    const newSubParams = node.sub_workflow_params || {};
    setSubWorkflowParams(newSubParams);
    setAutoRetryCount(node.auto_retry_count ?? 0);
    setAutoRetryIntervalSeconds(node.auto_retry_interval_seconds ?? 0);
    setFailAutoSkip(node.fail_auto_skip || false);
  }, [node]);

  // Load script content when script node is opened
  useEffect(() => {
    if (nt !== "script" || !scriptName.trim()) {
      setScriptContent("");
      setScriptLoaded(false);
      return;
    }
    setScriptLoaded(false);
    const controller = new AbortController();
    fetch(
      `/api/workflows/${encodeURIComponent(workflowId)}/script/${encodeURIComponent(scriptName)}?type=${encodeURIComponent(scriptType)}`
    )
      .then((res) => {
        if (!res.ok) throw new Error("Load failed");
        return res.json();
      })
      .then((data: { content: string; exists: boolean }) => {
        if (!controller.signal.aborted) {
          setScriptContent(data.content || "");
          setScriptLoaded(true);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setScriptContent("");
          setScriptLoaded(true);
        }
      });
    return () => controller.abort();
  }, [nt, scriptName, scriptType, node.id, workflowId]);

  // Load script library data when script node is opened
  useEffect(() => {
    if (nt !== "script") return;
    const controller = new AbortController();
    fetchScriptLibraryGroups().then(groups => {
      if (!controller.signal.aborted) setLibGroups(groups);
    }).catch(() => {});
    fetchScriptLibraryScripts().then(scripts => {
      if (!controller.signal.aborted) setLibScripts(scripts);
    }).catch(() => {});
    return () => controller.abort();
  }, [nt]);

  // Load workflow list for subprocess node target selector
  useEffect(() => {
    if (nt !== "subprocess") return;
    const controller = new AbortController();
    fetch("/api/workflows")
      .then((res) => res.json())
      .then((data: WorkflowSummary[]) => {
        if (!controller.signal.aborted) {
          setWorkflowOptions(
            data
              .filter((wf) => wf.workflow_id !== workflowId)
              .map(({ workflow_id, name }) => ({ workflow_id, name })),
          );
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [nt, workflowId]);

  // Load visible variables and schemes when subprocess target is selected
  useEffect(() => {
    if (nt !== "subprocess" || !subWorkflowId) {
      setSubVisibleVars([]);
      setSubSchemesOptions([]);
      return;
    }
    const controller = new AbortController();
    // Load schemes for the target workflow
    fetch(`/api/workflows/${subWorkflowId}/schemes`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : [])
      .then((schemes: ExecutionScheme[]) => {
        if (!controller.signal.aborted) {
          setSubSchemesOptions(schemes.map((scheme) => ({
            id: scheme.id,
            name: scheme.name,
            count: scheme.selected_node_ids.length,
          })));
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setSubSchemesOptions([]);
      });
    fetch(`/api/workflows/${encodeURIComponent(subWorkflowId)}/visible-variables`, {
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error("Load failed");
        return res.json();
      })
      .then((vars: WorkflowVariable[]) => {
        if (!controller.signal.aborted) {
          setSubVisibleVars(vars);
          // Initialize params if not already set
          if (vars.length > 0) {
            setSubWorkflowParams((currentParams) => {
              if (Object.keys(currentParams).length > 0) return currentParams;
              const initialParams: Record<string, { value: string; use_default: boolean }> = {};
              for (const variable of vars) {
                initialParams[variable.key] = {
                  value: variable.default || "",
                  use_default: false,
                };
              }
              return initialParams;
            });
          }
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setSubVisibleVars([]);
      });
    return () => controller.abort();
  }, [nt, subWorkflowId]);

  const handleSave = async () => {
    if (isReadOnly) return;
    const updates: Partial<WorkflowNodeDef> = {
      label,
      node_type: nt,
      agent_type: agentType,
      system_prompt_template: systemPrompt,
      first_message: firstMessage,
      position: node.position,
      var_bindings: varBindings,
      auto_retry_count: autoRetryCount,
      auto_retry_interval_seconds: autoRetryIntervalSeconds,
      fail_auto_skip: failAutoSkip,
    };
    if (nt === "agent" || (!["approval", "script"].includes(nt))) {
      updates.auto_flow = autoFlow;
      updates.enable_complete_node_task = enableCompleteNodeTask;
      updates.output_variable = outputVariable;
      updates.enable_reject_upstream = enableRejectUpstream;
      updates.max_reject_count = parseInt(maxRejectCount, 10) || 3;
      updates.save_output_to_file = saveOutputToFile;
      updates.output_file_path = outputFilePath;
      updates.model_override = modelOverride;

      // 输出变量名变更：同步到变量列表（delete old + create new）
      const oldOutVar = initialOutputVarRef.current;
      const newOutVar = updates.output_variable || "";
      if (oldOutVar !== newOutVar && onVarChange) {
        if (oldOutVar) {
          onVarChange("delete", {
            key: oldOutVar, name: "", type: "text", default: "",
            required: false, description: "", options: [],
            source_type: "output", source_node_id: node.id,
          }, "", "");
        }
        if (newOutVar) {
          onVarChange("create", {
            key: newOutVar,
            name: `${label || node.id} 输出`,
            type: "text", default: "",
            required: false, description: "", options: [],
            source_type: "output", source_node_id: node.id,
          }, "", "");
        }
        initialOutputVarRef.current = newOutVar;
      }
    }
    if (nt === "approval") {
      updates.node_params = {
        file_paths: filePaths,
        rejection_reason_placeholder: rejectionPlaceholder,
      };
    } else if (nt === "script") {
      updates.node_params = buildScriptNodeParams(node.node_params, {
        scriptSource,
        scriptType,
        scriptName,
        scriptGroup,
        scriptArgs,
        scriptArgv,
        useScriptArgv,
        timeout: timeout.toString(),
        enableRejectUpstream,
        maxRejectCount,
      });
      // Save script content to file (only for inline scripts)
      if (scriptSource !== "library" && workflowId && scriptName.trim()) {
        try {
          await fetch(
            `/api/workflows/${encodeURIComponent(workflowId)}/script/${encodeURIComponent(scriptName.trim())}?type=${encodeURIComponent(scriptType)}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ content: scriptContent }),
            }
          );
        } catch (e) {
          console.error("保存脚本内容失败:", e);
        }
      }
    } else if (nt === "subprocess") {
      updates.sub_workflow_id = subWorkflowId || null;
      updates.sub_scheme_id = subSchemeId || null;
      updates.sub_workflow_params = subWorkflowParams;
    }
    onUpdate(node.id, updates);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const readOnlyInput = "pointer-events-none opacity-60 cursor-not-allowed bg-slate-950/50";
  const baseInputClass = "w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors";
  const hookedInputClass = "pointer-events-none opacity-60 cursor-not-allowed bg-slate-950/50 text-slate-400";

  /** 处理字段的转为变量/取消变量 */
  const handleHookToggle = (field: string, currentValue: string) => {
    if (isReadOnly || !onVarChange) return;

    const binding = varBindings[field];
    if (binding) {
      // 取消变量：恢复原始值，删除全局变量
      const varKey = binding.var_key || generateVarKey(node.id, field, variables.map((v) => v.key));
      onVarChange("delete", { key: varKey, name: "", type: "text", default: "", required: false, description: "", options: [] }, field, binding.original_value);
    } else {
      // 转为变量：创建全局变量，字段值设为 {{key}}
      const existingKeys = variables.map((v) => v.key);
      const existingNames = variables.map((v) => v.name);
      const varKey = generateVarKey(node.id, field, existingKeys);
      const varName = generateVarName(node.label || node.id, FIELD_LABELS[field] || field, existingNames);
      const newVar: WorkflowVariable = {
        key: varKey,
        name: varName,
        type: "text",
        default: currentValue,
        required: false,
        description: `由节点 ${node.label || node.id} 的${FIELD_LABELS[field] || field}字段自动生成`,
        options: [],
      };
      onVarChange("create", newVar, field, currentValue);
    }
  };

  return (
    <div
      className="h-full bg-slate-900 border-l border-indigo-500/20 overflow-y-auto flex flex-col shadow-2xl relative"
      style={{ width: `${width}px`, minWidth: "280px", maxWidth: "700px" }}
      role="complementary"
      aria-label="节点配置面板"
    >
      {/* Resize Handle */}
      <div
        onMouseDown={handleResizeMouseDown}
        className={`absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-indigo-500/50 transition-colors z-10 group ${
          isResizing ? "bg-indigo-500/60" : ""
        }`}
        role="separator"
        aria-orientation="vertical"
        aria-label="调整面板宽度"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") {
            e.preventDefault();
            setWidth(Math.max(280, width - 20));
          } else if (e.key === "ArrowRight") {
            e.preventDefault();
            setWidth(Math.min(700, width + 20));
          }
        }}
      >
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
          <GripVertical size={16} className="text-indigo-500" />
        </div>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-indigo-500/10">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-100">
              {isReadOnly ? "节点详情" : "节点属性"}
            </h3>
            {isReadOnly && (
              <span className="flex items-center gap-1 text-xs text-blue-500 bg-blue-500/10 px-1.5 py-0.5 rounded">
                <Eye size={10} />只读
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{node.id}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 min-h-[44px] min-w-[44px] rounded-lg hover:bg-indigo-500/10 text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
          aria-label="关闭面板"
        >
          <X size={16} />
        </button>
      </div>

      {/* Form */}
      <div className="flex-1 p-4 space-y-4">
        {/* Node type badge */}
        <div className="flex items-center gap-2 mb-1">
          <span
            className={`text-xs px-2 py-0.5 rounded font-medium ${
              nt === "approval"
                ? "bg-amber-500/15 text-amber-500"
                : nt === "script"
                  ? "bg-cyan-500/15 text-cyan-500"
                  : "bg-indigo-500/15 text-indigo-500"
            }`}
          >
            {nt === "approval" ? "审批节点" :
             nt === "script" ? "脚本执行" :
             "Agent 节点"}
          </span>
        </div>

        {/* Label */}
        <div>
          <label htmlFor="node-label" className="block text-xs font-medium text-slate-400 mb-1.5">节点名称</label>
          <div className="relative">
            {varBindings["label"] ? (
              <input
                type="text"
                id="node-label"
                value={`{{${varBindings["label"].var_key || generateVarKey(node.id, "label", variables.map((v) => v.key))}}}`}
                readOnly
                aria-label="节点名称（已绑定变量）"
                className={`${baseInputClass} ${hookedInputClass} pr-9 w-full`}
              />
            ) : (
              <VarInput
                value={label}
                onChange={setLabel}
                placeholder="例如: 实现 {{task_name}}"
                readOnly={isReadOnly}
                readOnlyClass={readOnlyInput}
                inputClass={`${baseInputClass} pr-9`}
                variables={variables}
              />
            )}
            <FieldHookButton
              field="label"
              currentValue={label}
              isHooked={Boolean(varBindings.label)}
              isReadOnly={isReadOnly}
              onToggle={handleHookToggle}
            />
          </div>
        </div>

        <NodeFailurePolicyFields
          autoRetryCount={autoRetryCount}
          autoRetryIntervalSeconds={autoRetryIntervalSeconds}
          failAutoSkip={failAutoSkip}
          isReadOnly={isReadOnly}
          onAutoRetryCountChange={setAutoRetryCount}
          onAutoRetryIntervalSecondsChange={setAutoRetryIntervalSeconds}
          onFailAutoSkipChange={setFailAutoSkip}
          onMarkUnsaved={markUnsaved}
        />

        {/* ===== Agent node fields ===== */}
        {nt === "agent" && (
          <NodeConfigAgentFields
            node={node}
            variables={variables}
            varBindings={varBindings}
            agentTypeOptions={agentTypeOptions}
            agentType={agentType}
            setAgentType={setAgentType}
            systemPrompt={systemPrompt}
            setSystemPrompt={setSystemPrompt}
            firstMessage={firstMessage}
            setFirstMessage={setFirstMessage}
            modelOverride={modelOverride}
            setModelOverride={setModelOverride}
            modelOptions={modelOptions}
            autoFlow={autoFlow}
            setAutoFlow={setAutoFlow}
            enableCompleteNodeTask={enableCompleteNodeTask}
            setEnableCompleteNodeTask={setEnableCompleteNodeTask}
            outputVariable={outputVariable}
            setOutputVariable={setOutputVariable}
            saveOutputToFile={saveOutputToFile}
            setSaveOutputToFile={setSaveOutputToFile}
            outputFilePath={outputFilePath}
            setOutputFilePath={setOutputFilePath}
            enableRejectUpstream={enableRejectUpstream}
            setEnableRejectUpstream={setEnableRejectUpstream}
            maxRejectCount={maxRejectCount}
            setMaxRejectCount={setMaxRejectCount}
            isReadOnly={isReadOnly}
            readOnlyInput={readOnlyInput}
            baseInputClass={baseInputClass}
            hookedInputClass={hookedInputClass}
            onUpdate={onUpdate}
            onHookToggle={handleHookToggle}
            onMarkUnsaved={markUnsaved}
          />
        )}
        {/* ===== Approval node fields ===== */}
        {nt === "approval" && (
          <>
            {/* File paths */}
            <div>
              <label htmlFor="file-paths" className="block text-xs font-medium text-slate-400 mb-1.5">
                要展示的文件路径
                <span className="text-slate-500"> (可选)</span>
              </label>
              <textarea
                id="file-paths"
                value={filePaths}
                onChange={(e) => setFilePaths(e.target.value)}
                disabled={isReadOnly}
                rows={4}
                aria-label="要展示的文件路径"
                className={`${baseInputClass} resize-none ${
                  isReadOnly ? "pointer-events-none opacity-60" : ""
                }`}
                placeholder="/path/to/file.md&#10;/path/to/another.md"
              />
              <p className="text-xs text-slate-500 mt-1">
                每行一个文件路径（相对于工作流 workspace 根目录）
              </p>
            </div>

            {/* Rejection reason placeholder */}
            <div>
              <label htmlFor="rejection-placeholder" className="block text-xs font-medium text-slate-400 mb-1.5">
                驳回原因输入框提示文案
              </label>
              <input
                type="text"
                id="rejection-placeholder"
                value={rejectionPlaceholder}
                onChange={(e) => setRejectionPlaceholder(e.target.value)}
                disabled={isReadOnly}
                aria-label="驳回原因输入框提示文案"
                className={`${baseInputClass} ${
                  isReadOnly ? "pointer-events-none opacity-60" : ""
                }`}
                placeholder="请输入驳回原因..."
              />
            </div>
          </>
        )}

        {/* ===== Script node fields ===== */}
        {nt === "script" && (
          <NodeConfigScriptFields
            variables={variables}
            libGroups={libGroups}
            libScripts={libScripts}
            scriptSource={scriptSource}
            setScriptSource={setScriptSource}
            scriptType={scriptType}
            setScriptType={setScriptType}
            scriptName={scriptName}
            setScriptName={setScriptName}
            scriptGroup={scriptGroup}
            setScriptGroup={setScriptGroup}
            scriptArgs={scriptArgs}
            setScriptArgs={setScriptArgs}
            scriptArgv={scriptArgv}
            setScriptArgv={setScriptArgv}
            useScriptArgv={useScriptArgv}
            timeout={timeout}
            setTimeout={setTimeout_}
            scriptContent={scriptContent}
            setScriptContent={setScriptContent}
            scriptLoaded={scriptLoaded}
            enableRejectUpstream={enableRejectUpstream}
            setEnableRejectUpstream={setEnableRejectUpstream}
            maxRejectCount={maxRejectCount}
            setMaxRejectCount={setMaxRejectCount}
            isReadOnly={isReadOnly}
            readOnlyInput={readOnlyInput}
            baseInputClass={baseInputClass}
            onMarkUnsaved={markUnsaved}
          />
        )}
        {/* ===== Subprocess node fields ===== */}
        {nt === "subprocess" && (
          <>
            {/* Target Workflow Selector */}
            <div>
              <label htmlFor="sub-workflow-id" className="block text-xs font-medium text-slate-400 mb-1.5">
                目标流程 {!isReadOnly && <span className="text-red-400">*</span>}
              </label>
              <select
                id="sub-workflow-id"
                value={subWorkflowId}
                onChange={(e) => {
                  setSubWorkflowId(e.target.value);
                  setSaved(false);
                }}
                disabled={isReadOnly}
                aria-label="选择目标子流程模板"
                className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
                  isReadOnly ? "pointer-events-none opacity-60" : ""
                }`}
              >
                <option value="">-- 请选择流程模板 --</option>
                {workflowOptions.map((wf) => (
                  <option key={wf.workflow_id} value={wf.workflow_id}>
                    {wf.name} ({wf.workflow_id})
                  </option>
                ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">
                选择要嵌套复用的工作流模板
              </p>
            </div>

            {/* Execution Scheme Selector (only when target workflow is selected) */}
            {subSchemesOptions.length > 0 && (
              <div>
                <label htmlFor="sub-scheme-id" className="block text-xs font-medium text-slate-400 mb-1.5">
                  执行方案
                </label>
                <select
                  id="sub-scheme-id"
                  value={subSchemeId}
                  onChange={(e) => {
                    setSubSchemeId(e.target.value);
                    setSaved(false);
                  }}
                  disabled={isReadOnly}
                  aria-label="选择子流程的执行方案"
                  className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
                    isReadOnly ? "pointer-events-none opacity-60" : ""
                  }`}
                >
                  <option value="">全部执行</option>
                  {subSchemesOptions.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.count} 节点)
                    </option>
                  ))}
                </select>
                <p className="text-xs text-slate-500 mt-1">
                  选择"全部执行"则运行子流程所有节点
                </p>
              </div>
            )}

            {/* Visible Variables (Parameters) */}
            {subVisibleVars.length > 0 && (
              <div className="space-y-3 mt-4 border-t border-indigo-500/10 pt-4">
                <h4 className="text-xs font-semibold text-indigo-400 uppercase tracking-wider">
                  子流程参数
                </h4>
                {subVisibleVars.map((v) => {
                  const param = subWorkflowParams[v.key] || { value: v.default || "", use_default: false };
                  const isLocked = param.use_default === true;
                  return (
                    <div key={v.key}>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-medium text-slate-300">
                          {v.name || v.key}
                          {v.required && <span className="text-red-400 ml-0.5">*</span>}
                        </label>
                        <span className="text-xs text-slate-500 font-mono">{v.key}</span>
                      </div>
                      {v.description && (
                        <p className="text-xs text-slate-500 mb-1.5">{v.description}</p>
                      )}
                      <VarInput
                        value={isLocked ? (v.default || "") : param.value}
                        onChange={(newVal) => {
                          if (isLocked) return;
                          const newParams = { ...subWorkflowParams };
                          newParams[v.key] = { value: newVal, use_default: false };
                          setSubWorkflowParams(newParams);
                          setSaved(false);
                        }}
                        placeholder={v.default || `输入 ${v.name || v.key}`}
                        readOnly={isLocked || isReadOnly}
                        readOnlyClass="pointer-events-none opacity-60"
                        inputClass={baseInputClass}
                        variables={variables}
                      />
                      {/* 固定使用默认值 lock toggle */}
                      <label className="flex items-center gap-1.5 mt-1 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={isLocked}
                          disabled={isReadOnly}
                          onChange={(e) => {
                            const newParams = { ...subWorkflowParams };
                            if (e.target.checked) {
                              newParams[v.key] = { value: v.default || "", use_default: true };
                            } else {
                              newParams[v.key] = { value: v.default || "", use_default: false };
                            }
                            setSubWorkflowParams(newParams);
                            setSaved(false);
                          }}
                          className="w-3 h-3 rounded accent-indigo-500"
                        />
                        <span className="text-xs text-slate-500">
                          固定使用默认值
                          {isLocked && v.default && (
                            <span className="text-slate-600 ml-1">
                              ({v.default})
                            </span>
                          )}
                        </span>
                      </label>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* Read-only info banner */}
        {isReadOnly && (
          <div className="p-3 rounded-lg bg-blue-500/5 border border-blue-500/10">
            <div className="flex items-center gap-1.5 mb-1">
              <EyeOff size={12} className="text-blue-500" />
              <span className="text-xs font-medium text-blue-500">工作流运行中</span>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              工作流正在执行，节点配置不可编辑。等待执行完成即可恢复编辑。
            </p>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-indigo-500/10 space-y-2">
        {!isReadOnly && (
          <button
            type="button"
            onClick={handleSave}
            className={`w-full py-2.5 min-h-[44px] rounded-lg text-sm font-medium transition-all cursor-pointer ${
              saved ? "bg-green-500/20 text-green-400" : "bg-indigo-500 hover:bg-indigo-600 text-white"
            }`}
          >
            {saved ? "已保存" : "保存"}
          </button>
        )}
        {onDelete && !isReadOnly && (
          <button
            type="button"
            onClick={() => {
              if (confirm(`确认删除节点 "${node.label || node.id}" 吗？`)) {
                onDelete(node.id);
                onClose();
              }
            }}
            className="w-full py-2.5 min-h-[44px] rounded-lg text-sm font-medium bg-red-500/10 hover:bg-red-500/20 text-red-500 transition-all cursor-pointer"
          >
            删除节点
          </button>
        )}
      </div>
    </div>
  );
}
