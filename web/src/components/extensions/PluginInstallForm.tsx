import React, { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Loader2,
  PackagePlus,
  Plus,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  InstallPluginRequest,
  PluginCatalogEntry,
  PluginCatalogSource,
} from "@/extensions/plugin-types";

interface PluginInstallFormProps {
  busy: boolean;
  readOnly: boolean;
  catalog: PluginCatalogEntry[];
  sources: PluginCatalogSource[];
  catalogError: string;
  installedIds: ReadonlySet<string>;
  initialSourceId?: string;
  onAddSource: () => void;
  onManageSource: (source: PluginCatalogSource) => void;
  onDeleteSource: (source: PluginCatalogSource) => void;
  onInstall: (request: InstallPluginRequest) => Promise<boolean>;
  onInstalled?: () => void;
}

// eslint-disable-next-line react-refresh/only-export-components -- pure request builder is covered by node:test
export function buildCatalogInstallRequest(
  selected: PluginCatalogEntry,
  resourcePrefix: string,
  acknowledgeRisk: boolean,
): InstallPluginRequest {
  return {
    plugin_id: selected.id,
    source: selected.source,
    ref: selected.resolved_commit,
    subdirectory: selected.subdirectory,
    resource_prefix: resourcePrefix.trim() || undefined,
    acknowledge_risk: acknowledgeRisk,
  };
}

