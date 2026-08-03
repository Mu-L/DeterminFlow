import { useState, useEffect, useRef, useCallback } from "react";

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: unknown) => void;
  onReconnect?: () => void;
  autoConnect?: boolean;
  reconnectInterval?: number;
}

export function useWebSocket({
  url,
  onMessage,
  onReconnect,
  autoConnect = true,
  reconnectInterval = 3000,
}: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pongTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onReconnectRef = useRef(onReconnect);
  onReconnectRef.current = onReconnect;
  const connectionGenerationRef = useRef(0);
  const hasConnectedRef = useRef(false);
  // 标记是否主动断开（主动断开时不自动重连）
  const intentionalCloseRef = useRef(false);
  // 上次收到消息（任意帧）的时间戳，用于僵死检测
  const lastActivityRef = useRef<number>(Date.now());

  /** 清理所有定时器 */
  const clearAllTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
    if (pongTimeoutRef.current) {
      clearTimeout(pongTimeoutRef.current);
      pongTimeoutRef.current = null;
    }
  }, []);

  /** 启动心跳：每 15s 发 ping，5s 内无 pong 则判定连接僵死；45s 无活跃则判定僵死 */
  const startHeartbeat = useCallback(() => {
    clearAllTimers();

    pingTimerRef.current = setInterval(() => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      // 整体活跃度检测（45s 内无任何消息则判定僵死）
      const elapsed = Date.now() - lastActivityRef.current;
      if (elapsed > 45000) {
        ws.close();
        return;
      }

      // 发送 ping
      try {
        ws.send(JSON.stringify({ type: "ping" }));
      } catch {
        ws.close();
        return;
      }

      // 设置 pong 超时
      pongTimeoutRef.current = setTimeout(() => {
        ws.close();
      }, 5000);
    }, 15000);
  }, [clearAllTimers]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    intentionalCloseRef.current = false;
    lastActivityRef.current = Date.now();
    const generation = ++connectionGenerationRef.current;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const fullUrl = `${protocol}//${host}${url}`;

    const ws = new WebSocket(fullUrl);

    ws.onopen = () => {
      if (generation !== connectionGenerationRef.current || wsRef.current !== ws) {
        ws.close();
        return;
      }
      const reconnected = hasConnectedRef.current;
      hasConnectedRef.current = true;
      setConnected(true);
      lastActivityRef.current = Date.now();
      startHeartbeat();
      if (reconnected) onReconnectRef.current?.();
    };

    ws.onmessage = (event) => {
      if (generation !== connectionGenerationRef.current || wsRef.current !== ws) return;
      lastActivityRef.current = Date.now();
      try {
        const data = JSON.parse(event.data);
        // 收到 pong 时取消超时
        if (data.type === "pong" && pongTimeoutRef.current) {
          clearTimeout(pongTimeoutRef.current);
          pongTimeoutRef.current = null;
          return;
        }
        onMessageRef.current?.(data);
      } catch {
        // Ignore malformed frames; a later authoritative snapshot can recover state.
      }
    };

    ws.onclose = () => {
      if (generation !== connectionGenerationRef.current || wsRef.current !== ws) return;
      setConnected(false);
      clearAllTimers();
      wsRef.current = null;
      // 只在非主动断开时自动重连
      if (!intentionalCloseRef.current && reconnectInterval > 0) {
        reconnectTimerRef.current = setTimeout(connect, reconnectInterval);
      }
    };

    ws.onerror = () => {
      // 错误后主动关闭，确保 onclose 被触发从而启动重连
      // 避免 WebSocket 进入僵死状态（onerror 但不触发 onclose）
      ws.close();
    };

    wsRef.current = ws;
  }, [url, reconnectInterval, startHeartbeat, clearAllTimers]);

  const send = useCallback((data: unknown): boolean => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify(data));
    return true;
  }, []);

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    connectionGenerationRef.current += 1;
    clearAllTimers();
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
    setConnected(false);
  }, [clearAllTimers]);

  useEffect(() => {
    if (autoConnect) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [autoConnect, connect, disconnect]);

  return { connected, send, disconnect, connect };
}
