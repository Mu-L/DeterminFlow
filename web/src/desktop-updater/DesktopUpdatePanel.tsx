import { AlertCircle, CheckCircle2, Download, RefreshCw } from "lucide-react";

import { useDesktopUpdate } from "./context-value";

export function DesktopUpdatePanel() {
  const {
    enabled,
    phase,
    currentVersion,
    update,
    progress,
    error,
    checkForUpdates,
    installUpdate,
  } = useDesktopUpdate();

  if (!enabled) return null;

  const busy = phase === "checking" || phase === "downloading" || phase === "installing";
  const confirmInstall = () => {
    if (!update) return;
    const confirmed = window.confirm(
      `将下载并安装 DeterminFlow v${update.version}。安装时应用会自动关闭并重新启动，是否继续？`,
    );
    if (confirmed) void installUpdate();
  };

  return (
    <section aria-label="桌面应用更新" className="overflow-hidden rounded-xl border border-slate-700/50 bg-slate-800/80">
      <div className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 text-indigo-400"><Download size={18} aria-hidden="true" /></div>
          <div>
            <h3 className="text-base font-semibold text-slate-100">桌面应用</h3>
            <p className="mt-1 text-sm text-slate-400">
              当前版本 <span className="font-mono text-slate-200">{currentVersion ? `v${currentVersion}` : "读取中..."}</span>
            </p>
            <p className="mt-1 text-xs text-slate-500">每天启动时后台检查一次；只有确认后才会下载和安装。</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void checkForUpdates()}
          disabled={busy}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border border-slate-600 px-4 text-sm text-slate-200 transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <RefreshCw size={15} className={phase === "checking" ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
          {phase === "checking" ? "检查中..." : "检查更新"}
        </button>
      </div>

      {(phase === "up-to-date" || phase === "error" || update) && (
        <div className="border-t border-slate-700/50 px-5 py-4">
          {phase === "up-to-date" && (
            <div role="status" className="flex items-center gap-2 text-sm text-emerald-400">
              <CheckCircle2 size={16} aria-hidden="true" />
              已是最新版本
            </div>
          )}

          {phase === "error" && error && (
            <div role="alert" className="flex items-start gap-2 text-sm text-amber-400">
              <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {update && (
            <div className="space-y-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-100">可更新至 v{update.version}</p>
                  {update.date && <p className="mt-1 text-xs text-slate-500">发布于 {new Date(update.date).toLocaleDateString("zh-CN")}</p>}
                </div>
                <button
                  type="button"
                  onClick={confirmInstall}
                  disabled={busy}
                  className="min-h-[44px] rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                >
                  {phase === "downloading" ? "下载中..." : phase === "installing" ? "正在安装..." : "下载并安装"}
                </button>
              </div>

              {update.body && (
                <div className="max-h-28 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-900/60 p-3 text-xs leading-5 text-slate-400">
                  {update.body}
                </div>
              )}

              {(phase === "downloading" || phase === "installing") && (
                <div role="status" aria-live="polite">
                  <div className="mb-1 flex justify-between text-xs text-slate-400">
                    <span>{phase === "installing" ? "正在启动安装程序" : "正在下载更新"}</span>
                    {progress !== null && <span>{progress}%</span>}
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-700">
                    <div
                      className={`h-full rounded-full bg-indigo-500 transition-[width] ${progress === null ? "w-1/3 animate-pulse motion-reduce:animate-none" : ""}`}
                      style={progress === null ? undefined : { width: `${progress}%` }}
                    />
                  </div>
                </div>
              )}

              {phase === "available" && error && (
                <div role="alert" className="flex items-start gap-2 text-sm text-amber-400">
                  <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
