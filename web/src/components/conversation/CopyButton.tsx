import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

interface CopyButtonProps {
  value: string;
  label: string;
  className?: string;
}

async function copyToClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("clipboard unavailable");
}

export default function CopyButton({ value, label, className = "" }: CopyButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
  }, []);

  const handleCopy = async () => {
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
    try {
      await copyToClipboard(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    resetTimerRef.current = setTimeout(() => setState("idle"), 1600);
  };

  const accessibleLabel = state === "copied"
    ? `${label}已复制`
    : state === "failed"
      ? `${label}复制失败`
      : `复制${label}`;

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className={`inline-flex min-h-8 min-w-8 items-center justify-center rounded text-slate-500 transition-colors hover:bg-slate-700/60 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 ${className}`}
    >
      {state === "copied" ? (
        <Check size={14} className="text-green-400" aria-hidden="true" />
      ) : (
        <Copy size={14} aria-hidden="true" />
      )}
      <span className="sr-only" aria-live="polite">{accessibleLabel}</span>
    </button>
  );
}
