/** Canonical workflow task/node conversation loading helpers. */
import { useCallback, useEffect, useState } from "react";

import { useConversation } from "../features/conversation/useConversation";
import type { ConversationPhase } from "../features/conversation/conversationTypes";
import { resolveNodeSessionId } from "../features/conversation/resolveNodeSession";
import { getNodeMessages, getTask } from "../lib/api";
import type {
  Message,
  NodeMessageResponse,
  StreamingSegment,
  WorkflowNodeDef,
  WorkflowTask,
} from "../types";

export type { StreamingSegment, ToolCallState } from "../types";

export interface WorkflowTaskRestoreState {
  taskKey: string | null;
  task: WorkflowTask | null;
  nodeDefinitions: Record<string, { nodeType: string; label: string }>;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/** Load a task and its frozen node definition with stale-response protection. */
export function useWorkflowTaskRestore(
  workflowId: string | null,
  taskId: string | null,
): WorkflowTaskRestoreState {
  const [reloadNonce, setReloadNonce] = useState(0);
  const [state, setState] = useState<Omit<WorkflowTaskRestoreState, "reload">>({
    taskKey: null,
    task: null,
    nodeDefinitions: {},
    loading: false,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    if (!workflowId || !taskId) {
      setState({ taskKey: null, task: null, nodeDefinitions: {}, loading: false, error: null });
      return () => { cancelled = true; };
    }

    const taskKey = `${workflowId}:${taskId}`;
    setState({ taskKey: null, task: null, nodeDefinitions: {}, loading: true, error: null });
    void getTask(workflowId, taskId)
      .then((data) => {
        if (cancelled) return;
        setState({
          taskKey,
          task: data.task,
          nodeDefinitions: Object.fromEntries(
            (data.definition?.nodes || []).map((node: WorkflowNodeDef) => [
              node.id,
              { nodeType: node.node_type, label: node.label },
            ]),
          ),
          loading: false,
          error: null,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({
          taskKey,
          task: null,
          nodeDefinitions: {},
          loading: false,
          error: "任务详情加载失败，请重试",
        });
      });
    return () => { cancelled = true; };
  }, [reloadNonce, taskId, workflowId]);

  const reload = useCallback(() => setReloadNonce((value) => value + 1), []);
  return { ...state, reload };
}

interface NodeHistorySource {
  workflowId: string;
  taskId: string;
  nodeId: string;
}

interface UseNodeStreamingOptions {
  sessionId: string | null;
  autoConnect?: boolean;
  historySource?: NodeHistorySource | null;
}

export interface UseNodeStreamingReturn {
  sessionId: string | null;
  metadata: NodeMessageResponse | null;
  messages: Message[];
  streamingSegments: StreamingSegment[];
  phase: ConversationPhase;
  isStreaming: boolean;
  connected: boolean;
  loading: boolean;
  error: string | null;
  retry: () => boolean;
  reload: () => void;
}

export function useNodeStreaming({
  sessionId: runtimeSessionId,
  autoConnect = true,
  historySource = null,
}: UseNodeStreamingOptions): UseNodeStreamingReturn {
  const [metadataSnapshot, setMetadataSnapshot] = useState<{
    sourceKey: string;
    data: NodeMessageResponse;
    runtimeSessionIdAtRestore: string | null;
  } | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const historyWorkflowId = historySource?.workflowId || null;
  const historyTaskId = historySource?.taskId || null;
  const historyNodeId = historySource?.nodeId || null;
  const historySourceKey = autoConnect && historyWorkflowId && historyTaskId && historyNodeId
    ? `${historyWorkflowId}:${historyTaskId}:${historyNodeId}`
    : null;

  useEffect(() => {
    let cancelled = false;
    if (!autoConnect || !historyWorkflowId || !historyTaskId || !historyNodeId) {
      setMetadataSnapshot(null);
      setHistoryLoading(false);
      setHistoryError(null);
      return () => { cancelled = true; };
    }
    const activeSourceKey = `${historyWorkflowId}:${historyTaskId}:${historyNodeId}`;
    const runtimeSessionIdAtRestore = runtimeSessionId;
    setMetadataSnapshot((current) =>
      current?.sourceKey === activeSourceKey ? current : null,
    );
    setHistoryLoading(true);
    setHistoryError(null);
    void getNodeMessages(historyWorkflowId, historyTaskId, historyNodeId)
      .then((data) => {
        if (!cancelled) {
          setMetadataSnapshot({
            sourceKey: activeSourceKey,
            data,
            runtimeSessionIdAtRestore,
          });
        }
      })
      .catch(() => {
        if (!cancelled) setHistoryError("节点消息加载失败，请重试");
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => { cancelled = true; };
  }, [
    autoConnect,
    historyNodeId,
    historySourceKey,
    historyTaskId,
    historyWorkflowId,
    reloadNonce,
    runtimeSessionId,
  ]);

  const currentSnapshot = metadataSnapshot?.sourceKey === historySourceKey
    ? metadataSnapshot
    : null;
  const currentMetadata = currentSnapshot?.data || null;
  const targetSessionId = resolveNodeSessionId(
    runtimeSessionId,
    currentMetadata?.session_id || null,
    currentSnapshot?.runtimeSessionIdAtRestore || null,
  );
  const conversation = useConversation({
    sessionId: targetSessionId,
    autoConnect: autoConnect && Boolean(targetSessionId),
  });
  const { replaceMessages } = conversation;

  useEffect(() => {
    if (
      currentMetadata?.session_id &&
      currentMetadata.session_id === targetSessionId &&
      conversation.phase === "loading"
    ) {
      replaceMessages(currentMetadata.messages || []);
    }
  }, [conversation.phase, currentMetadata, replaceMessages, targetSessionId]);

  const reload = useCallback(() => setReloadNonce((value) => value + 1), []);
  const metadataMatchesTarget = Boolean(
    currentMetadata?.session_id && currentMetadata.session_id === targetSessionId,
  );
  const fallbackMessages = metadataMatchesTarget &&
    conversation.phase === "loading" &&
    conversation.messages.length === 0
    ? (currentMetadata?.messages || [])
    : conversation.messages;
  const canonicalLoading = Boolean(targetSessionId) && (
    conversation.phase === "loading" || conversation.phase === "reconnecting"
  );
  const fallbackError = !targetSessionId || canonicalLoading ? historyError : null;

  return {
    sessionId: targetSessionId,
    metadata: currentMetadata,
    messages: fallbackMessages,
    streamingSegments: conversation.streamingSegments,
    phase: conversation.phase,
    isStreaming: conversation.isStreaming,
    connected: conversation.connected,
    loading: canonicalLoading || (!targetSessionId && historyLoading),
    error: conversation.error || fallbackError,
    retry: conversation.resync,
    reload,
  };
}
