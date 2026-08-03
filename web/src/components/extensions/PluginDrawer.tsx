import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface PluginDrawerProps {
  title: string;
  description: string;
  children: ReactNode;
  onClose: () => void;
  contentClassName?: string;
}

export function PluginDrawer({
  title,
  description,
  children,
  onClose,
  contentClassName,
}: PluginDrawerProps) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled])")?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector('[data-plugin-repository-dialog="true"]')) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;

      const focusable = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
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

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="plugin-drawer-title"
        aria-describedby="plugin-drawer-description"
        className="ml-auto flex h-full w-full max-w-2xl flex-col border-l bg-background shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id="plugin-drawer-title" className="text-lg font-semibold">{title}</h2>
            <p id="plugin-drawer-description" className="mt-1 text-sm text-muted-foreground">
              {description}
            </p>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label="关闭抽屉">
            <X aria-hidden="true" />
          </Button>
        </header>
        <div className={cn("min-h-0 flex-1 overflow-y-auto p-4 sm:p-6", contentClassName)}>
          {children}
        </div>
      </aside>
    </div>
  );
}