export function PluginInstallForm({
  busy,
  readOnly,
  catalog,
  sources,
  catalogError,
  installedIds,
  initialSourceId = "",
  onAddSource,
  onManageSource,
  onDeleteSource,
  onInstall,
  onInstalled,
}: PluginInstallFormProps) {
  const hasInitialSource = sources.some((source) => source.id === initialSourceId);
  const defaultSourceId = (hasInitialSource ? initialSourceId : "")
    || sources.find((source) => source.builtin && !source.error)?.id
    || sources.find((source) => !source.error)?.id
    || "";
  const [sourceId, setSourceId] = useState(defaultSourceId);
  const [selectedKey, setSelectedKey] = useState("");
  const [resourcePrefix, setResourcePrefix] = useState("");
  const [acknowledgeRisk, setAcknowledgeRisk] = useState(false);

  useEffect(() => {
    if (initialSourceId) setSourceId(initialSourceId);
  }, [initialSourceId]);

  useEffect(() => {
    if (sources.some((source) => source.id === sourceId)) return;
    setSourceId(defaultSourceId);
    setSelectedKey("");
    setAcknowledgeRisk(false);
  }, [defaultSourceId, sourceId, sources]);

  const selectedSource = sources.find((source) => source.id === sourceId) ?? null;
  const visiblePlugins = useMemo(
    () => catalog.filter((entry) => entry.source_id === sourceId),
    [catalog, sourceId],
  );
  const selected = catalog.find((entry) => (
    `${entry.source_id}:${entry.id}` === selectedKey
  )) ?? null;
  const thirdParty = selectedSource?.kind === "custom";

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || (thirdParty && !acknowledgeRisk)) return;
    const installed = await onInstall(buildCatalogInstallRequest(
      selected,
      resourcePrefix,
      acknowledgeRisk,
    ));
    if (installed) onInstalled?.();
  };

  if (readOnly) {
    return (
      <Card className="border-0 shadow-none">
        <CardHeader>
          <CardTitle className="text-base">Plugin 包由 Release 管理</CardTitle>
          <CardDescription>
            当前部署不能直接安装插件；请构建并激活包含目标插件的新 Release。
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <form className="flex h-full min-h-0 flex-col" onSubmit={submit}>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
        <ol className="grid grid-cols-3 gap-2 border-b pb-4" aria-label="添加插件步骤">
          {["选择仓库", "选择插件", "确认安装"].map((label, index) => (
            <li key={label} className="flex min-w-0 items-center gap-2 text-xs font-medium">
              <span className={`flex size-6 shrink-0 items-center justify-center rounded-full ${
                index === 0 || selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
              }`}>{index + 1}</span>
              <span className="truncate">{label}</span>
            </li>
          ))}
        </ol>

        <section aria-labelledby="plugin-source-heading">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 id="plugin-source-heading" className="text-sm font-semibold">插件仓库</h3>
            <Button type="button" variant="ghost" size="sm" onClick={onAddSource} disabled={busy}>
              <Plus data-icon="inline-start" aria-hidden="true" />添加仓库
            </Button>
          </div>
          <div className="grid gap-2">
            {sources.map((source) => (
              <div
                key={source.id}
                className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-md border p-2 ${
                  sourceId === source.id ? "border-primary bg-primary/5" : "bg-card"
                }`}
              >
                <button
                  type="button"
                  className="min-w-0 rounded-sm px-1 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => {
                    setSourceId(source.id);
                    setSelectedKey("");
                    setAcknowledgeRisk(false);
                  }}
                  disabled={busy || Boolean(source.error)}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {source.builtin
                      ? <ShieldCheck className="size-4 shrink-0" aria-hidden="true" />
                      : <ShieldAlert className="size-4 shrink-0" aria-hidden="true" />}
                    <span className="truncate">{source.name}</span>
                  </span>
                  <span className={`mt-1 block truncate text-xs ${source.error ? "text-destructive" : "text-muted-foreground"}`}>
                    {source.error || `${source.plugin_count} 个插件`}
                  </span>
                </button>
                <div className="flex items-center gap-1">
                  <Badge variant={source.builtin ? "secondary" : "outline"}>
                    {source.builtin ? "官方" : "第三方"}
                  </Badge>
                  {!source.builtin ? (
                    <>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => onManageSource(source)}
                        disabled={busy}
                      >
                        <Wrench aria-hidden="true" />管理
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        onClick={() => onDeleteSource(source)}
                        disabled={busy}
                      >
                        <Trash2 aria-hidden="true" />删除
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="flex min-w-0 flex-col gap-4" aria-label="可安装插件">
        {catalogError ? <p className="text-xs text-destructive">{catalogError}</p> : null}
        {!selectedSource ? (
          <div className="flex min-h-44 items-center justify-center rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
            先添加一个可用的插件仓库。
          </div>
        ) : visiblePlugins.length === 0 ? (
          <div className="flex min-h-44 items-center justify-center rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
            当前仓库没有可安装的插件。
          </div>
        ) : (
          <div className="grid gap-2">
            {visiblePlugins.map((entry) => {
              const key = `${entry.source_id}:${entry.id}`;
              const installed = installedIds.has(entry.id);
              return (
                <button
                  key={key}
                  type="button"
                  className={`grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-start gap-3 rounded-md border p-3 text-left ${
                    selectedKey === key ? "border-primary bg-primary/5" : "hover:bg-muted/40"
                  }`}
                  onClick={() => !installed && setSelectedKey(key)}
                  disabled={busy || installed}
                >
                  <span className="flex size-10 items-center justify-center rounded-md border bg-muted text-sm font-semibold">
                    {entry.name.trim().slice(0, 1) || "P"}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{entry.name}</span>
                    <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
                      {entry.id} · {entry.version}
                    </span>
                    {entry.description ? (
                      <span className="mt-2 line-clamp-2 block text-xs text-muted-foreground">{entry.description}</span>
                    ) : null}
                  </span>
                  <Badge variant={installed ? "secondary" : selectedKey === key ? "default" : "outline"}>
                    {installed ? "已安装" : selectedKey === key ? "已选择" : "可安装"}
                  </Badge>
                </button>
              );
            })}
          </div>
        )}

        {selected ? (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-sm">安装 {selected.name}</CardTitle>
                  <CardDescription className="mt-1">
                    锁定 {selected.resolved_commit.slice(0, 8)}，重启后启用。
                  </CardDescription>
                </div>
                <Badge variant={thirdParty ? "destructive" : "secondary"}>
                  {thirdParty ? "第三方" : "内置官方"}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <details className="rounded-md border p-3 text-sm">
                <summary className="cursor-pointer font-medium">高级选项</summary>
                <div className="mt-3 flex flex-col gap-2">
                  <Label htmlFor="plugin-resource-prefix">资源前缀覆盖</Label>
                  <Input
                    id="plugin-resource-prefix"
                    value={resourcePrefix}
                    onChange={(event) => setResourcePrefix(event.target.value)}
                    placeholder="通常留空"
                    pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                    disabled={busy}
                  />
                </div>
              </details>
              {thirdParty ? (
                <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
                  <ShieldAlert className="mt-0.5 shrink-0 text-destructive" aria-hidden="true" />
                  <div className="flex flex-col gap-2">
                    <p className="text-sm font-medium">第三方代码与主进程同权限运行</p>
                    <div className="flex items-start gap-2">
                      <Checkbox
                        id="acknowledge-third-party-risk"
                        checked={acknowledgeRisk}
                        onCheckedChange={(checked) => setAcknowledgeRisk(checked)}
                        disabled={busy}
                      />
                      <Label htmlFor="acknowledge-third-party-risk" className="text-xs font-normal leading-5">
                        我已确认仓库来源可信，并理解插件可以访问本机资源。
                      </Label>
                    </div>
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
        </section>
      </div>

      <footer className="flex items-center justify-between gap-4 border-t bg-background px-4 py-3 sm:px-6">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          {selected ? `已选择：${selected.name}` : "请选择一个插件"}
        </p>
        <Button type="submit" disabled={busy || !selected || (thirdParty && !acknowledgeRisk)}>
          {busy
            ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
            : <PackagePlus data-icon="inline-start" aria-hidden="true" />}
          安装所选插件
        </Button>
      </footer>
    </form>
  );
}
