import { useCallback } from "react";
import { useWebSocket } from "./useWebSocket";
import { useToast } from "@/components/ui/use-toast";

export function useGlobalEvents() {
  const { toast } = useToast();

  const handleEvent = useCallback((data: unknown) => {
    const event = data as { type: string; subtype?: string; success?: boolean; message?: string };

    if (event.type === "system" && event.subtype === "agent_config_reloaded") {
      toast({
        title: "Agent 配置已更新",
        description: event.message || "Agent 配置已重新加载",
        variant: event.success ? "success" : "warning",
        duration: 5000,
      });
    }
  }, [toast]);

  const { connected } = useWebSocket({
    url: "/ws/events",
    onMessage: handleEvent,
    autoConnect: true,
    reconnectInterval: 5000,
  });

  return { connected };
}
