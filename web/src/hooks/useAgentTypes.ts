import { useState, useEffect, useCallback } from "react";
import { useWebSocket } from "./useWebSocket";

interface AgentTypeOption {
  agent_type: string;
  description: string;
  available_for_sub_session?: boolean;
  template_variables?: {
    key: string;
    name: string;
    description: string;
    default: string;
    required: boolean;
  }[];
}

interface UseAgentTypesOptions {
  /** API 端点，默认 "/api/workflows/agent-types/list" */
  endpoint?: string;
  /** 是否过滤 available_for_sub_session=false 的类型 */
  filterSubSessionOnly?: boolean;
}

export function useAgentTypes(options: UseAgentTypesOptions = {}) {
  const { endpoint = "/api/workflows/agent-types/list", filterSubSessionOnly = false } = options;
  const [agentTypes, setAgentTypes] = useState<AgentTypeOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAgentTypes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(endpoint);
      if (!res.ok) throw new Error("Failed to load agent types");
      const data = await res.json();
      const types = Array.isArray(data) ? data : data.agent_types || [];
      setAgentTypes(filterSubSessionOnly ? types.filter((t: AgentTypeOption) => t.available_for_sub_session !== false) : types);
    } catch (e) {
      const message = e instanceof Error ? e.message : "加载 Agent 类型失败";
      setError(message);
      console.error("加载 Agent 类型失败:", e);
    } finally {
      setLoading(false);
    }
  }, [endpoint, filterSubSessionOnly]);

  // 初始加载
  useEffect(() => {
    fetchAgentTypes();
  }, [fetchAgentTypes]);

  // 监听 WebSocket 事件
  const handleEvent = useCallback((data: unknown) => {
    const event = data as { type: string; subtype?: string };
    if (event.type === "system" && event.subtype === "agent_config_reloaded") {
      fetchAgentTypes();
    }
  }, [fetchAgentTypes]);

  useWebSocket({
    url: "/ws/events",
    onMessage: handleEvent,
    autoConnect: true,
    reconnectInterval: 5000,
  });

  return {
    agentTypes,
    loading,
    error,
    refresh: fetchAgentTypes,
  };
}
