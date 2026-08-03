import {
  Info,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Undo2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getPluginEnableAction,
  getPluginCatalogUpdate,
  getPluginTargetState,
  getRuntimeStatusMeta,
} from "@/extensions/plugin-model";
import type { PluginCatalogEntry, PluginRecord } from "@/extensions/plugin-types";

interface PluginLifecycleListProps {
  plugins: PluginRecord[];
  catalog: PluginCatalogEntry[];
  busyAction: string;
  onDetails: (plugin: PluginRecord) => void;
  onSetEnabled: (plugin: PluginRecord, enabled: boolean) => Promise<boolean>;
  onUpdate: (plugin: PluginRecord, ref: string) => Promise<boolean>;
}

function versionLabel(plugin: PluginRecord): string {
  return plugin.active_version || plugin.desired_version || "未加载";
}

export function PluginLifecycleList({
  plugins,
  catalog,
  busyAction,
  onDetails,
  onSetEnabled,
  onUpdate,
}: PluginLifecycleListProps) {
  const busy = Boolean(busyAction);

  return (
    <Card>
      <CardHeader className="gap-1 border-b pb-4">
        <CardTitle className="text-base">已安装插件</CardTitle>
        <CardDescription>当前运行事实与重启后的目标状态并排显示。</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {plugins.length > 0 ? (
          <>
            <div className="hidden grid-cols-[minmax(15rem,1.6fr)_minmax(9rem,0.8fr)_minmax(9rem,0.8fr)_auto] gap-4 border-b px-5 py-3 text-xs font-medium text-muted-foreground lg:grid">
              <span>插件</span>
              <span>当前运行</span>
              <span>重启后</span>
              <span className="text-right">操作</span>
            </div>
            <div className="divide-y">
              {plugins.map((plugin) => {
                const runtime = getRuntimeStatusMeta(plugin.runtime_status);
                const action = getPluginEnableAction(plugin);
                const catalogUpdate = getPluginCatalogUpdate(plugin, catalog);
                const target = getPluginTargetState(plugin);
                const actionBusy = busyAction === `${plugin.id}:enabled`;
                const ActionIcon = action.kind === "undo"
                  ? Undo2
                  : action.targetEnabled
                    ? Play
                    : Pause;

                return (
                  <article
                    key={plugin.id}
                    className={`grid gap-4 px-5 py-4 lg:grid-cols-[minmax(15rem,1.6fr)_minmax(9rem,0.8fr)_minmax(9rem,0.8fr)_auto] lg:items-center ${
                      plugin.restart_required ? "bg-amber-500/5" : ""
                    }`}
                  >
                    <div className="flex min-w-0 items-start gap-3">
                      <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted text-sm font-semibold">
                        {plugin.name.trim().slice(0, 1) || "P"}
                      </div>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate text-sm font-medium">{plugin.name}</h3>
                          <Badge variant={plugin.source.trust === "official" ? "secondary" : "destructive"}>
                            {plugin.source.trust === "official"
                              ? <ShieldCheck aria-hidden="true" />
                              : <ShieldAlert aria-hidden="true" />}
                            {plugin.source.trust === "official" ? "官方" : "第三方"}
                          </Badge>
                        </div>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                          {plugin.id} · {versionLabel(plugin)}
                        </p>
                        {catalogUpdate?.available ? (
                          <p className="mt-1 text-xs text-primary">
                            {catalogUpdate.entry.version !== versionLabel(plugin)
                              ? `可更新至 ${catalogUpdate.entry.version}`
                              : "仓库内容有更新"}
                          </p>
                        ) : null}
                        {plugin.error ? (
                          <p className="mt-1 line-clamp-1 text-xs text-destructive" title={plugin.error}>
                            {plugin.error}
                          </p>
                        ) : null}
                      </div>
                    </div>

                    <div className="flex items-center justify-between gap-3 lg:block">
                      <span className="text-xs text-muted-foreground lg:hidden">当前运行</span>
                      <Badge variant={runtime.variant}>{runtime.label}</Badge>
                    </div>

                    <div className="flex items-center justify-between gap-3 lg:block">
                      <span className="text-xs text-muted-foreground lg:hidden">重启后</span>
                      <div className="text-right lg:text-left">
                        <p className="text-sm font-medium">{target.label}</p>
                        {target.pending ? (
                          <p className="mt-0.5 text-xs text-amber-500">{target.description}</p>
                        ) : null}
                      </div>
                    </div>

                    <div className="flex flex-wrap justify-end gap-2">
                      <Button type="button" variant="ghost" size="sm" onClick={() => onDetails(plugin)}>
                        <Info data-icon="inline-start" aria-hidden="true" />
                        详情
                      </Button>
                      <Button
                        type="button"
                        variant={action.targetEnabled && action.kind !== "undo" ? "default" : "outline"}
                        size="sm"
                        onClick={() => void onSetEnabled(plugin, action.targetEnabled)}
                        disabled={busy || action.disabled}
                        title={action.disabledReason}
                      >
                        {actionBusy
                          ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                          : <ActionIcon data-icon="inline-start" aria-hidden="true" />}
                        {action.label}
                      </Button>
                      {catalogUpdate?.available ? (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => void onUpdate(
                            plugin,
                            catalogUpdate.entry.resolved_commit,
                          )}
                          disabled={busy}
                        >
                          {busyAction === `${plugin.id}:update`
                            ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                            : <RefreshCw data-icon="inline-start" aria-hidden="true" />}
                          更新
                        </Button>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 p-6 text-center">
            <p className="text-sm font-medium">尚未安装插件</p>
            <p className="text-xs text-muted-foreground">点击右上角“添加插件”开始。</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
