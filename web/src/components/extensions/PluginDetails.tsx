import { useState } from "react";
import {
  GitCommitHorizontal,
  Loader2,
  PackageMinus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  ShieldAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getRuntimeStatusMeta } from "@/extensions/plugin-model";
import type { PluginRecord, PluginSettings } from "@/extensions/plugin-types";

import { PluginConfigForm } from "./PluginConfigForm";
import { PluginStaticPage } from "./PluginStaticPage";

interface PluginDetailsProps {
  plugin: PluginRecord;
  busyAction: string;
  packageManagementReadOnly: boolean;
  onUpdate: (plugin: PluginRecord, ref: string) => Promise<boolean>;
  onRollback: (plugin: PluginRecord) => Promise<boolean>;
  onUninstall: (plugin: PluginRecord) => Promise<boolean>;
  onSaveConfig: (plugin: PluginRecord, settings: PluginSettings) => Promise<boolean>;
  onResetConfig: (plugin: PluginRecord) => Promise<boolean>;
}

function shortIdentity(value: string): string {
  return value.length > 16 ? `${value.slice(0, 12)}…` : value || "-";
}

export function PluginDetails({
  plugin,
  busyAction,
  packageManagementReadOnly,
  onUpdate,
  onRollback,
  onUninstall,
  onSaveConfig,
  onResetConfig,
}: PluginDetailsProps) {
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [updateRef, setUpdateRef] = useState("");
  const runtime = getRuntimeStatusMeta(plugin.runtime_status);
  const busy = Boolean(busyAction);
  const hasConfig = plugin.settings_schema !== null;
  const hasPage = Boolean(plugin.page_url);
  const contentTab = hasConfig ? "config" : "page";
  const packageManaged = plugin.source.url !== "bundled";

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="truncate text-lg">{plugin.name}</CardTitle>
              <CardDescription className="mt-1">
                <span className="font-mono">{plugin.id}</span>
                {plugin.description ? ` · ${plugin.description}` : ""}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={plugin.source.trust === "official" ? "secondary" : "destructive"}>
                {plugin.source.trust === "official"
                  ? <ShieldCheck aria-hidden="true" />
                  : <ShieldAlert aria-hidden="true" />}
                {plugin.source.trust === "official" ? "官方可信" : "第三方"}
              </Badge>
              <Badge variant={runtime.variant}>{runtime.label}</Badge>
              {plugin.restart_required ? <Badge variant="outline">等待重启</Badge> : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {plugin.error ? (
            <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
              {plugin.error}
            </div>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">当前版本</p>
              <p className="mt-1 font-mono text-sm">{plugin.active_version || "-"}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">重启后版本</p>
              <p className="mt-1 font-mono text-sm">{plugin.desired_version || "-"}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">当前状态</p>
              <p className="mt-1 text-sm">{plugin.active_enabled ? "已启用" : "未启用"}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">重启后状态</p>
              <p className="mt-1 text-sm">{plugin.desired_enabled ? "启用" : "停用"}</p>
            </div>
          </div>

          <dl className="grid gap-3 text-sm lg:grid-cols-2">
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">来源</dt>
              <dd className="mt-1 break-all font-mono text-xs">{plugin.source.url || "-"}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">Ref / 子目录</dt>
              <dd className="mt-1 break-all font-mono text-xs">
                {plugin.source.ref || "-"} / {plugin.source.subdirectory || "-"}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">资源前缀</dt>
              <dd className="mt-1 font-mono text-xs">
                {plugin.resource_prefix || "未命名（兼容模式）"}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="flex items-center gap-1 text-xs text-muted-foreground">
                <GitCommitHorizontal aria-hidden="true" />
                Commit
              </dt>
              <dd className="mt-1 font-mono text-xs" title={plugin.source.resolved_commit}>
                {shortIdentity(plugin.source.resolved_commit)}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">内容摘要</dt>
              <dd className="mt-1 font-mono text-xs" title={plugin.source.content_sha256}>
                {shortIdentity(plugin.source.content_sha256)}
              </dd>
            </div>
          </dl>

          {plugin.source.trust === "third_party" ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-muted-foreground">
              第三方插件与 DeterminFlow 同机执行，能够使用主进程权限访问本机资源。
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {plugin.capabilities.map((capability) => (
              <Badge key={capability} variant="secondary">{capability}</Badge>
            ))}
            {plugin.dependencies.map((dependency) => (
              <Badge key={dependency} variant="outline">依赖 {dependency}</Badge>
            ))}
          </div>

          {plugin.processes.length > 0 ? (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">托管进程</h3>
              {plugin.processes.map((process, index) => {
                const status = getRuntimeStatusMeta(process.status || "");
                return (
                  <div key={process.process_id || process.id || process.name || index} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3">
                    <div>
                      <p className="text-sm">{process.name || process.process_id || process.id || `进程 ${index + 1}`}</p>
                      {process.pid ? <p className="mt-1 font-mono text-xs text-muted-foreground">PID {process.pid}</p> : null}
                      {process.error ? <p className="mt-1 text-xs text-destructive">{process.error}</p> : null}
                    </div>
                    <Badge variant={status.variant}>{status.label}</Badge>
                  </div>
                );
              })}
            </div>
          ) : null}

          {plugin.pending_action ? (
            <p className="text-xs text-muted-foreground">
              待执行操作：<span className="font-mono">{plugin.pending_action}</span>
            </p>
          ) : null}

        </CardContent>
        {packageManaged ? (
          <CardFooter className="flex-wrap justify-end gap-2">
            {packageManagementReadOnly ? (
              <p className="mr-auto w-full text-xs text-muted-foreground">
                此部署的 Plugin 包由不可变 Release 管理；版本操作需发布新 Release。
              </p>
            ) : null}
            <div className="mr-auto flex min-w-52 flex-col gap-1">
              <Label htmlFor={`plugin-update-ref-${plugin.id}`}>更新 ref</Label>
              <Input
                id={`plugin-update-ref-${plugin.id}`}
                value={updateRef}
                onChange={(event) => setUpdateRef(event.target.value)}
                placeholder={`留空沿用 ${plugin.source.ref || "当前 ref"}`}
                disabled={busy || packageManagementReadOnly}
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onUpdate(plugin, updateRef.trim())}
              disabled={busy || packageManagementReadOnly}
            >
              {busyAction === `${plugin.id}:update`
                ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                : <RefreshCw data-icon="inline-start" aria-hidden="true" />}
              检查并更新
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onRollback(plugin)}
              disabled={busy || packageManagementReadOnly}
            >
              {busyAction === `${plugin.id}:rollback`
                ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                : <RotateCcw data-icon="inline-start" aria-hidden="true" />}
              回退版本
            </Button>
            <Button
              variant={confirmUninstall ? "destructive" : "outline"}
              size="sm"
              onClick={() => {
                if (!confirmUninstall) {
                  setConfirmUninstall(true);
                  return;
                }
                setConfirmUninstall(false);
                void onUninstall(plugin);
              }}
              disabled={busy || packageManagementReadOnly}
            >
              {busyAction === `${plugin.id}:uninstall`
                ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
                : <PackageMinus data-icon="inline-start" aria-hidden="true" />}
              {confirmUninstall ? "确认重启后卸载" : "卸载"}
            </Button>
          </CardFooter>
        ) : null}
      </Card>

      {hasConfig && hasPage ? (
        <Tabs key={plugin.id} defaultValue={contentTab}>
          <TabsList>
            <TabsTrigger value="config">配置</TabsTrigger>
            <TabsTrigger value="page">插件页面</TabsTrigger>
          </TabsList>
          <TabsContent value="config">
            <PluginConfigForm
              pluginId={plugin.id}
              schemaValue={plugin.settings_schema}
              settings={plugin.settings}
              configPresent={plugin.config_present}
              busy={busy}
              onSave={(settings) => onSaveConfig(plugin, settings)}
              onReset={() => onResetConfig(plugin)}
            />
          </TabsContent>
          <TabsContent value="page">
            <PluginStaticPage pluginName={plugin.name} pageUrl={plugin.page_url!} />
          </TabsContent>
        </Tabs>
      ) : hasConfig ? (
        <PluginConfigForm
          pluginId={plugin.id}
          schemaValue={plugin.settings_schema}
          settings={plugin.settings}
          configPresent={plugin.config_present}
          busy={busy}
          onSave={(settings) => onSaveConfig(plugin, settings)}
          onReset={() => onResetConfig(plugin)}
        />
      ) : hasPage ? (
        <PluginStaticPage pluginName={plugin.name} pageUrl={plugin.page_url!} />
      ) : null}
    </div>
  );
}
