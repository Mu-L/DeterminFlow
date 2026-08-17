import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PendingExtensionAnnouncement } from "./extension-announcement-state";

interface ExtensionAnnouncementDialogProps {
  announcement: PendingExtensionAnnouncement;
  onClose: () => void;
}

const LEVEL_LABELS = {
  info: "通知",
  maintenance: "维护",
  warning: "提醒",
} as const;

const LEVEL_CLASSES = {
  info: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-300",
  maintenance: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  warning: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
} as const;

function formatPublishedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function ExtensionAnnouncementDialog({
  announcement,
  onClose,
}: ExtensionAnnouncementDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    confirmButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      const panel = panelRef.current;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/65 p-4"
      role="presentation"
      data-extension-announcement-dialog="true"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-background text-foreground shadow-2xl sm:max-h-[42rem]"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className={cn("rounded-full px-2 py-1 font-medium", LEVEL_CLASSES[announcement.level])}>
                {LEVEL_LABELS[announcement.level]}
              </span>
              <time dateTime={announcement.published_at}>{formatPublishedAt(announcement.published_at)}</time>
            </div>
            <h2 id={titleId} className="mt-3 text-lg font-semibold leading-7">
              {announcement.title}
            </h2>
          </div>
          <button
            type="button"
            aria-label="关闭公告"
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-6">
          <p id={descriptionId} className="whitespace-pre-wrap text-sm leading-7 text-foreground/90">
            {announcement.body}
          </p>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-4 sm:px-6">
          <span className="truncate text-xs text-muted-foreground">{announcement.extensionName}</span>
          <Button ref={confirmButtonRef} type="button" onClick={onClose}>我知道了</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
