import { useState, useCallback, useRef, useEffect } from "react";
import { useWebSocket } from "./useWebSocket";
import { useUrlParam } from "./useUrlParam";
import {
  contiguousRoundtableReplay,
  getRoundtableEventRevision,
  shouldBufferRoundtableEvent,
  shouldHandleRoundtableEvent,
} from "../lib/roundtableStream";
import {
  RoundtableSummary,
  RoundtableSession,
  TranscriptEntry,
  Seat,
  WSRoundtableEvent,
  ModeratorDecision,
  StructuredConclusion,
} from "../types";
import {
  fetchRoundtables,
  fetchRoundtableDetail,
  createRoundtable,
  startRoundtable,
  stopRoundtable,
  deleteRoundtable,
  pauseRoundtable,
  resumeRoundtable,
  injectToRoundtable,
  nominateSpeaker,
  addSeatToRoundtable,
  removeSeatFromRoundtable,
} from "../lib/api";
import type { CreateRoundtableRequest } from "../types";

interface StreamingSeat {
  seatId: string;
  speakerName: string;
  content: string;
  round: number;
}

export interface RestoredRoundtableHistory {
  roundSummaries: { round: number; content: string; source: string }[];
  conclusion: { content: string; source: string } | null;
  structuredConclusion: StructuredConclusion | null;
}

/** Derive all persisted conclusion state from one authoritative REST snapshot. */
export function restoreRoundtableHistory(
  detail: Pick<RoundtableSession, "transcript" | "shared_memory">,
): RestoredRoundtableHistory {
  const roundSummaries: RestoredRoundtableHistory["roundSummaries"] = [];
  let conclusion: RestoredRoundtableHistory["conclusion"] = null;

  for (const entry of detail.transcript || []) {
    if (entry.entry_type === "summary") {
      roundSummaries.push({
        round: entry.round_number,
        content: entry.content,
        source: entry.speaker_name,
      });
    } else if (entry.entry_type === "conclusion") {
      conclusion = { content: entry.content, source: entry.speaker_name };
    }
  }

  return {
    roundSummaries,
    conclusion,
    structuredConclusion: detail.shared_memory?.structured_conclusion || null,
  };
}

/** 需要处理的圆桌事件类型集合（提取到模块级避免每次消息重建） */
const RT_EVENT_TYPES = new Set([
  "speaker_selected", "moderator_decision",
  "roundtable_summary", "roundtable_conclusion",
  "rt_seat_added", "rt_seat_removed",
  "rt_paused", "rt_resumed",
  "rt_inject_result", "rt_nominate_result",
]);

