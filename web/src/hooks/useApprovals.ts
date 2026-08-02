/**
 * useApprovals - 审批状态管理 Hook
 *
 * 职责：
 * - 维护 pendingApprovals 状态
 * - 监听 WebSocket events 通道的 approval_request / approval_resolved 事件
 * - 提供 approve() / reject() 方法（调用 REST API）
 * - 初始化时从 REST API 加载已有的待审批请求
 */
import { useState, useEffect, useCallback } from "react";
import { ApprovalRequest } from "../types";
import { fetchPendingApprovals, approveRequest, rejectRequest } from "../lib/api";
import { useWebSocket } from "./useWebSocket";

export function useApprovals() {
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>([]);
  const [resolvedApprovals, setResolvedApprovals] = useState<
    { request_id: string; result: string; resolved_at: string }[]
  >([]);

  // 加载初始待审批列表
  const loadPending = useCallback(async () => {
    try {
      const data = await fetchPendingApprovals();
      setPendingApprovals(data.approvals || []);
    } catch (e) {
      console.error("加载待审批请求失败:", e);
    }
  }, []);

  // 初始加载
  useEffect(() => {
    loadPending();
  }, [loadPending]);

  // 监听 WebSocket events 通道
  const handleEventMessage = useCallback((data: unknown) => {
    const event = data as Record<string, unknown>;
    if (!event || !event.type) return;

    if (event.type === "approval_request") {
      const request: ApprovalRequest = {
        request_id: String(event.request_id || ""),
        session_id: String(event.session_id || ""),
        tool_name: String(event.tool_name || ""),
        command: String(event.command || ""),
        workspace: String(event.workspace || ""),
        status: "pending",
        created_at: String(event.created_at || ""),
        expires_at: String(event.expires_at || ""),
      };
      setPendingApprovals((prev) => {
        // 避免重复
        if (prev.some((p) => p.request_id === request.request_id)) return prev;
        return [...prev, request];
      });
    }

    if (event.type === "approval_resolved") {
      const requestId = String(event.request_id || "");
      const result = String(event.result || "");
      const resolvedAt = String(event.resolved_at || "");

      // 从 pending 中移除
      setPendingApprovals((prev) => prev.filter((p) => p.request_id !== requestId));

      // 记录到 resolved（用于短暂的 toast 展示）
      setResolvedApprovals((prev) => [
        ...prev.slice(-9), // 只保留最近 10 条
        { request_id: requestId, result, resolved_at: resolvedAt },
      ]);
    }
  }, []);

  useWebSocket({
    url: "/ws/events",
    onMessage: handleEventMessage,
    autoConnect: true,
  });

  // 批准
  const approve = useCallback(async (requestId: string) => {
    try {
      await approveRequest(requestId);
      setPendingApprovals((prev) => prev.filter((p) => p.request_id !== requestId));
    } catch (e) {
      console.error("批准请求失败:", e);
    }
  }, []);

  // 拒绝
  const reject = useCallback(async (requestId: string, reason: string = "") => {
    try {
      await rejectRequest(requestId, reason);
      setPendingApprovals((prev) => prev.filter((p) => p.request_id !== requestId));
    } catch (e) {
      console.error("拒绝请求失败:", e);
    }
  }, []);

  // 清除已解决的通知
  const clearResolved = useCallback((requestId: string) => {
    setResolvedApprovals((prev) => prev.filter((r) => r.request_id !== requestId));
  }, []);

  return {
    pendingApprovals,
    resolvedApprovals,
    approve,
    reject,
    clearResolved,
    refresh: loadPending,
  };
}
