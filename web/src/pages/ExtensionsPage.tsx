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
  KeyRound,
  Loader2,
  PackagePlus,
  Plus,
  RefreshCw,
  RotateCcw,
} from "lucide-react";

import { PluginAdminTokenForm } from "@/components/extensions/PluginAdminTokenForm";
import { PluginDetails } from "@/components/extensions/PluginDetails";
import { PluginDrawer } from "@/components/extensions/PluginDrawer";
import { PluginInstallForm } from "@/components/extensions/PluginInstallForm";
import { PluginLifecycleList } from "@/components/extensions/PluginLifecycleList";
import { PluginRepositoryDialog } from "@/components/extensions/PluginRepositoryDialog";
import { PluginRepositoryList } from "@/components/extensions/PluginRepositoryList";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { useExtensionActivationErrors } from "@/extensions/context-value";
import type {
  InstallPluginRequest,
  PluginCatalogResponse,
  PluginCatalogSource,
  PluginListResponse,
  PluginRecord,
  PluginSettings,
  PluginSourceMutationResponse,
  PluginSourceRequest,
} from "@/extensions/plugin-types";
import {
  createPluginSource,
  deletePluginSource,
  fetchPluginCatalog,
  fetchPlugins,
  installPlugin,
  resetPluginConfig,
  rollbackPlugin,
  savePluginConfig,
  setPluginEnabled,
  uninstallPlugin,
  updatePlugin,
  updatePluginSource,
} from "@/lib/plugin-api";

type PluginOperation = () => Promise<unknown>;
type PageTab = "installed" | "repositories";
type Drawer = "install" | "details" | "admin" | null;
interface RepositoryDialogState {
  source: PluginCatalogSource | null;
  view: "form" | "delete";
}

const EMPTY_CATALOG: PluginCatalogResponse = { sources: [], plugins: [] };

