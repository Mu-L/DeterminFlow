import { useState, useEffect, useCallback } from "react";
import {
  PromptSectionData,
  AgentDefinitionData,
  ToolInfo,
  ToolGroup,
  OrchestrationSubTab,
  SkillGroup,
  RuleGroup,
  TemplateVariable,
} from "../types";
import {
  fetchPromptTemplates,
  fetchPromptSectionsWithContent,
  fetchAgentDefinitions,
  fetchTools,
  fetchSkillGroups,
  fetchRuleGroups,
  updateSections,
  updateAgentDefinition,
  fetchUserInjectionSections,
  updateUserInjectionSections,
  fetchDefaultModelParams,
  fetchTemplateVariables,
  updateTemplateVariables,
  deletePromptTemplate,
} from "../lib/api";

/** API 返回的 section 原始结构 */
interface RawSection {
  name: string;
  content?: string;
  token_estimate?: number;
  cache_break?: boolean;
  cache_break_reason?: string;
  enabled?: boolean;
  workflow_only?: boolean;
  order?: number;
}

function mapSections(sections: RawSection[]): PromptSectionData[] {
  return sections.map((s, i) => ({
    name: s.name,
    content: s.content || "",
    token_estimate: s.token_estimate || 0,
    cache_break: s.cache_break || false,
    cache_break_reason: s.cache_break_reason || "",
    enabled: s.enabled !== undefined ? s.enabled : true,
    workflow_only: s.workflow_only || false,
    order: s.order !== undefined ? s.order : i,
  }));
}

