import { Download, X } from "lucide-react";

import { useDesktopUpdate } from "./context-value";

export function DesktopUpdateNotice({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { enabled, phase, update, noticeDismissed, dismissNotice } = useDesktopUpdate();

  if (!enabled || phase !== "available" || !update || noticeDismissed) return null;

  return (
    <aside
      aria-live="polite"
      aria-label="桌面更新通知"
      className="fixed right-4 top-16 z-40 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-indigo-500/30 bg-slate-800 p-4 shadow-2xl shadow-black/30"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-indigo-500/15 p-2 text-indigo-300">
          <Download size={18} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-100">发现新版本 v{update.version}</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">查看版本信息并确认后再下载，不会强制更新。</p>
          <button
            type="button"
            onClick={onOpenSettings}
            className="mt-3 min-h-[40px] rounded-lg bg-indigo-600 px-3 text-sm font-medium text-white transition-colors hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            查看更新
          </button>
        </div>
        <button
          type="button"
          onClick={dismissNotice}
          aria-label="暂时关闭更新通知"
          className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-700 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}