export function useRoundtable() {
  const [urlRoundtableId, setUrlRoundtableId] = useUrlParam("roundtable_id");
  // 列表状态
  const [roundtables, setRoundtables] = useState<RoundtableSummary[]>([]);

  // refreshList ref（用于 handleMessage 的 stale closure 修复）
  const refreshListRef = useRef<(() => Promise<void>) | null>(null);

  // 当前活跃的圆桌会议
  const [activeSession, setActiveSession] = useState<RoundtableSession | null>(null);
  const activeSessionRef = useRef<RoundtableSession | null>(null);
  const detailRequestRef = useRef(0);
  const selectedRoundtableIdRef = useRef<string | null>(null);
  const loadingRoundtableIdRef = useRef<string | null>(null);
  const bufferedEventsRef = useRef<WSRoundtableEvent[]>([]);
  const appliedRevisionRef = useRef(0);
  const reloadDetailRef = useRef<((sessionId: string) => Promise<void>) | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // 讨论记录（实时更新）
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);

  // 席位状态（实时更新）
  const [seats, setSeats] = useState<Seat[]>([]);
  const seatsRef = useRef<Seat[]>([]);
  seatsRef.current = seats;

  // 流式发言状态
  const [streamingSeat, setStreamingSeat] = useState<StreamingSeat | null>(null);
  const streamingRef = useRef("");

  // 当前轮次和发言者
  const [currentRound, setCurrentRound] = useState(1);
  const currentRoundRef = useRef(1);
  currentRoundRef.current = currentRound;
  const [isDiscussing, setIsDiscussing] = useState(false);

  // Phase 2: Moderator 决策状态
  const [moderatorDecision, setModeratorDecision] = useState<ModeratorDecision | null>(null);
  const [thinkingSeatId, setThinkingSeatId] = useState<string | null>(null);

  // Phase 2: 摘要和结论
  const [roundSummaries, setRoundSummaries] = useState<{ round: number; content: string; source: string }[]>([]);
  const [conclusion, setConclusion] = useState<{ content: string; source: string } | null>(null);

  // Phase 2: 当前策略
  const [strategy, setStrategy] = useState<string>("round_robin");

  // Phase 3: 结构化结论和暂停状态
  const [structuredConclusion, setStructuredConclusion] = useState<StructuredConclusion | null>(null);
  const [isPaused, setIsPaused] = useState(false);

  // 将单个已确认属于当前圆桌的事件应用到界面状态。
  const applyRoundtableEvent = useCallback((rtEvent: WSRoundtableEvent) => {
    switch (rtEvent.type) {
      case "rt_started": {
        setIsDiscussing(true);
        setSeats(rtEvent.seats);
        setTranscript([]);
        setCurrentRound(1);
        setModeratorDecision(null);
        setThinkingSeatId(null);
        setRoundSummaries([]);
        setConclusion(null);
        setStructuredConclusion(null);
        setIsPaused(false);
        if (rtEvent.strategy) {
          setStrategy(rtEvent.strategy);
        }
        refreshListRef.current?.();
        break;
      }

      case "rt_turn_start": {
        setCurrentRound(rtEvent.round);

        // Phase 2: 检测 moderator 思考状态
        if (rtEvent.is_moderator_thinking) {
          setThinkingSeatId(rtEvent.seat_id);
          setSeats((prev) =>
            prev.map((s) => ({
              ...s,
              status: s.seat_id === rtEvent.seat_id ? "thinking" as const : s.status,
            }))
          );
          break;
        }

        setThinkingSeatId(null);
        streamingRef.current = "";
        setStreamingSeat({
          seatId: rtEvent.seat_id,
          speakerName: rtEvent.speaker_name,
          content: "",
          round: rtEvent.round,
        });
        // 更新席位状态
        setSeats((prev) =>
          prev.map((s) => ({
            ...s,
            status: s.seat_id === rtEvent.seat_id ? "speaking" as const : s.status,
          }))
        );
        break;
      }

      case "rt_token": {
        streamingRef.current += rtEvent.content;
        setStreamingSeat((prev) => {
          if (prev) return { ...prev, content: streamingRef.current };
          const seat = seatsRef.current.find((item) => item.seat_id === rtEvent.seat_id);
          return {
            seatId: rtEvent.seat_id,
            speakerName: seat?.role_name || rtEvent.seat_id,
            content: streamingRef.current,
            round: currentRoundRef.current,
          };
        });
        break;
      }

      case "rt_turn_end": {
        // 添加完整发言到 transcript
        const entry: TranscriptEntry = {
          speaker_seat_id: rtEvent.seat_id,
          speaker_name: rtEvent.speaker_name,
          content: rtEvent.full_content,
          round_number: rtEvent.round,
          timestamp: new Date().toISOString(),
          entry_type: "statement",
        };
        setTranscript((prev) => [...prev, entry]);

        // 清除流式状态
        setStreamingSeat(null);
        streamingRef.current = "";

        // 更新席位状态
        setSeats((prev) =>
          prev.map((s) => ({
            ...s,
            status: s.seat_id === rtEvent.seat_id ? "done" as const : s.status,
          }))
        );
        break;
      }

      case "rt_round_end": {
        setCurrentRound(rtEvent.round);
        // 重置所有席位状态
        setSeats((prev) =>
          prev.map((s) => ({ ...s, status: "idle" as const }))
        );
        break;
      }

      case "rt_ended": {
        setIsDiscussing(false);
        setStreamingSeat(null);
        setThinkingSeatId(null);
        setIsPaused(false);
        streamingRef.current = "";
        // 更新席位状态
        setSeats((prev) =>
          prev.map((s) => ({ ...s, status: "idle" as const }))
        );
        // 更新活跃会话状态
      setActiveSession((prev) =>
        prev ? { ...prev, status: "ended" } : null
      );
      // 刷新列表
      refreshListRef.current?.();
      break;
      }

      case "rt_start_result": {
        if (!rtEvent.success) {
          setDetailError(rtEvent.message || "圆桌启动失败，请重试");
        }
        break;
      }

      // Phase 2: 新事件处理
      case "speaker_selected": {
        setModeratorDecision({
          action: "select_speaker",
          speaker_id: rtEvent.seat_id,
          reason: rtEvent.reason,
        });
        setThinkingSeatId(null);
        // 短暂显示决策后清除
        setTimeout(() => setModeratorDecision(null), 3000);
        break;
      }

      case "moderator_decision": {
        setModeratorDecision(rtEvent.decision);
        setThinkingSeatId(null);
        if (rtEvent.decision.action !== "select_speaker") {
          setTimeout(() => setModeratorDecision(null), 5000);
        }
        break;
      }

      case "roundtable_summary": {
        setRoundSummaries((prev) => [
          ...prev,
          { round: rtEvent.round, content: rtEvent.content, source: rtEvent.source },
        ]);

        // 同时追加到 transcript 作为摘要条目
        const summaryEntry: TranscriptEntry = {
          speaker_seat_id: "system",
          speaker_name: rtEvent.source,
          content: rtEvent.content,
          round_number: rtEvent.round,
          timestamp: new Date().toISOString(),
          entry_type: "summary",
        };
        setTranscript((prev) => [...prev, summaryEntry]);
        break;
      }

      case "roundtable_conclusion": {
        setConclusion({
          content: rtEvent.content,
          source: rtEvent.source,
        });

        // Phase 3: 保存结构化结论
        if (rtEvent.structured) {
          setStructuredConclusion(rtEvent.structured);
        }

        // 追加到 transcript
        const conclusionEntry: TranscriptEntry = {
          speaker_seat_id: "system",
          speaker_name: rtEvent.source,
          content: rtEvent.content,
          round_number: rtEvent.total_rounds,
          timestamp: new Date().toISOString(),
          entry_type: "conclusion",
        };
        setTranscript((prev) => [...prev, conclusionEntry]);
        break;
      }

      // Phase 3: 新事件处理
      case "rt_seat_added": {
        setSeats((prev) => [...prev, rtEvent.seat]);
        break;
      }

      case "rt_seat_removed": {
        setSeats((prev) => prev.filter((s) => s.seat_id !== rtEvent.seat_id));
        break;
      }

      case "rt_paused": {
        setIsPaused(true);
        setActiveSession((prev) =>
          prev ? { ...prev, status: "paused" } : null
        );
        break;
      }

      case "rt_resumed": {
        setIsPaused(false);
        setActiveSession((prev) =>
          prev ? { ...prev, status: "discussing" } : null
        );
        break;
      }

      case "rt_inject_result":
      case "rt_nominate_result": {
        // 可选：显示操作反馈
        break;
      }
    }
  }, []);

  // 详情快照加载期间先缓冲实时事件，快照落地后按原顺序重放，避免漏 token。
  const handleMessage = useCallback((data: unknown) => {
    const event = data as WSRoundtableEvent | { type: string };
    if (!event.type.startsWith("rt_") && !RT_EVENT_TYPES.has(event.type)) return;

    const rtEvent = event as WSRoundtableEvent;
    if (!shouldHandleRoundtableEvent(selectedRoundtableIdRef.current, rtEvent)) return;
    if (shouldBufferRoundtableEvent(loadingRoundtableIdRef.current, rtEvent)) {
      bufferedEventsRef.current.push(rtEvent);
      return;
    }
    const revision = getRoundtableEventRevision(rtEvent);
    if (revision !== null) {
      if (revision <= appliedRevisionRef.current) return;
      if (revision > appliedRevisionRef.current + 1) {
        const selectedId = selectedRoundtableIdRef.current;
        if (selectedId) {
          void reloadDetailRef.current?.(selectedId);
          bufferedEventsRef.current.push(rtEvent);
        }
        return;
      }
      appliedRevisionRef.current = revision;
    }
    applyRoundtableEvent(rtEvent);
  }, [applyRoundtableEvent]);

  // 使用圆桌作用域连接，避免接收其他 Agent 会话的 token。
  const { connected } = useWebSocket({
    url: urlRoundtableId
      ? `/ws/chat?roundtable_id=${encodeURIComponent(urlRoundtableId)}`
      : "/ws/chat?roundtable_only=1",
    onMessage: handleMessage,
  });
  const previousConnectedRef = useRef(false);

  // ============ REST API 操作 ============

  const refreshList = useCallback(async () => {
    try {
      const result = await fetchRoundtables();
      setRoundtables(result.roundtables);
    } catch {
      setRoundtables([]);
    }
  }, []);

  // 同步 ref，供 handleMessage 通过 ref 调用（避免 stale closure）
  refreshListRef.current = refreshList;

  const loadDetail = useCallback(async (sessionId: string) => {
    const requestId = ++detailRequestRef.current;
    const isSwitchingSession = selectedRoundtableIdRef.current !== sessionId;
    selectedRoundtableIdRef.current = sessionId;
    loadingRoundtableIdRef.current = sessionId;
    setDetailLoading(true);
    setDetailError(null);
    if (isSwitchingSession) {
      bufferedEventsRef.current = [];
      appliedRevisionRef.current = 0;
      activeSessionRef.current = null;
      setActiveSession(null);
      setTranscript([]);
      setSeats([]);
      setStreamingSeat(null);
      streamingRef.current = "";
    }
    setUrlRoundtableId(sessionId);
    try {
      const detail = await fetchRoundtableDetail(sessionId);
      if (requestId !== detailRequestRef.current) return;
      const restoredHistory = restoreRoundtableHistory(detail);
      activeSessionRef.current = detail;
      setActiveSession(detail);
      setTranscript(detail.transcript || []);
      setSeats(detail.seats || []);
      setCurrentRound(detail.current_round || 1);
      setIsDiscussing(detail.status === "discussing");
      setStrategy(detail.strategy || "round_robin");
      setConclusion(restoredHistory.conclusion);
      setStructuredConclusion(restoredHistory.structuredConclusion);
      setRoundSummaries(restoredHistory.roundSummaries);
      setModeratorDecision(null);
      setThinkingSeatId(null);
      setIsPaused(detail.status === "paused");
      if (detail.active_turn) {
        streamingRef.current = detail.active_turn.content;
        setStreamingSeat({
          seatId: detail.active_turn.seat_id,
          speakerName: detail.active_turn.speaker_name,
          content: detail.active_turn.content,
          round: detail.active_turn.round,
        });
      } else {
        streamingRef.current = "";
        setStreamingSeat(null);
      }

      const snapshotRevision = Number.isSafeInteger(detail.event_revision)
        ? detail.event_revision || 0
        : 0;
      appliedRevisionRef.current = snapshotRevision;
      const replay = contiguousRoundtableReplay(
        snapshotRevision,
        bufferedEventsRef.current,
      );
      if (replay.hasGap) {
        bufferedEventsRef.current = replay.pending;
        void reloadDetailRef.current?.(sessionId);
        return;
      }
      bufferedEventsRef.current = [];
      loadingRoundtableIdRef.current = null;
      replay.events.forEach((event) => {
        const revision = getRoundtableEventRevision(event);
        if (revision !== null && revision <= appliedRevisionRef.current) return;
        if (revision !== null) appliedRevisionRef.current = revision;
        applyRoundtableEvent(event);
      });
    } catch (e) {
      if (requestId === detailRequestRef.current) {
        setDetailError(
          e instanceof Error && e.message.includes("404")
            ? "未找到该圆桌会议，可能已被删除"
            : "加载圆桌详情失败，请检查连接后重试",
        );
      }
    } finally {
      if (requestId === detailRequestRef.current) {
        setDetailLoading(false);
      }
    }
  }, [applyRoundtableEvent, setUrlRoundtableId]);
  reloadDetailRef.current = loadDetail;

  const retryDetail = useCallback(() => {
    const sessionId = selectedRoundtableIdRef.current;
    if (sessionId) void loadDetail(sessionId);
  }, [loadDetail]);

  const clearActive = useCallback(() => {
    detailRequestRef.current += 1;
    selectedRoundtableIdRef.current = null;
    loadingRoundtableIdRef.current = null;
    bufferedEventsRef.current = [];
    appliedRevisionRef.current = 0;
    activeSessionRef.current = null;
    setActiveSession(null);
    setTranscript([]);
    setSeats([]);
    setStreamingSeat(null);
    streamingRef.current = "";
    setIsDiscussing(false);
    setModeratorDecision(null);
    setThinkingSeatId(null);
    setRoundSummaries([]);
    setConclusion(null);
    setStructuredConclusion(null);
    setIsPaused(false);
    setDetailLoading(false);
    setDetailError(null);
    setUrlRoundtableId(null, { replace: true });
  }, [setUrlRoundtableId]);

  const handleCreate = useCallback(async (data: CreateRoundtableRequest) => {
    const result = await createRoundtable(data);
    if (result.success) {
      await refreshList();
      await loadDetail(result.session.session_id);
    }
    return result;
  }, [refreshList, loadDetail]);

  const handleStart = useCallback(async (sessionId: string) => {
    const result = await startRoundtable(sessionId);
    if (result.success) {
      setIsDiscussing(true);
      setActiveSession((prev) =>
        prev ? { ...prev, status: "discussing" } : null
      );
    }
    return result;
  }, []);

  const handleStop = useCallback(async (sessionId: string) => {
    const result = await stopRoundtable(sessionId);
    if (result.success) {
      setIsDiscussing(false);
      setActiveSession((prev) =>
        prev ? { ...prev, status: "ended" } : null
      );
    }
    return result;
  }, []);

  const handleDelete = useCallback(async (sessionId: string) => {
    const result = await deleteRoundtable(sessionId);
    if (result.success) {
      if (activeSession?.session_id === sessionId) {
        clearActive();
      }
      await refreshList();
    }
    return result;
  }, [activeSession, clearActive, refreshList]);

  // Phase 3: 暂停/恢复
  const handlePause = useCallback(async (sessionId: string) => {
    const result = await pauseRoundtable(sessionId);
    if (result.success) {
      setIsPaused(true);
      setActiveSession((prev) =>
        prev ? { ...prev, status: "paused" } : null
      );
    }
    return result;
  }, []);

  const handleResume = useCallback(async (sessionId: string) => {
    const result = await resumeRoundtable(sessionId);
    if (result.success) {
      setIsPaused(false);
      setActiveSession((prev) =>
        prev ? { ...prev, status: "discussing" } : null
      );
    }
    return result;
  }, []);

  // Phase 3: 用户插话
  const handleInject = useCallback(async (sessionId: string, content: string) => {
    return await injectToRoundtable(sessionId, content);
  }, []);

  // Phase 3: 点名发言
  const handleNominate = useCallback(async (sessionId: string, targetSeatId?: string, targetName?: string, content?: string) => {
    return await nominateSpeaker(sessionId, targetSeatId, targetName, content);
  }, []);

  // Phase 3: 动态添加席位
  const handleAddSeat = useCallback(async (sessionId: string, seatConfig: {
    role_name: string;
    system_prompt?: string;
    temperature?: number;
    is_moderator?: boolean;
  }) => {
    const result = await addSeatToRoundtable(sessionId, seatConfig);
    // seat 添加通过 WS 事件同步
    return result;
  }, []);

  // Phase 3: 动态移除席位
  const handleRemoveSeat = useCallback(async (sessionId: string, seatId: string) => {
    const result = await removeSeatFromRoundtable(sessionId, seatId);
    // seat 移除通过 WS 事件同步
    return result;
  }, []);

  // 初始加载
  useEffect(() => {
    refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (urlRoundtableId && selectedRoundtableIdRef.current !== urlRoundtableId) {
      void loadDetail(urlRoundtableId);
    } else if (!urlRoundtableId && selectedRoundtableIdRef.current) {
      clearActive();
    }
  }, [clearActive, urlRoundtableId, loadDetail]);

  // 首次连接和每次重连都重新取得带 watermark 的 REST 快照，补齐断线窗口。
  useEffect(() => {
    const wasConnected = previousConnectedRef.current;
    previousConnectedRef.current = connected;
    if (connected && !wasConnected && selectedRoundtableIdRef.current) {
      void loadDetail(selectedRoundtableIdRef.current);
    }
  }, [connected, loadDetail]);

  return {
    // 列表
    roundtables,
    refreshList,

    // 活跃会话
    activeSession,
    loadDetail,
    clearActive,
    detailLoading,
    detailError,
    retryDetail,

    // 实时状态
    transcript,
    seats,
    streamingSeat,
    currentRound,
    isDiscussing,

    // Phase 2: 新增状态
    moderatorDecision,
    thinkingSeatId,
    roundSummaries,
    conclusion,
    strategy,

    // Phase 3: 新增状态
    structuredConclusion,
    isPaused,

    // 操作
    handleCreate,
    handleStart,
    handleStop,
    handleDelete,

    // Phase 3: 新增操作
    handlePause,
    handleResume,
    handleInject,
    handleNominate,
    handleAddSeat,
    handleRemoveSeat,

    // 连接状态
    connected,
  };
}