export function useOrchestration() {
  const [availableTemplates, setAvailableTemplates] = useState<string[]>([]);
  const [sectionsMap, setSectionsMap] = useState<Record<string, PromptSectionData[]>>({});
  const [templateVariablesMap, setTemplateVariablesMap] = useState<Record<string, TemplateVariable[]>>({});
  const [userInjectionSections, setUserInjectionSections] = useState<PromptSectionData[]>([]);
  const [promptTarget, setPromptTarget] = useState<string>("main");
  const [agents, setAgents] = useState<AgentDefinitionData[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolGroups, setToolGroups] = useState<ToolGroup[]>([]);
  const [skillGroups, setSkillGroups] = useState<SkillGroup[]>([]);
  const [ruleGroups, setRuleGroups] = useState<RuleGroup[]>([]);
  const [activeTab, setActiveTab] = useState<OrchestrationSubTab>("prompts");
  const [selectedAgentType, setSelectedAgentType] = useState<string | null>(null);
  const [defaultModelParams, setDefaultModelParams] = useState<{ thinking_enabled: boolean; reasoning_effort: string; temperature: number; top_p: number; presence_penalty: number; thinking_budget: number | null; response_format: { type: "text" | "json_object" } | null } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      // 1. 先获取可用模板列表
      const templatesRes = await fetchPromptTemplates();
      const templateNames: string[] = templatesRes.templates || [];
      setAvailableTemplates(templateNames);

      // 2. 并行获取所有模板的 sections 和 template_variables
      const sectionsPromises = templateNames.map((t) => fetchPromptSectionsWithContent(t));
      const templateVarsPromises = templateNames.map((t) => fetchTemplateVariables(t).catch(() => ({ template_variables: [] })));
      const [allSections, allTemplateVars, userInjectionRes, agentsRes, toolsRes, skillGroupsRes, ruleGroupsRes, defaultParamsRes] = await Promise.all([
        Promise.all(sectionsPromises),
        Promise.all(templateVarsPromises),
        fetchUserInjectionSections(),
        fetchAgentDefinitions(),
        fetchTools(),
        fetchSkillGroups(),
        fetchRuleGroups(),
        fetchDefaultModelParams(),
      ]);
      const defaultParams = defaultParamsRes.default_params;
      setDefaultModelParams(defaultParams);

      // 3. 构建 sections map 和 template variables map
      const sectionsMapResult: Record<string, PromptSectionData[]> = {};
      const templateVarsMapResult: Record<string, TemplateVariable[]> = {};
      templateNames.forEach((t, i) => {
        sectionsMapResult[t] = mapSections(allSections[i]?.sections || []);
        templateVarsMapResult[t] = allTemplateVars[i]?.template_variables || [];
      });
      setSectionsMap(sectionsMapResult);
      setTemplateVariablesMap(templateVarsMapResult);

      // 如果当前 target 不在可用模板中，切到第一个
      if (!templateNames.includes(promptTarget) && templateNames.length > 0) {
        setPromptTarget(templateNames[0]);
      }

      setUserInjectionSections(mapSections(userInjectionRes.sections || []));

      const mappedAgents: AgentDefinitionData[] = agentsRes.agent_types.map(
        (a) => {
          // 合并 model_params: agent 配置 > models_config default_params
          const mergedModelParams: AgentDefinitionData["model_params"] = {
            thinking_enabled: a.model_params?.thinking_enabled ?? defaultParams.thinking_enabled,
            reasoning_effort: a.model_params?.reasoning_effort ?? defaultParams.reasoning_effort,
            temperature: a.model_params?.temperature ?? defaultParams.temperature,
            top_p: a.model_params?.top_p ?? defaultParams.top_p,
            presence_penalty: a.model_params?.presence_penalty ?? defaultParams.presence_penalty,
            thinking_budget: a.model_params?.thinking_budget ?? defaultParams.thinking_budget,
            response_format: (a.model_params?.response_format ?? defaultParams.response_format ?? null) as { type: "text" | "json_object" } | null,
          };
          return {
            agent_type: a.agent_type,
            description: a.description || "",
            prompt_template: a.prompt_template || "subagent",
            tools: a.tools || null,
            disallowed_tools: a.disallowed_tools || null,
            model: a.model || null,
            max_turns: a.max_turns || 10,
            system_prompt_template: a.system_prompt_template || "",
            copy_main_workspace: a.copy_main_workspace !== undefined ? a.copy_main_workspace : null,
            visible_skill_group_ids: a.visible_skill_group_ids || null,
            visible_rule_group_ids: a.visible_rule_group_ids || null,
            extension_options: a.extension_options || null,
            model_params: mergedModelParams,
          };
        }
      );
      setAgents(mappedAgents);

      setTools(toolsRes.tools);
      setToolGroups(toolsRes.groups || []);
      setSkillGroups(skillGroupsRes.groups || []);
      setRuleGroups(ruleGroupsRes.groups || []);
    } catch (e) {
      console.error("Failed to load orchestration data:", e);
    } finally {
      setLoading(false);
    }
  }, [promptTarget]);

  useEffect(() => {
    loadData();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 当前选中的模板的 sections
  const currentSections = sectionsMap[promptTarget] || [];

  const setCurrentSections = useCallback((sections: PromptSectionData[]) => {
    setSectionsMap((prev) => ({ ...prev, [promptTarget]: sections }));
  }, [promptTarget]);

  const saveSections = useCallback(async (sectionsToSave: PromptSectionData[], target: string = "main") => {
    try {
      await updateSections(sectionsToSave, target);
      await loadData();
      return { success: true };
    } catch (e) {
      console.error("Failed to save sections:", e);
      return { success: false, error: e };
    }
  }, [loadData]);

  const saveAgent = useCallback(async (agentType: string, updates: Partial<AgentDefinitionData>) => {
    try {
      await updateAgentDefinition(agentType, updates);
      await loadData();
      return { success: true };
    } catch (e) {
      console.error("Failed to save agent:", e);
      return { success: false, error: e };
    }
  }, [loadData]);

  const saveUserInjectionSections = useCallback(async (sectionsToSave: PromptSectionData[]) => {
    try {
      await updateUserInjectionSections(sectionsToSave);
      await loadData();
      return { success: true };
    } catch (e) {
      console.error("Failed to save user injection sections:", e);
      return { success: false, error: e };
    }
  }, [loadData]);

  const saveTemplateVariables = useCallback(async (varsToSave: TemplateVariable[], target: string = "main") => {
    try {
      await updateTemplateVariables(varsToSave, target);
      await loadData();
      return { success: true };
    } catch (e) {
      console.error("Failed to save template variables:", e);
      return { success: false, error: e };
    }
  }, [loadData]);

  const deleteTemplate = useCallback(async (templateName: string) => {
    try {
      await deletePromptTemplate(templateName);
      await loadData();
      return { success: true };
    } catch (e) {
      console.error("Failed to delete template:", e);
      return { success: false, error: e };
    }
  }, [loadData]);

  // 当前选中的模板的 template variables
  const currentTemplateVariables = templateVariablesMap[promptTarget] || [];

  const setCurrentTemplateVariables = useCallback((vars: TemplateVariable[]) => {
    setTemplateVariablesMap((prev) => ({ ...prev, [promptTarget]: vars }));
  }, [promptTarget]);

  return {
    availableTemplates,
    sections: currentSections,
    setSections: setCurrentSections,
    templateVariables: currentTemplateVariables,
    setTemplateVariables: setCurrentTemplateVariables,
    userInjectionSections,
    setUserInjectionSections,
    promptTarget,
    setPromptTarget,
    agents,
    setAgents,
    tools,
    toolGroups,
    skillGroups,
    ruleGroups,
    activeTab,
    setActiveTab,
    selectedAgentType,
    setSelectedAgentType,
    loading,
    reload: loadData,
    saveSections,
    saveAgent,
    saveUserInjectionSections,
    saveTemplateVariables,
    deleteTemplate,
    defaultModelParams,
  };
}
