import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Boxes,
  Loader2,
  RefreshCw,
  RotateCcw,
} from "lucide-react";

import { PluginDetails } from "@/components/extensions/PluginDetails";
import { PluginInstallForm } from "@/components/extensions/PluginInstallForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { useExtensionActivationErrors } from "@/extensions/context-value";
import { getRuntimeStatusMeta } from "@/extensions/plugin-model";
import type {
  InstallPluginRequest,
  PluginCatalogEntry,
  PluginListResponse,
  PluginRecord,
  PluginSettings,
} from "@/extensions/plugin-types";
import {
  fetchPluginCatalog,
  fetchPlugins,
  installPlugin,
  resetPluginConfig,
  rollbackPlugin,
  savePluginConfig,
  setPluginEnabled,
  uninstallPlugin,
  updatePlugin,
} from "@/lib/plugin-api";

type PluginOperation = () => Promise<unknown>;

export default function ExtensionsPage() {
  const activationErrors = useExtensionActivationErrors();
  const { toast } = useToast();
  const [data, setData] = useState<PluginListResponse>({
    plugins: [],
    restart_required: false,
    package_management_read_only: false,
  });
  const [catalog, setCatalog] = useState<PluginCatalogEntry[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [error, setError] = useState("");
  const operationInFlight = useRef(false);

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true);
    else setRefreshing(true);
    setError("");
    try {
      const next = await fetchPlugins();
      setData(next);
      setSelectedId((current) => (
        next.plugins.some((plugin) => plugin.id === current)
          ? current
          : next.plugins[0]?.id || ""
      ));
      return true;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "插件状态加载失败");
      return false;
    } finally {
      if (initial) setLoading(false);
      else setRefreshing(false);
    }
  }, []);

  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    try {
      const next = await fetchPluginCatalog();
      setCatalog(next.plugins);
      setCatalogError(
        next.sources
          .filter((source) => source.error)
          .map((source) => `${source.name}: ${source.error}`)
          .join("；"),
      );
    } catch (loadError) {
      setCatalogError(
        loadError instanceof Error ? loadError.message : "官方插件目录加载失败",
      );
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
    void loadCatalog();
  }, [load, loadCatalog]);

  const selectedPlugin = useMemo(
    () => data.plugins.find((plugin) => plugin.id === selectedId) ?? null,
    [data.plugins, selectedId],
  );
  const restartRequired = data.restart_required
    || data.plugins.some((plugin) => plugin.restart_required);

  const runOperation = useCallback(async (
    key: string,
    operation: PluginOperation,
    successTitle: string,
  ): Promise<boolean> => {
    if (operationInFlight.current) return false;
    operationInFlight.current = true;
    setBusyAction(key);
    setError("");
    try {
      await operation();
      await load(false);
      toast({
        title: successTitle,
        description: "目标状态已保存，重启 DeterminFlow 主进程后生效。",
      });
      return true;
    } catch (operationError) {
      const message = operationError instanceof Error
        ? operationError.message
        : "插件操作失败";
      setError(message);
      toast({
        title: "操作失败",
        description: message,
        variant: "destructive",
      });
      return false;
    } finally {
      operationInFlight.current = false;
      setBusyAction("");
    }
  }, [load, toast]);

  const install = (request: InstallPluginRequest) => runOperation(
    "install",
    () => installPlugin(request, adminToken),
    "插件已安装",
  );

  const setEnabled = (plugin: PluginRecord, enabled: boolean) => runOperation(
    `${plugin.id}:enabled`,
    () => setPluginEnabled(plugin.id, enabled, adminToken),
    enabled ? "插件将在重启后启用" : "插件将在重启后停用",
  );

  const update = (plugin: PluginRecord, ref: string) => runOperation(
    `${plugin.id}:update`,
    () => updatePlugin(plugin.id, ref, adminToken),
    "插件更新已准备",
  );

  const rollback = (plugin: PluginRecord) => runOperation(
    `${plugin.id}:rollback`,
    () => rollbackPlugin(plugin.id, adminToken),
    "插件回退已准备",
  );

  const uninstall = (plugin: PluginRecord) => runOperation(
    `${plugin.id}:uninstall`,
    () => uninstallPlugin(plugin.id, adminToken),
    "插件将在重启后卸载",
  );

  const saveConfig = (plugin: PluginRecord, settings: PluginSettings) => runOperation(
    `${plugin.id}:config`,
    () => savePluginConfig(plugin.id, settings, adminToken),
    "插件配置已保存",
  );

  const resetConfig = (plugin: PluginRecord) => runOperation(
    `${plugin.id}:config`,
    () => resetPluginConfig(plugin.id, adminToken),
    "插件配置已清空",
  );

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] bg-background text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-md bg-muted">
            <Boxes aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">插件</h2>
            <p className="text-xs text-muted-foreground">
              {data.plugins.filter((plugin) => plugin.active_enabled).length} 当前启用 / {data.plugins.length} 已安装
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={() => {
            void load(false);
            void loadCatalog();
          }}
          disabled={
            loading
            || refreshing
            || catalogLoading
            || Boolean(busyAction)
          }
          aria-label="刷新插件状态"
          title="刷新"
        >
          <RefreshCw
            className={refreshing || catalogLoading ? "animate-spin" : ""}
            aria-hidden="true"
          />
        </Button>
      </header>

      <main className="flex flex-col gap-5 p-4 sm:p-6">
        {restartRequired ? (
          <Card role="status" className="border-primary/40">
            <CardContent className="flex items-start gap-3 p-4">
              <RotateCcw className="mt-0.5 text-primary" aria-hidden="true" />
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium">插件变更等待重启</p>
                <p className="text-xs text-muted-foreground">
                  当前进程仍使用启动时的插件版本和启用状态。请重启 DeterminFlow 主进程应用全部变更。
                </p>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {error ? (
          <Card role="alert" className="border-destructive/40">
            <CardContent className="flex items-start gap-2 p-4 text-sm text-destructive">
              <AlertTriangle aria-hidden="true" />
              <span className="break-all">{error}</span>
            </CardContent>
          </Card>
        ) : null}

        {activationErrors.length > 0 ? (
          <Card role="alert">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <AlertTriangle aria-hidden="true" />
                兼容前端 Extension 未激活
              </CardTitle>
              <CardDescription>
                以下诊断来自旧 build-time React Extension，不影响外部插件管理。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
                {activationErrors.map((activationError, index) => (
                  <li key={`${activationError.extensionId}-${index}`}>
                    {activationError.extensionId}: {activationError.message}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : null}

        <PluginInstallForm
          busy={Boolean(busyAction)}
          readOnly={data.package_management_read_only}
          catalog={catalog}
          catalogError={catalogError}
          adminToken={adminToken}
          onAdminTokenChange={setAdminToken}
          onInstall={install}
        />

        {loading ? (
          <Card>
            <CardContent className="flex min-h-48 items-center justify-center gap-2 p-6 text-sm text-muted-foreground" role="status">
              <Loader2 className="animate-spin" aria-hidden="true" />
              正在加载插件...
            </CardContent>
          </Card>
        ) : (
          <div className="grid items-start gap-5 xl:grid-cols-[20rem_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">已安装插件</CardTitle>
                <CardDescription>选择插件查看来源、目标状态和配置。</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {data.plugins.map((plugin) => {
                  const status = getRuntimeStatusMeta(plugin.runtime_status);
                  return (
                    <Button
                      key={plugin.id}
                      type="button"
                      variant={plugin.id === selectedId ? "secondary" : "ghost"}
                      className="h-auto min-w-0 justify-start px-3 py-3 text-left"
                      onClick={() => setSelectedId(plugin.id)}
                    >
                      <span className="flex min-w-0 flex-1 flex-col items-start gap-1">
                        <span className="w-full truncate">{plugin.name}</span>
                        <span className="w-full truncate font-mono text-xs text-muted-foreground">
                          {plugin.id}
                        </span>
                      </span>
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </Button>
                  );
                })}
                {data.plugins.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    暂无已安装插件
                  </p>
                ) : null}
              </CardContent>
            </Card>

            {selectedPlugin ? (
              <PluginDetails
                key={selectedPlugin.id}
                plugin={selectedPlugin}
                busyAction={busyAction}
                packageManagementReadOnly={data.package_management_read_only}
                onSetEnabled={setEnabled}
                onUpdate={update}
                onRollback={rollback}
                onUninstall={uninstall}
                onSaveConfig={saveConfig}
                onResetConfig={resetConfig}
              />
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">插件详情</CardTitle>
                  <CardDescription>安装或选择一个插件后在此管理。</CardDescription>
                </CardHeader>
              </Card>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
