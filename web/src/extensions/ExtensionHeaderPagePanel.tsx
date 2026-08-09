import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";

interface ExtensionHeaderPagePanelProps {
  extensionId: string;
  title: string;
  onClose: () => void;
}

export function ExtensionHeaderPagePanel({
  extensionId,
  title,
  onClose,
}: ExtensionHeaderPagePanelProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const pageUrl = `/api/plugins/${encodeURIComponent(extensionId)}/ui/`;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose]);

  return createPortal(
    <aside
      data-extension-header-page
      role="dialog"
      aria-modal="false"
      aria-labelledby="extension-header-page-title"
      className="fixed bottom-3 right-3 top-16 z-[80] flex w-[calc(100vw-1.5rem)] max-w-[42rem] flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-950 shadow-2xl shadow-black/50 lg:max-w-[50vw]"
    >
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-800 px-4">
        <h2 id="extension-header-page-title" className="truncate text-sm font-semibold text-slate-100">
          {title}
        </h2>
        <Button
          ref={closeRef}
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={`关闭${title}`}
          className="h-8 w-8 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </header>
      <iframe
        key={pageUrl}
        src={pageUrl}
        title={title}
        referrerPolicy="no-referrer"
        className="min-h-0 flex-1 border-0 bg-slate-900"
      />
    </aside>,
    document.body,
  );
}
