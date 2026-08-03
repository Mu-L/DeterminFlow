import { useCallback, useEffect, useRef, type RefObject } from "react";

export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export function distanceFromBottom(metrics: ScrollMetrics): number {
  return Math.max(0, metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight);
}

export function isNearBottom(metrics: ScrollMetrics, threshold = 160): boolean {
  return distanceFromBottom(metrics) <= threshold;
}

export interface UseAutoFollowOutputOptions {
  threshold?: number;
  behavior?: ScrollBehavior;
}

export interface UseAutoFollowOutputReturn {
  scrollToBottom: (force?: boolean) => void;
  resetAutoFollow: () => void;
  isFollowingOutput: () => boolean;
}

export function useAutoFollowOutput<T extends HTMLElement>(
  viewportRef: RefObject<T | null>,
  { threshold = 160, behavior = "auto" }: UseAutoFollowOutputOptions = {},
): UseAutoFollowOutputReturn {
  const shouldFollowRef = useRef(true);
  const animationFrameRef = useRef<number | null>(null);

  const cancelScheduledScroll = useCallback(() => {
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  }, []);

  const scrollToBottom = useCallback((force = false) => {
    const viewport = viewportRef.current;
    if (!viewport || (!force && !shouldFollowRef.current)) return;
    if (animationFrameRef.current !== null) return;

    animationFrameRef.current = requestAnimationFrame(() => {
      animationFrameRef.current = null;
      const currentViewport = viewportRef.current;
      if (!currentViewport) return;
      currentViewport.scrollTo({ top: currentViewport.scrollHeight, behavior });
      if (force) shouldFollowRef.current = true;
    });
  }, [behavior, viewportRef]);

  const resetAutoFollow = useCallback(() => {
    shouldFollowRef.current = true;
    scrollToBottom(true);
  }, [scrollToBottom]);

  const isFollowingOutput = useCallback(() => shouldFollowRef.current, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const handleScroll = () => {
      shouldFollowRef.current = isNearBottom(viewport, threshold);
    };
    handleScroll();
    viewport.addEventListener("scroll", handleScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", handleScroll);
  }, [threshold, viewportRef]);

  useEffect(() => cancelScheduledScroll, [cancelScheduledScroll]);

  return { scrollToBottom, resetAutoFollow, isFollowingOutput };
}
