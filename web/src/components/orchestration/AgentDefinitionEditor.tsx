import { useState, useEffect, useMemo } from "react";
import { ChevronDown, ChevronRight, Plus, Trash2, Bot, Wrench, Eye, Save, Layers, AlertCircle, CheckSquare, Square, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { AgentDefinitionData, ToolInfo, ToolGroup, SkillGroup, RuleGroup } from "../../types";
import { updateAgentVisibility, getAllModels, getModelProviders } from "../../lib/api";
import { toolGroupLabel, toolGroupColor } from "../../lib/utils-helpers";
import { useExtensions } from "@/extensions/context-value";

interface Props {
  agents: AgentDefinitionData[];
  allTools: ToolInfo[];
  groups?: ToolGroup[];
  onAgentsChange: (agents: AgentDefinitionData[]) => void;
  selectedAgentType: string | null;
  onSelectAgent: (agentType: string | null) => void;
  skillGroups: SkillGroup[];
  ruleGroups: RuleGroup[];
  availableTemplates?: string[];
  onSave?: (agentType: string, updates: Partial<AgentDefinitionData>) => Promise<{ success: boolean }>;
  defaultModelParams?: { thinking_enabled: boolean; reasoning_effort: string; temperature: number; top_p: number; presence_penalty: number; thinking_budget: number | null; response_format: { type: "text" | "json_object" } | null } | null;
}

/** Local collapsible section for tool whitelist/blacklist */
function CollapsibleSection({
  title, subtitle, defaultExpanded = false, icon, children,
}: {
  title: string;
  subtitle?: string;
  defaultExpanded?: boolean;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex items-center gap-1 w-full text-left text-xs text-muted-foreground hover:text-slate-300 transition-colors duration-200 cursor-pointer mb-1 focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
      >
        {expanded ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
        {icon}
        <span className="font-medium">{title}</span>
        {subtitle && (
          <Badge variant="outline" className="text-xs ml-1">{subtitle}</Badge>
        )}
      </button>
      {expanded && <div className="mt-1.5">{children}</div>}
    </div>
  );
}

export default function AgentDefinitionEditor({
  agents, allTools, groups = [], onAgentsChange, selectedAgentType, onSelectAgent,
  skillGroups, ruleGroups, availableTemplates = [], onSave, defaultModelParams,
}: Props) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<{ provider_id: string; model_name: string; display_name: string; value: string; category: string }[]>([]);
  const [providerReasoningEfforts, setProviderReasoningEfforts] = useState<Record<string, string[]>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const extensions = useExtensions();

  // Group tools by group_id
  const toolsByGroup = useMemo(() => {
    const grouped: Record<string, ToolInfo[]> = {};
    allTools.forEach((t) => {
      const gid = t.group_id || "__ungrouped__";
      if (!grouped[gid]) grouped[gid] = [];
      grouped[gid].push(t);
    });
    return grouped;
  }, [allTools]);

  // Get group display order
  const groupOrder = useMemo(() => {
    return groups.map((g) => g.id).filter((id) => toolsByGroup[id]);
  }, [groups, toolsByGroup]);

  useEffect(() => {
    const loadModels = async () => {
      try {
        const [modelsData, providersData] = await Promise.all([
          getAllModels(),
          getModelProviders(),
        ]);
        setAvailableModels(modelsData.models || []);
        setProviderReasoningEfforts(Object.fromEntries(
          Object.entries(providersData.providers).map(([providerId, provider]) => [
            providerId,
            provider.capabilities?.reasoning_efforts || [],
          ]),
        ));
      } catch (e) {
        console.error("Failed to load models:", e);
      }
    };
    loadModels();
  }, []);

  const updateAgent = (agentType: string, patch: Partial<AgentDefinitionData>) => {
    onAgentsChange(agents.map((a) => (a.agent_type === agentType ? { ...a, ...patch } : a)));
  };

  const toggleTool = (agentType: string, toolName: string, list: "tools" | "disallowed_tools") => {
    const agent = agents.find((a) => a.agent_type === agentType);
    if (!agent) return;
    const current = (list === "tools" ? agent.tools : agent.disallowed_tools) || [];
    const updated = current.includes(toolName)
      ? current.filter((t) => t !== toolName)
      : [...current, toolName];
    updateAgent(agentType, { [list]: updated.length > 0 ? updated : null });
  };

  const toggleGroupTools = (agentType: string, groupId: string, list: "tools" | "disallowed_tools") => {
    const agent = agents.find((a) => a.agent_type === agentType);
    if (!agent) return;
    const groupToolList = toolsByGroup[groupId];
    if (!groupToolList || groupToolList.length === 0) return;

    const current = (list === "tools" ? agent.tools : agent.disallowed_tools) || [];
    const toolNames = groupToolList.map((t) => t.name);
    const allSelected = toolNames.every((tn) => current.includes(tn));

    let updated: string[];
    if (allSelected) {
      updated = current.filter((t) => !toolNames.includes(t));
    } else {
      const existing = new Set(current);
      toolNames.forEach((tn) => existing.add(tn));
      updated = Array.from(existing);
    }

    updateAgent(agentType, { [list]: updated.length > 0 ? updated : null });
  };

  const addAgent = () => {
    const id = `custom_${Date.now()}`;
    onAgentsChange([
      ...agents,
      {
        agent_type: id,
        description: "自定义 Agent",
        prompt_template: "subagent",
        tools: null,
        disallowed_tools: null,
        model: null,
        max_turns: 10,
        system_prompt_template: "",
        copy_main_workspace: null,
        visible_skill_group_ids: null,
        visible_rule_group_ids: null,
        extension_options: null,
        model_params: defaultModelParams ? {
          thinking_enabled: defaultModelParams.thinking_enabled,
          reasoning_effort: defaultModelParams.reasoning_effort,
          temperature: defaultModelParams.temperature,
          top_p: defaultModelParams.top_p,
          presence_penalty: defaultModelParams.presence_penalty,
          thinking_budget: defaultModelParams.thinking_budget,
          response_format: defaultModelParams.response_format,
        } : null,
      },
    ]);
    setExpandedAgent(id);
  };

  const removeAgent = (agentType: string) => {
    onAgentsChange(agents.filter((a) => a.agent_type !== agentType));
    if (selectedAgentType === agentType) onSelectAgent(null);
  };

  // 根据 Agent 当前选择的模型推导 category（ds/gpt/qwen/mimo 等）
  const getAgentModelCategory = (modelValue: string | null | undefined): string => {
    if (!modelValue) return "ds"; // 未选模型时，默认按 ds 行为（向后兼容）
    const model = availableModels.find((m) => m.value === modelValue);
    return model?.category || "ds";
  };

  const effortLabels: Record<string, string> = {
    low: "低",
    medium: "中",
    high: "高",
    max: "极高",
    xhigh: "极高",
  };

  const handleSaveAll = async (agentType: string) => {
    if (!onSave) return;
    setSaving(agentType);
    try {
      const agent = agents.find((a) => a.agent_type === agentType);
      if (agent) {
        // Save definition (includes visibility fields)
        await onSave(agentType, agent);
        // Also save visibility via dedicated endpoint as fallback
        await updateAgentVisibility(agentType, {
          visible_skill_group_ids: agent.visible_skill_group_ids || [],
          visible_rule_group_ids: agent.visible_rule_group_ids || [],
        });
      }
    } catch (e) {
      console.error("Save failed:", e);
      setSaveError(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
      setTimeout(() => setSaveError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const toggleSkillGroup = (agentType: string, groupId: string) => {
    const agent = agents.find((a) => a.agent_type === agentType);
    if (!agent) return;
    const current = agent.visible_skill_group_ids || [];
    const updated = current.includes(groupId)
      ? current.filter((g) => g !== groupId)
      : [...current, groupId];
    updateAgent(agentType, { visible_skill_group_ids: updated.length > 0 ? updated : null });
  };

  const toggleRuleGroup = (agentType: string, groupId: string) => {
    const agent = agents.find((a) => a.agent_type === agentType);
    if (!agent) return;
    const current = agent.visible_rule_group_ids || [];
    const updated = current.includes(groupId)
      ? current.filter((g) => g !== groupId)
      : [...current, groupId];
    updateAgent(agentType, { visible_rule_group_ids: updated.length > 0 ? updated : null });
  };

  const toolColor = (t: string[] | null) => {
    if (!t) return "text-muted-foreground";
    if (t.includes("*")) return "text-green-500";
    return "text-cyan-400";
  };

  const toolLabel = (t: string[] | null) => {
    if (!t) return "仅通信";
    if (t.includes("*")) return "全部";
    return `${t.length} 个`;
  };

  const getVisibleSkillGroupNames = (agent: AgentDefinitionData) => {
    const ids = agent.visible_skill_group_ids || [];
    return skillGroups.filter((g) => ids.includes(g.id));
  };

  const getVisibleRuleGroupNames = (agent: AgentDefinitionData) => {
    const ids = agent.visible_rule_group_ids || [];
    return ruleGroups.filter((g) => ids.includes(g.id));
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-200">Agent 定义</h3>
          <Badge variant="outline" className="text-xs text-indigo-500 border-indigo-500/30">
            {agents.length} 个
          </Badge>
        </div>
        <button
          type="button"
          onClick={addAgent}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none"
        >
          <Plus size={12} aria-hidden="true" /> 新增
        </button>
      </div>

      <div className="space-y-2">
        {agents.map((agent) => {
          const isExpanded = expandedAgent === agent.agent_type;
          const isSelected = selectedAgentType === agent.agent_type;
          return (
            <div
              key={agent.agent_type}
              role="button"
              tabIndex={0}
              aria-label={`选择 ${agent.agent_type}`}
              className={`bg-slate-800/80 border border-border/30 rounded-lg px-3 py-3 transition-all cursor-pointer ${
                isSelected ? "border-indigo-500/50 bg-indigo-500/5" : "hover:border-indigo-500/30"
              }`}
              onClick={() => onSelectAgent(isSelected ? null : agent.agent_type)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelectAgent(isSelected ? null : agent.agent_type); }}}
            >
              {/* Header */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setExpandedAgent(isExpanded ? null : agent.agent_type); }}
                  aria-expanded={isExpanded}
                  aria-label={`${isExpanded ? "折叠" : "展开"} ${agent.agent_type}`}
                  className="p-0.5 text-muted-foreground hover:text-foreground cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                >
                  {isExpanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                </button>
                <Bot size={14} aria-hidden="true" className="text-indigo-500 flex-shrink-0" />
                <span className="text-xs font-medium text-slate-200 flex-1">{agent.agent_type}</span>
                <div className="flex items-center gap-1">
                  <Wrench size={12} aria-hidden="true" className={toolColor(agent.tools)} />
                  <span className={`text-xs ${toolColor(agent.tools)}`}>{toolLabel(agent.tools)}</span>
                </div>
                <Badge variant="outline" className="text-xs text-muted-foreground border-muted-foreground/30">
                  {agent.max_turns}轮
                </Badge>
                {agent.agent_type.startsWith("custom_") && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeAgent(agent.agent_type); }}
                    aria-label={`删除 ${agent.agent_type}`}
                    className="p-0.5 text-red-500/60 hover:text-red-500 cursor-pointer focus-visible:ring-2 focus-visible:ring-red-500/30 focus-visible:outline-none"
                  >
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-1 ml-7 line-clamp-1">{agent.description}</p>

              {/* Expanded Editor */}
              {isExpanded && (
                <div className="mt-3 ml-7 space-y-4" onClick={(e) => e.stopPropagation()}>
                  {/* Description */}
                  <div>
                    <label htmlFor={`desc-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">描述</label>
                    <input
                      id={`desc-${agent.agent_type}`}
                      value={agent.description}
                      onChange={(e) => updateAgent(agent.agent_type, { description: e.target.value })}
                      className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                    />
                  </div>

                  {/* Max Turns + Model */}
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <label htmlFor={`turns-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">最大轮次</label>
                      <input
                        id={`turns-${agent.agent_type}`}
                        type="number" min={1} max={50}
                        value={agent.max_turns}
                        onChange={(e) => updateAgent(agent.agent_type, { max_turns: parseInt(e.target.value) || 10 })}
                        className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                      />
                    </div>
                    <div className="flex-1">
                      <label htmlFor={`model-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">模型覆盖</label>
                      <select
                        id={`model-${agent.agent_type}`}
                        value={agent.model || ""}
                        onChange={(e) => updateAgent(agent.agent_type, { model: e.target.value || null })}
                        className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 cursor-pointer"
                      >
                        <option value="">{agent.agent_type === "main" ? "自动使用首个模型" : "继承 Main"}</option>
                        {availableModels.map((m) => (
                          <option key={`${m.provider_id}:${m.model_name}`} value={`${m.provider_id}:${m.model_name}`}>
                            {m.display_name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Copy Main Workspace */}
                  <div>
                    <label htmlFor={`workspace-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">复制 Main Workspace</label>
                    <select
                      id={`workspace-${agent.agent_type}`}
                      value={agent.copy_main_workspace === null ? "null" : agent.copy_main_workspace.toString()}
                      onChange={(e) => {
                        const val = e.target.value;
                        updateAgent(agent.agent_type, {
                          copy_main_workspace: val === "null" ? null : val === "true"
                        });
                      }}
                      className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 cursor-pointer"
                    >
                      <option value="null">继承全局配置</option>
                      <option value="true">强制复制（需要访问代码）</option>
                      <option value="false">强制不复制（空白 workspace）</option>
                    </select>
                    <p className="text-xs text-muted-foreground/70 mt-1">
                      创建子会话时是否复制 Main workspace 的文件到子会话的独立 workspace
                    </p>
                  </div>

                  {/* ===== 模型参数配置 ===== */}
                  {(() => {
                    const inheritedMainModel = agents.find((item) => item.agent_type === "main")?.model
                      || availableModels[0]?.value
                      || null;
                    const effectiveModel = agent.model || inheritedMainModel;
                    const category = getAgentModelCategory(effectiveModel);
                    const isGPT = category === "gpt";
                    const isQwen = category === "qwen";
                    const selectedProviderId = effectiveModel?.split(":", 1)[0] || "";
                    const effortOptions = (providerReasoningEfforts[selectedProviderId] || []).map((effort) => ({
                      value: effort,
                      label: effortLabels[effort] || effort,
                    }));
                    const showThinkingToggle = !isGPT; // GPT 始终推理，无需开关
                    const thinkingActive = isGPT || (agent.model_params?.thinking_enabled ?? false);

                    return (
                      <div className="border-t border-border/30 pt-3">
                        <div className="mb-2">
                          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-1">
                            <Bot size={12} className="text-indigo-500" aria-hidden="true" />
                            模型参数
                          </h4>
                        </div>
                        <p className="text-xs text-muted-foreground/70 mb-2">
                          {isGPT
                            ? "GPT 模型始终启用推理。reasoning_effort 控制推理深度。"
                            : "覆盖此 Agent 的 temperature / top_p / presence_penalty / thinking_budget / reasoning_effort。未设置的字段使用 models_config.json 中的全局默认值。"}
                        </p>

                        {/* Thinking 开关（GPT 不显示） */}
                        {showThinkingToggle && (
                          <div className="flex items-center justify-between mb-3">
                            <div>
                              <label id={`thinking-label-${agent.agent_type}`} className="text-xs text-slate-300">思考模式 (thinking)</label>
                              <p className="text-xs text-muted-foreground/60">
                                {isQwen ? "开启后使用 enable_thinking 启用思考" : "开启后 temperature / top_p 将被 API 忽略"}
                              </p>
                            </div>
                            <button
                              type="button"
                              role="switch"
                              aria-checked={agent.model_params?.thinking_enabled ?? false}
                              aria-labelledby={`thinking-label-${agent.agent_type}`}
                              onClick={() => {
                                const current = agent.model_params || {};
                                updateAgent(agent.agent_type, {
                                  model_params: { ...current, thinking_enabled: !current.thinking_enabled },
                                });
                              }}
                              className={`relative w-10 h-5 rounded-full transition-all duration-300 border cursor-pointer ${
                                (agent.model_params?.thinking_enabled ?? false)
                                  ? "bg-amber-500/30 border-amber-500/50"
                                  : "bg-slate-800 border-slate-600"
                              }`}
                            >
                              <span
                                className={`absolute top-0.5 w-4 h-4 rounded-full transition-all duration-300 ${
                                  (agent.model_params?.thinking_enabled ?? false)
                                    ? "left-5 bg-amber-500"
                                    : "left-0.5 bg-slate-500"
                                }`}
                              />
                            </button>
                          </div>
                        )}

                        {/* Reasoning Effort */}
                        {thinkingActive && effortOptions.length > 0 && (
                          <div className="mb-3">
                            <label htmlFor={`effort-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">
                              思考力度 (reasoning_effort)
                              {isGPT && <span className="text-indigo-400/70 ml-1">[GPT 始终推理]</span>}
                            </label>
                            <select
                              id={`effort-${agent.agent_type}`}
                              value={agent.model_params?.reasoning_effort || effortOptions[0]?.value || "high"}
                              onChange={(e) => {
                                const current = agent.model_params || {};
                                updateAgent(agent.agent_type, {
                                  model_params: { ...current, reasoning_effort: e.target.value },
                                });
                              }}
                              className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 cursor-pointer"
                            >
                              {effortOptions.map((opt) => (
                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                              ))}
                            </select>
                          </div>
                        )}

                        {/* Temperature */}
                        <div className="mb-3">
                          <div className="flex items-center justify-between mb-1">
                            <label className={`text-xs ${(thinkingActive && !isGPT) ? "text-muted-foreground/50" : "text-muted-foreground"}`}>
                              温度 (temperature)
                              {(thinkingActive && !isGPT) && (
                                <span className="text-amber-500/70 ml-1">[thinking 开启时忽略]</span>
                              )}
                            </label>
                            <span className="text-xs text-muted-foreground">
                              {agent.model_params?.temperature ?? "默认"}
                            </span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={2}
                            step={0.1}
                            value={agent.model_params?.temperature ?? 0.7}
                            disabled={thinkingActive && !isGPT}
                            aria-valuemin={0}
                            aria-valuemax={2}
                            aria-valuenow={agent.model_params?.temperature ?? 0.7}
                            aria-label={`温度: ${agent.model_params?.temperature ?? "默认"}`}
                            onChange={(e) => {
                              const current = agent.model_params || {};
                              updateAgent(agent.agent_type, {
                                model_params: { ...current, temperature: parseFloat(e.target.value) },
                              });
                            }}
                            className={`w-full h-1.5 rounded-full appearance-none bg-slate-800 outline-none
                              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5
                              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500 [&::-webkit-slider-thumb]:cursor-pointer
                              ${(thinkingActive && !isGPT) ? "opacity-30 cursor-not-allowed" : "cursor-pointer"}`}
                          />
                          <div className="flex justify-between mt-0.5">
                            <span className="text-xs text-muted-foreground/40">0</span>
                            <span className="text-xs text-muted-foreground/40">2</span>
                          </div>
                        </div>

                        {/* Top P */}
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <label className={`text-xs ${(thinkingActive && !isGPT) ? "text-muted-foreground/50" : "text-muted-foreground"}`}>
                              Top P
                              {(thinkingActive && !isGPT) && (
                                <span className="text-amber-500/70 ml-1">[thinking 开启时忽略]</span>
                              )}
                            </label>
                            <span className="text-xs text-muted-foreground">
                              {agent.model_params?.top_p ?? "默认"}
                            </span>
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.05}
                            value={agent.model_params?.top_p ?? 1.0}
                            disabled={thinkingActive && !isGPT}
                            aria-valuemin={0}
                            aria-valuemax={1}
                            aria-valuenow={agent.model_params?.top_p ?? 1.0}
                            aria-label={`Top P: ${agent.model_params?.top_p ?? "默认"}`}
                            onChange={(e) => {
                              const current = agent.model_params || {};
                              updateAgent(agent.agent_type, {
                                model_params: { ...current, top_p: parseFloat(e.target.value) },
                              });
                            }}
                            className={`w-full h-1.5 rounded-full appearance-none bg-slate-800 outline-none
                              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5
                              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500 [&::-webkit-slider-thumb]:cursor-pointer
                              ${(thinkingActive && !isGPT) ? "opacity-30 cursor-not-allowed" : "cursor-pointer"}`}
                          />
                          <div className="flex justify-between mt-0.5">
                            <span className="text-xs text-muted-foreground/40">0</span>
                            <span className="text-xs text-muted-foreground/40">1</span>
                          </div>
                        </div>

                        {/* Presence Penalty */}
                        <div className="mb-3 mt-3 pt-3 border-t border-border/20">
                          <div className="flex items-center justify-between mb-1">
                            <label className="text-xs text-muted-foreground">存在惩罚 (presence_penalty)</label>
                            <span className="text-xs text-muted-foreground">
                              {agent.model_params?.presence_penalty ?? "默认"}
                            </span>
                          </div>
                          <input
                            type="range"
                            min={-2}
                            max={2}
                            step={0.1}
                            value={agent.model_params?.presence_penalty ?? 0.0}
                            aria-valuemin={-2}
                            aria-valuemax={2}
                            aria-valuenow={agent.model_params?.presence_penalty ?? 0.0}
                            aria-label={`存在惩罚: ${agent.model_params?.presence_penalty ?? "默认"}`}
                            onChange={(e) => {
                              const current = agent.model_params || {};
                              updateAgent(agent.agent_type, {
                                model_params: { ...current, presence_penalty: parseFloat(e.target.value) },
                              });
                            }}
                            className="w-full h-1.5 rounded-full appearance-none bg-slate-800 outline-none cursor-pointer
                              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5
                              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500 [&::-webkit-slider-thumb]:cursor-pointer"
                          />
                          <div className="flex justify-between mt-0.5">
                            <span className="text-xs text-muted-foreground/40">-2.0</span>
                            <span className="text-xs text-muted-foreground/40">2.0</span>
                          </div>
                          <p className="text-xs text-muted-foreground/50 mt-0.5">
                            正值减少重复，负值增加重复。创意写作建议调高
                          </p>
                        </div>

                        {/* Thinking Budget（仅 Qwen thinking 开启时显示） */}
                        {isQwen && (agent.model_params?.thinking_enabled ?? false) && (
                          <div className="mb-3">
                            <label htmlFor={`budget-${agent.agent_type}`} className="text-xs text-muted-foreground mb-1 block">
                              思考 Token 上限 (thinking_budget)
                            </label>
                            <input
                              id={`budget-${agent.agent_type}`}
                              type="number"
                              min={1}
                              max={131072}
                              step={100}
                              placeholder="不限制"
                              value={agent.model_params?.thinking_budget ?? ""}
                              onChange={(e) => {
                                const current = agent.model_params || {};
                                const val = e.target.value ? parseInt(e.target.value, 10) : null;
                                updateAgent(agent.agent_type, {
                                  model_params: { ...current, thinking_budget: val },
                                });
                              }}
                              className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                            />
                            <p className="text-xs text-muted-foreground/50 mt-0.5">
                              限制思考过程的最大 Token 数（仅 Qwen 类模型生效），留空则不限制
                            </p>
                          </div>
                        )}

                        {/* Response Format (JSON Mode) */}
                        <div className="mt-3 pt-3 border-t border-border/20">
                          <div className="flex items-center justify-between">
                            <div>
                              <label className="text-xs text-slate-300">JSON 输出模式 (response_format)</label>
                              <p className="text-xs text-muted-foreground/60">
                                强制模型输出合法 JSON（开启后将禁用工具调用）
                              </p>
                            </div>
                            <button
                              type="button"
                              role="switch"
                              aria-checked={!!agent.model_params?.response_format}
                              onClick={() => {
                                const current = agent.model_params || {};
                                const nextFormat = current.response_format
                                  ? null
                                  : { type: "json_object" as const };
                                updateAgent(agent.agent_type, {
                                  model_params: { ...current, response_format: nextFormat },
                                });
                              }}
                              className={`relative w-10 h-5 rounded-full transition-all duration-300 border cursor-pointer ${
                                agent.model_params?.response_format
                                  ? "bg-emerald-500/30 border-emerald-500/50"
                                  : "bg-slate-800 border-slate-600"
                              }`}
                            >
                              <span
                                className={`absolute top-0.5 w-4 h-4 rounded-full transition-all duration-300 ${
                                  agent.model_params?.response_format
                                    ? "left-5 bg-emerald-500"
                                    : "left-0.5 bg-slate-500"
                                }`}
                              />
                            </button>
                          </div>
                          {agent.model_params?.response_format && (
                            <p className="text-xs text-amber-500/70 mt-1">
                              已开启 JSON 模式：模型将只输出合法 JSON。请确保 prompt 中明确要求模型输出 JSON 格式。
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Tools whitelist - collapsible, default collapsed */}
                  <CollapsibleSection
                    title="工具白名单"
                    subtitle={agent.tools?.includes("*") ? "全部工具" : agent.tools ? `${agent.tools.length} 个` : "空 = 仅通信"}
                    defaultExpanded={false}
                  >
                    {agent.tools?.includes("*") ? (
                      <div className="bg-green-500/5 border border-green-500/20 rounded-md p-3 text-center">
                        <p className="text-xs text-green-500 font-medium mb-1">
                          全部工具已启用（共 {allTools.length} 个）
                        </p>
                        <p className="text-xs text-muted-foreground mb-2">
                          下方黑名单可禁用部分工具
                        </p>
                        <button
                          type="button"
                          onClick={() => {
                            // Switch from "*" to selective mode: pre-select all tools, keep disallowed_tools
                            const allToolNames = allTools.map((t) => t.name);
                            updateAgent(agent.agent_type, { tools: [...allToolNames], disallowed_tools: agent.disallowed_tools });
                          }}
                          className="px-2 py-0.5 text-xs rounded bg-slate-800/60 text-muted-foreground hover:text-foreground hover:bg-slate-800 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                        >
                          切换到选择性模式
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {groupOrder.map((gid) => {
                          const groupToolList = toolsByGroup[gid];
                          const groupName = groups.find((g) => g.id === gid)?.name || toolGroupLabel[gid] || gid;
                          const colorClass = toolGroupColor[gid] || "bg-slate-800/40 text-muted-foreground border-transparent";
                          const agentTools = agent.tools || [];
                          const selectedInGroup = groupToolList.filter((t) => agentTools.includes(t.name));
                          const allInGroup = groupToolList.every((t) => agentTools.includes(t.name));
                          const someInGroup = selectedInGroup.length > 0 && !allInGroup;

                          return (
                            <div key={gid} className="bg-slate-800/30 rounded-md p-2">
                              <div className="flex items-center justify-between mb-1.5">
                                <span className={`text-xs font-medium ${colorClass.split(" ")[0] || "text-muted-foreground"}`}>
                                  {groupName}
                                  <span className="text-muted-foreground ml-1 font-normal">
                                    {selectedInGroup.length}/{groupToolList.length}
                                  </span>
                                </span>
                                <button
                                  type="button"
                                  onClick={() => toggleGroupTools(agent.agent_type, gid, "tools")}
                                  className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none ${
                                    allInGroup
                                      ? "bg-green-500/15 text-green-500"
                                      : someInGroup
                                      ? "bg-amber-500/15 text-amber-500"
                                      : "bg-slate-800/40 text-muted-foreground hover:text-foreground"
                                  }`}
                                >
                                  {allInGroup ? <CheckSquare size={12} aria-hidden="true" /> : <Square size={12} aria-hidden="true" />}
                                  {allInGroup ? "取消全选" : "全选"}
                                </button>
                              </div>
                              <div className="flex flex-wrap gap-1">
                                {groupToolList.map((t) => {
                                  const active = agentTools.includes(t.name);
                                  return (
                                    <button
                                      type="button"
                                      key={t.name}
                                      onClick={() => toggleTool(agent.agent_type, t.name, "tools")}
                                      aria-pressed={active}
                                      aria-label={`${active ? "取消" : "启用"}工具 ${t.name}`}
                                      className={`px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-cyan-500/30 focus-visible:outline-none ${
                                        active
                                          ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30"
                                          : "bg-slate-800/40 text-muted-foreground border border-transparent hover:border-border"
                                      }`}
                                      title={t.description}
                                    >
                                      {t.name}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </CollapsibleSection>

                  {/* Disallowed Tools - collapsible, default collapsed */}
                  {(agent.tools?.includes("*") || (agent.disallowed_tools && agent.disallowed_tools.length > 0)) && (
                    <CollapsibleSection
                      title="禁用工具黑名单"
                      subtitle={agent.disallowed_tools?.length ? `${agent.disallowed_tools.length} 个` : "无"}
                      defaultExpanded={false}
                      icon={<AlertCircle size={12} className="text-amber-500" aria-hidden="true" />}
                    >
                      <div className="space-y-2">
                        {groupOrder.map((gid) => {
                          const groupToolList = toolsByGroup[gid];
                          const groupName = groups.find((g) => g.id === gid)?.name || toolGroupLabel[gid] || gid;
                          const colorClass = toolGroupColor[gid] || "bg-slate-800/40 text-muted-foreground border-transparent";
                          const disallowedList = agent.disallowed_tools || [];
                          const allInGroup = groupToolList.every((t) => disallowedList.includes(t.name));
                          const someInGroup = groupToolList.filter((t) => disallowedList.includes(t.name)).length > 0 && !allInGroup;

                          return (
                            <div key={gid} className="bg-slate-800/30 rounded-md p-2">
                              <div className="flex items-center justify-between mb-1.5">
                                <span className={`text-xs font-medium ${colorClass.split(" ")[0] || "text-muted-foreground"}`}>
                                  {groupName}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => toggleGroupTools(agent.agent_type, gid, "disallowed_tools")}
                                  className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none ${
                                    allInGroup
                                      ? "bg-red-500/15 text-red-500"
                                      : someInGroup
                                      ? "bg-amber-500/15 text-amber-500"
                                      : "bg-slate-800/40 text-muted-foreground hover:text-foreground"
                                  }`}
                                >
                                  {allInGroup ? <CheckSquare size={12} aria-hidden="true" /> : <Square size={12} aria-hidden="true" />}
                                  {allInGroup ? "取消全选" : "全选"}
                                </button>
                              </div>
                              <div className="flex flex-wrap gap-1">
                                {groupToolList.map((t) => {
                                  const active = disallowedList.includes(t.name);
                                  return (
                                    <button
                                      type="button"
                                      key={t.name}
                                      onClick={() => toggleTool(agent.agent_type, t.name, "disallowed_tools")}
                                      aria-pressed={active}
                                      aria-label={`${active ? "取消禁用" : "禁用"}工具 ${t.name}`}
                                      className={`px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-red-500/30 focus-visible:outline-none ${
                                        active
                                          ? "bg-red-500/20 text-red-500 border border-red-500/30"
                                          : "bg-slate-800/40 text-muted-foreground border border-transparent hover:border-border"
                                      }`}
                                      title={t.description}
                                    >
                                      {t.name}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </CollapsibleSection>
                  )}

                  {/* ===== 可见性配置 ===== */}
                  <div className="border-t border-border/30 pt-3">
                    <div className="mb-2">
                      <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-1">
                        <Eye size={12} className="text-amber-500" aria-hidden="true" />
                        可见性
                      </h4>
                    </div>
                    <p className="text-xs text-muted-foreground/70 mb-2">
                      以组为单位配置此 Agent 可访问的 Skill 和 Rule。在 Skills/Rules 页面管理组和成员关系。
                    </p>

                    {/* Skill 组可见性 */}
                    <div className="mb-3">
                      <label className="text-xs text-muted-foreground mb-1.5 block flex items-center gap-1">
                        <Layers size={12} className="text-cyan-400" aria-hidden="true" />
                        可见的 Skill 组
                        {getVisibleSkillGroupNames(agent).length > 0 && (
                          <Badge variant="outline" className="text-xs ml-1">
                            {getVisibleSkillGroupNames(agent).length} 组
                          </Badge>
                        )}
                      </label>
                      {skillGroups.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {skillGroups.map((g) => {
                            const active = (agent.visible_skill_group_ids || []).includes(g.id);
                            return (
                              <button
                                type="button"
                                key={g.id}
                                onClick={() => toggleSkillGroup(agent.agent_type, g.id)}
                                className={`px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-cyan-500/30 focus-visible:outline-none ${
                                  active
                                    ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30"
                                    : "bg-slate-800/40 text-muted-foreground border border-transparent hover:border-border"
                                }`}
                                title={g.description || g.name}
                              >
                                {g.name}
                              </button>
                            );
                          })}
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground">暂无 Skill 组，请在 Skills 页面创建</p>
                      )}
                    </div>

                    {/* Rule 组可见性 */}
                    <div>
                      <label className="text-xs text-muted-foreground mb-1.5 block flex items-center gap-1">
                        <Layers size={12} className="text-purple-500" aria-hidden="true" />
                        可见的 Rule 组
                        {getVisibleRuleGroupNames(agent).length > 0 && (
                          <Badge variant="outline" className="text-xs ml-1">
                            {getVisibleRuleGroupNames(agent).length} 组
                          </Badge>
                        )}
                      </label>
                      {ruleGroups.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {ruleGroups.map((g) => {
                            const active = (agent.visible_rule_group_ids || []).includes(g.id);
                            return (
                              <button
                                type="button"
                                key={g.id}
                                onClick={() => toggleRuleGroup(agent.agent_type, g.id)}
                                className={`px-1.5 py-0.5 rounded text-xs transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-purple-500/30 focus-visible:outline-none ${
                                  active
                                    ? "bg-purple-500/20 text-purple-500 border border-purple-500/30"
                                    : "bg-slate-800/40 text-muted-foreground border border-transparent hover:border-border"
                                }`}
                                title={g.description || g.name}
                              >
                                {g.name}
                              </button>
                            );
                          })}
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground">暂无 Rule 组，请在 Rules 页面创建</p>
                      )}
                    </div>
                  </div>

                  {extensions.map((extension) => {
                    const ExtensionEditor = extension.agentEditor;
                    return ExtensionEditor ? (
                      <ExtensionEditor
                        key={extension.id}
                        agent={agent}
                        updateAgent={(patch) => updateAgent(agent.agent_type, patch)}
                      />
                    ) : null;
                  })}

                  {/* Prompt Template 选择器 */}
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block flex items-center gap-1">
                      <FileText size={12} className="text-indigo-500" aria-hidden="true" />
                      提示词模板 (prompt_template)
                    </label>
                    <select
                      value={agent.prompt_template || "subagent"}
                      onChange={(e) => updateAgent(agent.agent_type, { prompt_template: e.target.value })}
                      className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50 cursor-pointer"
                    >
                      {(availableTemplates.length > 0 ? availableTemplates : ["main", "subagent", "compressor"]).map((tmpl) => (
                        <option key={tmpl} value={tmpl}>{tmpl}</option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground/60 mt-0.5">
                      决定使用 prompts_config.json 中的哪个模板组装提示词
                    </p>
                  </div>

                  {/* System prompt template */}
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Prompt 模板补充</label>
                    <textarea
                      value={agent.system_prompt_template}
                      onChange={(e) => updateAgent(agent.agent_type, { system_prompt_template: e.target.value })}
                      rows={3}
                      className="w-full bg-slate-800/60 border border-border/50 rounded-md px-2 py-1.5 text-xs text-slate-300 leading-relaxed resize-y outline-none focus:border-indigo-500/50"
                      placeholder="可选的额外 prompt..."
                    />
                  </div>

                  {/* 保存按钮 */}
                  {onSave && (
                    <div className="flex flex-col items-end gap-1">
                      {saveError && (
                        <span role="alert" className="text-xs text-red-500">{saveError}</span>
                      )}
                      <button
                        type="button"
                        onClick={() => handleSaveAll(agent.agent_type)}
                        disabled={saving === agent.agent_type}
                        aria-label={`保存 ${agent.agent_type}`}
                        className="flex items-center gap-1 px-3 py-1.5 min-h-[44px] text-xs rounded-md bg-indigo-500/15 text-indigo-500 hover:bg-indigo-500/25 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                      >
                        <Save size={12} aria-hidden="true" />
                        {saving === agent.agent_type ? "保存中..." : "保存"}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
