import React, { useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  GitBranch,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  PluginCatalogSource,
  PluginSourceRequest,
} from "@/extensions/plugin-types";

interface PluginRepositoryDialogProps {
  source: PluginCatalogSource | null;
  initialView?: "form" | "delete";
  busyAction: string;
  onClose: () => void;
  onSave: (source: PluginCatalogSource | null, request: PluginSourceRequest) => Promise<boolean>;
  onDelete: (source: PluginCatalogSource) => Promise<boolean>;
  onRefresh: () => Promise<boolean>;
}

function shortCommit(value: string): string {
  return value ? value.slice(0, 8) : "-";
}

export function PluginRepositoryDialog({
  source,
  initialView = "form",
  busyAction,
  onClose,
  onSave,
  onDelete,
  onRefresh,
}: PluginRepositoryDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const [name, setName] = useState(source?.name ?? "");
  const [url, setUrl] = useState(source?.url ?? "");
  const [ref, setRef] = useState(source?.ref ?? "main");
  const [view, setView] = useState(initialView);
  const busy = Boolean(busyAction);
  const adding = !source;
  const deleting = Boolean(source) && view === "delete";

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current
      ?.querySelector<HTMLElement>("input:not([disabled]), button:not([disabled])")
      ?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      const panel = panelRef.current;
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
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
  }, [busy, onClose]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (deleting && source) {
      if (await onDelete(source)) onClose();
      return;
    }
    const saved = await onSave(source, {
      name: name.trim(),
      url: url.trim(),
      ref: ref.trim() || "HEAD",
    });
    if (saved) onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/65 p-4"
      role="presentation"
      data-plugin-repository-dialog="true"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="flex max-h-[min(46rem,calc(100dvh-2rem))] w-full max-w-xl flex-col overflow-hidden rounded-lg border bg-background shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold">
              {adding ? "添加插件仓库" : "管理插件仓库"}
            </h2>
            <p id={descriptionId} className="mt-1 truncate text-sm text-muted-foreground">
              {adding ? "保存后立即拉取插件目录。" : `${source.name} · 第三方仓库`}
            </p>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} disabled={busy} aria-label="关闭弹窗">
            <X aria-hidden="true" />
          </Button>
        </header>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5 sm:px-6">
            <div className="space-y-2">
              <Label htmlFor="plugin-source-name">仓库名称</Label>
              <Input
                id="plugin-source-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="团队插件仓库"
                disabled={busy || deleting}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="plugin-source-url">Git 仓库地址</Label>
              <Input
                id="plugin-source-url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="ssh://git@example.com/team/plugins.git"
                disabled={busy || deleting}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="plugin-source-ref">分支或标签</Label>
              <Input
                id="plugin-source-ref"
                value={ref}
                onChange={(event) => setRef(event.target.value)}
                placeholder="main"
                disabled={busy || deleting}
                required
              />
            </div>

            {adding ? (
              <>
                <details className="rounded-md border p-3 text-sm">
                  <summary className="cursor-pointer font-medium">私有仓库访问方式</summary>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    使用主进程所在主机已有的 Git 或 SSH 凭据，不在这里保存访问令牌。
                  </p>
                </details>
                <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                  <ShieldAlert className="mt-0.5 shrink-0 text-amber-500" aria-hidden="true" />
                  <div>
                    <p className="text-sm font-medium">第三方仓库</p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      保存只读取目录；安装具体插件时再确认与主进程同权限运行的风险。
                    </p>
                  </div>
                </div>
              </>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/30 p-3">
                <div>
                  <p className="text-xs text-muted-foreground">目录状态</p>
                  <p className={`mt-1 text-sm font-medium ${source.error ? "text-destructive" : ""}`}>
                    {source.error || `${source.plugin_count} 个插件 · commit ${shortCommit(source.resolved_commit)}`}
                  </p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => void onRefresh()} disabled={busy || deleting}>
                  {busyAction === "catalog:refresh"
                    ? <Loader2 className="animate-spin" aria-hidden="true" />
                    : <RefreshCw aria-hidden="true" />}
                  重新拉取
                </Button>
              </div>
            )}

            {deleting ? (
              <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3" role="alert">
                <AlertTriangle className="mt-0.5 shrink-0 text-destructive" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium">删除这个仓库？</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    仅删除仓库记录，不卸载已经安装的插件。
                  </p>
                </div>
              </div>
            ) : null}
          </div>

          <footer className="flex flex-wrap items-center justify-between gap-3 border-t bg-background px-5 py-4 sm:px-6">
            {!adding && !deleting ? (
              <Button type="button" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setView("delete")} disabled={busy}>
                <Trash2 data-icon="inline-start" aria-hidden="true" />删除仓库
              </Button>
            ) : <span />}
            <div className="ml-auto flex items-center gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={busy}>取消</Button>
              <Button
                type="submit"
                variant={deleting ? "destructive" : "default"}
                disabled={busy || (!deleting && (!name.trim() || !url.trim() || !ref.trim()))}
              >
                {busy
                  ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                  : deleting
                    ? <Trash2 data-icon="inline-start" aria-hidden="true" />
                    : <GitBranch data-icon="inline-start" aria-hidden="true" />}
                {adding ? "保存并拉取" : deleting ? "确认删除仓库" : "保存修改"}
              </Button>
            </div>
          </footer>
        </form>
      </div>
    </div>
  );
}
