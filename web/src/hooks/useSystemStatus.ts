import { useState, useEffect, useCallback } from "react";
import { SystemStatus, ToolInfo, GraphStructure } from "../types";
import { fetchSystemStatus, fetchTools, fetchGraphStructure } from "../lib/api";

export function useSystemStatus() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [graphStructure, setGraphStructure] = useState<{
    main_graph: GraphStructure;
    sub_graph: GraphStructure;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchSystemStatus();
      setStatus(data);
    } catch (e) {
      console.error("加载系统状态失败:", e);
    }
  }, []);

  const loadTools = useCallback(async () => {
    try {
      const data = await fetchTools();
      setTools(data.tools);
    } catch (e) {
      console.error("加载工具列表失败:", e);
    }
  }, []);

  const loadGraphStructure = useCallback(async () => {
    try {
      const data = await fetchGraphStructure();
      setGraphStructure(data);
    } catch (e) {
      console.error("加载图结构失败:", e);
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([loadStatus(), loadTools(), loadGraphStructure()]);
    } finally {
      setLoading(false);
    }
  }, [loadStatus, loadTools, loadGraphStructure]);

  useEffect(() => {
    loadAll();
    const interval = setInterval(loadStatus, 5000);
    return () => clearInterval(interval);
  }, [loadAll, loadStatus]);

  return { status, tools, graphStructure, loading, loadAll, loadStatus };
}