export default function ExtensionsPage() {
  const activationErrors = useExtensionActivationErrors();
  const { toast } = useToast();
  const [data, setData] = useState<PluginListResponse>({
    plugins: [],
    restart_required: false,
    package_management_read_only: false,
  });
  const [catalog, setCatalog] = useState<PluginCatalogResponse>(EMPTY_CATALOG);
  const [tab, setTab] = useState<PageTab>("installed");
  const [selectedId, setSelectedId] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [repositoryDialog, setRepositoryDialog] = useState<RepositoryDialogState | null>(null);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
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

  const loadCatalog = useCallback(async (refresh = false) => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const next = await fetchPluginCatalog(refresh);
      setCatalog(next);
      const sourceErrors = next.sources
        .filter((source) => source.error)
        .map((source) => `${source.name}: ${source.error}`)
        .join("；");
      setCatalogError(sourceErrors);
      return true;
    } catch (loadError) {
      setCatalogError(loadError instanceof Error ? loadError.message : "插件仓库加载失败");
      return false;
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
  const installedIds = useMemo(
    () => new Set(data.plugins.map((plugin) => plugin.id)),
    [data.plugins],
  );
  const restartRequired = data.restart_required
    || data.plugins.some((plugin) => plugin.restart_required);
  const pendingCount = data.plugins.filter((plugin) => plugin.restart_required).length;

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
      const message = operationError instanceof Error ? operationError.message : "插件操作失败";
      setError(message);
      toast({ title: "操作失败", description: message, variant: "destructive" });
      return false;
    } finally {
      operationInFlight.current = false;
      setBusyAction("");
    }
  }, [load, toast]);

  const runSourceOperation = useCallback(async (
    key: string,
    operation: PluginOperation,
    successTitle: string,
    onSuccess?: (result: PluginSourceMutationResponse) => void,
  ): Promise<boolean> => {
    if (operationInFlight.current) return false;
    operationInFlight.current = true;
    setBusyAction(key);
    setCatalogError("");
    try {
      const result = await operation() as PluginSourceMutationResponse;
      if (result.catalog) {
        setCatalog(result.catalog);
        setCatalogError(
          result.catalog.sources
            .filter((source) => source.error)
            .map((source) => `${source.name}: ${source.error}`)
            .join("；"),
        );
      } else {
        await loadCatalog(true);
      }
      onSuccess?.(result);
      toast({ title: successTitle, description: "仓库元数据已持久化保存。" });
      return true;
    } catch (operationError) {
      const message = operationError instanceof Error ? operationError.message : "插件仓库操作失败";
      setCatalogError(message);
      toast({ title: "操作失败", description: message, variant: "destructive" });
      return false;
    } finally {
      operationInFlight.current = false;
      setBusyAction("");
    }
  }, [loadCatalog, toast]);

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

  const saveSource = (source: PluginCatalogSource | null, request: PluginSourceRequest) => runSourceOperation(
    source ? `${source.id}:source` : "source:create",
    () => source
      ? updatePluginSource(source.id, request, adminToken)
      : createPluginSource(request, adminToken),
    source ? "插件仓库已更新" : "插件仓库已添加",
    source ? undefined : (result) => setSelectedSourceId(result.source.id),
  );

  const deleteSource = (source: PluginCatalogSource) => runSourceOperation(
    `${source.id}:delete`,
    () => deletePluginSource(source.id, adminToken),
    "插件仓库已删除",
    () => setSelectedSourceId((current) => current === source.id ? "" : current),
  );

  const refreshCatalog = async (): Promise<boolean> => {
    if (operationInFlight.current) return false;
    operationInFlight.current = true;
    setBusyAction("catalog:refresh");
    const ok = await loadCatalog(true);
    operationInFlight.current = false;
    setBusyAction("");
    if (ok) toast({ title: "插件目录已刷新" });
    return ok;
  };

  const openCatalog = (sourceId = "") => {
    setSelectedSourceId(sourceId);
    setDrawer("install");
  };

  return (
    <div className="min-h-[calc(100dvh-3.5rem)] bg-background text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-md bg-muted"><Boxes aria-hidden="true" /></div>
          <div>
            <h2 className="text-lg font-semibold">插件</h2>
            <p className="text-xs text-muted-foreground">
              {data.plugins.length} 个已安装 · {catalog.sources.length} 个仓库
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setDrawer("admin")}
            aria-label="远程管理授权"
            title="远程管理授权"
          >
            <KeyRound aria-hidden="true" />
          </Button>
          {tab === "installed" ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  void load(false);
                  void refreshCatalog();
                }}
                disabled={loading || refreshing || catalogLoading || Boolean(busyAction)}
              >
                <RefreshCw className={refreshing || catalogLoading ? "animate-spin" : ""} data-icon="inline-start" aria-hidden="true" />
                检查更新
              </Button>
              <Button type="button" onClick={() => openCatalog()} disabled={data.package_management_read_only}>
                <PackagePlus data-icon="inline-start" aria-hidden="true" />添加插件
              </Button>
            </>
          ) : (
            <Button
              type="button"
              onClick={() => setRepositoryDialog({ source: null, view: "form" })}
              disabled={data.package_management_read_only}
            >
              <Plus data-icon="inline-start" aria-hidden="true" />添加仓库
            </Button>
          )}
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1400px] flex-col gap-5 p-4 sm:p-6">
        <div className="flex gap-1 border-b" role="tablist" aria-label="插件管理范围">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "installed"}
            className={`border-b-2 px-4 py-2 text-sm font-medium ${tab === "installed" ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}
            onClick={() => setTab("installed")}
          >
            已安装 {data.plugins.length}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "repositories"}
            className={`border-b-2 px-4 py-2 text-sm font-medium ${tab === "repositories" ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}
            onClick={() => setTab("repositories")}
          >
            插件仓库 {catalog.sources.length}
          </button>
        </div>

        {tab === "repositories" ? (
          <section className="grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-3" aria-label="添加插件流程">
            {[
              ["1", "保存仓库", "官方仓库已内置"],
              ["2", "刷新目录", "读取仓库插件索引"],
              ["3", "选择插件", "检查后安装"],
            ].map(([number, title, description]) => (
              <div key={number} className="flex items-center gap-3 bg-card px-4 py-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold text-muted-foreground">{number}</span>
                <div><p className="text-sm font-medium">{title}</p><p className="text-xs text-muted-foreground">{description}</p></div>
              </div>
            ))}
          </section>
        ) : null}

        {restartRequired ? (
          <Card role="status" className="border-amber-500/40 bg-amber-500/5">
            <CardContent className="flex items-start gap-3 p-4">
              <RotateCcw className="mt-0.5 text-amber-500" aria-hidden="true" />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p className="text-sm font-medium">{pendingCount > 0 ? `${pendingCount} 项插件变更等待重启` : "插件变更等待重启"}</p>
                <p className="text-xs text-muted-foreground">当前进程继续使用原状态；重启 DeterminFlow 主进程后统一生效。</p>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {error || (tab === "repositories" && catalogError) ? (
          <Card role="alert" className="border-destructive/40">
            <CardContent className="flex items-start gap-2 p-4 text-sm text-destructive">
              <AlertTriangle aria-hidden="true" /><span className="break-all">{error || catalogError}</span>
            </CardContent>
          </Card>
        ) : null}

        {activationErrors.length > 0 ? (
          <Card role="alert">
            <CardHeader><CardTitle className="flex items-center gap-2 text-sm"><AlertTriangle aria-hidden="true" />兼容前端 Extension 未激活</CardTitle><CardDescription>以下诊断来自旧 build-time React Extension，不影响外部插件管理。</CardDescription></CardHeader>
            <CardContent><ul className="flex flex-col gap-1 text-xs text-muted-foreground">{activationErrors.map((item, index) => <li key={`${item.extensionId}-${index}`}>{item.extensionId}: {item.message}</li>)}</ul></CardContent>
          </Card>
        ) : null}

        {loading ? (
          <Card><CardContent className="flex min-h-48 items-center justify-center gap-2 p-6 text-sm text-muted-foreground" role="status"><Loader2 className="animate-spin" aria-hidden="true" />正在加载插件...</CardContent></Card>
        ) : tab === "installed" ? (
          <PluginLifecycleList
            plugins={data.plugins}
            catalog={catalog.plugins}
            busyAction={busyAction}
            onDetails={(plugin) => {
              setSelectedId(plugin.id);
              setDrawer("details");
            }}
            onSetEnabled={setEnabled}
            onUpdate={update}
          />
        ) : (
          <PluginRepositoryList
            sources={catalog.sources}
            busyAction={busyAction}
            readOnly={data.package_management_read_only}
            onBrowse={openCatalog}
            onEdit={(source) => setRepositoryDialog({ source, view: "form" })}
            onDeleteRequest={(source) => setRepositoryDialog({ source, view: "delete" })}
            onRefresh={() => void refreshCatalog()}
          />
        )}
      </main>

      {drawer === "install" ? (
        <PluginDrawer
          title="添加插件"
          description="从已保存的仓库中选择一个插件。"
          contentClassName="overflow-hidden p-0 sm:p-0"
          onClose={() => setDrawer(null)}
        >
          <PluginInstallForm
            busy={Boolean(busyAction)}
            readOnly={data.package_management_read_only}
            catalog={catalog.plugins}
            sources={catalog.sources}
            catalogError={catalogError}
            installedIds={installedIds}
            initialSourceId={selectedSourceId}
            onAddSource={() => setRepositoryDialog({ source: null, view: "form" })}
            onManageSource={(source) => setRepositoryDialog({ source, view: "form" })}
            onDeleteSource={(source) => setRepositoryDialog({ source, view: "delete" })}
            onInstall={install}
            onInstalled={() => setDrawer(null)}
          />
        </PluginDrawer>
      ) : null}

      {drawer === "admin" ? (
        <PluginDrawer title="远程管理授权" description="仅在服务端要求远程写操作授权时填写。" onClose={() => setDrawer(null)}>
          <PluginAdminTokenForm value={adminToken} onChange={setAdminToken} />
        </PluginDrawer>
      ) : null}

      {drawer === "details" && selectedPlugin ? (
        <PluginDrawer title={selectedPlugin.name} description="来源、版本、配置与低频包管理操作。" onClose={() => setDrawer(null)}>
          <PluginDetails
            key={selectedPlugin.id}
            plugin={selectedPlugin}
            busyAction={busyAction}
            packageManagementReadOnly={data.package_management_read_only}
            onUpdate={update}
            onRollback={rollback}
            onUninstall={uninstall}
            onSaveConfig={saveConfig}
            onResetConfig={resetConfig}
          />
        </PluginDrawer>
      ) : null}

      {repositoryDialog ? (
        <PluginRepositoryDialog
          key={`${repositoryDialog.source?.id || "new"}:${repositoryDialog.view}`}
          source={repositoryDialog.source}
          initialView={repositoryDialog.view}
          busyAction={busyAction}
          onClose={() => setRepositoryDialog(null)}
          onSave={saveSource}
          onDelete={deleteSource}
          onRefresh={refreshCatalog}
        />
      ) : null}
    </div>
  );
}
