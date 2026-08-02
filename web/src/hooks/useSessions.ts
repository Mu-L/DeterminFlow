import { useState, useEffect, useCallback } from "react";
import { Session, SessionTree, SessionDetail, WSEventData, StatusUpdateData } from "../types";
import { fetchSessions, fetchSessionTree, fetchSessionDetail } from "../lib/api";
import { useWebSocket } from "./useWebSocket";

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [tree, setTree] = useState<SessionTree | null>(null);
  const [activeSubCount, setActiveSubCount] = useState(0);
  const [mainSessionId, setMainSessionId] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<SessionDetail | null>(null);

  const loadSessions = useCallback(async () => {
    try {
      const data = await fetchSessions();
      setSessions(data.sessions);
      setActiveSubCount(data.active_sub_count);
      setMainSessionId(data.main_session_id);
    } catch (e) {
      console.error("加载会话列表失败:", e);
    }
  }, []);

  const loadTree = useCallback(async () => {
    try {
      const data = await fetchSessionTree();
      setTree(data);
    } catch (e) {
      console.error("加载会话树失败:", e);
    }
  }, []);

  const handleEvent = useCallback((data: unknown) => {
    const event = data as WSEventData;
    if (event.type === "status_update") {
      const statusData = event.data as StatusUpdateData & { sessions?: Session[] };
      if (Array.isArray(statusData.sessions)) {
        setSessions(statusData.sessions);
      }
      setActiveSubCount(statusData.active_sub_count);
      setMainSessionId(statusData.main_session_id);
    } else if (event.type === "session_update") {
      loadSessions();
      loadTree();
    }
  }, [loadSessions, loadTree]);

  const { connected } = useWebSocket({
    url: "/ws/events",
    onMessage: handleEvent,
  });

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    try {
      const data = await fetchSessionDetail(sessionId);
      setSelectedSession(data);
      return data;
    } catch (e) {
      console.error("加载会话详情失败:", e);
      return null;
    }
  }, []);

  useEffect(() => {
    loadSessions();
    loadTree();
  }, [loadSessions, loadTree]);

  return {
    sessions,
    tree,
    activeSubCount,
    mainSessionId,
    selectedSession,
    connected,
    loadSessions,
    loadTree,
    loadSessionDetail,
    setSelectedSession,
  };
}
