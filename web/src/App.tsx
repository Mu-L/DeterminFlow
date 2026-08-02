import { lazy, Suspense, useMemo, useState } from "react";
import { MessageSquare, LayoutDashboard, GitBranch, Users, Layers, Settings, BookOpen, Wifi, WifiOff, FileText, Sliders, Workflow, Clock, Boxes, Loader2, type LucideIcon } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToastProvider } from "@/components/ui/toast-provider";
import { CORE_TAB_IDS, isCoreTabId, type CoreTabId } from "@/core-tabs";
import { useGlobalEvents } from "./hooks/useGlobalEvents";
import { useExtensions } from "./extensions/context-value";

const ChatPage = lazy(() => import("./pages/ChatPage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const GraphPage = lazy(() => import("./pages/GraphPage"));
const RoundtablePage = lazy(() => import("./pages/RoundtablePage"));
const OrchestrationPage = lazy(() => import("./pages/OrchestrationPage"));
const WorkflowPage = lazy(() => import("./pages/WorkflowPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SkillsPage = lazy(() => import("./pages/SkillsPage"));
const RulesPage = lazy(() => import("./pages/RulesPage"));
const SystemPromptPage = lazy(() => import("./pages/SystemPromptPage"));
const CompressionConfigPage = lazy(() => import("./pages/CompressionConfigPage"));
const CronPage = lazy(() => import("./pages/CronPage"));
const ExtensionsPage = lazy(() => import("./pages/ExtensionsPage"));

/** Tab 配置：value + 图标 + 标签 + active 样式 */
interface TabConfig {
  value: string;
  icon: LucideIcon;
  label: string;
  activeClass: string;
}

const CORE_TAB_METADATA: Record<CoreTabId, Omit<TabConfig, "value">> = {
  chat: { icon: MessageSquare, label: "对话", activeClass: "data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-400" },
  dashboard: { icon: LayoutDashboard, label: "看板", activeClass: "data-[state=active]:bg-purple-500/20 data-[state=active]:text-purple-400" },
  graph: { icon: GitBranch, label: "图谱", activeClass: "data-[state=active]:bg-cyan-500/20 data-[state=active]:text-cyan-400" },
  roundtable: { icon: Users, label: "圆桌", activeClass: "data-[state=active]:bg-green-500/20 data-[state=active]:text-green-400" },
  orchestration: { icon: Layers, label: "编排", activeClass: "data-[state=active]:bg-amber-500/20 data-[state=active]:text-amber-400" },
  workflow: { icon: Workflow, label: "Workflow", activeClass: "data-[state=active]:bg-purple-500/20 data-[state=active]:text-purple-400" },
  cron: { icon: Clock, label: "Cron", activeClass: "data-[state=active]:bg-amber-500/20 data-[state=active]:text-amber-400" },
  skills: { icon: BookOpen, label: "Skills", activeClass: "data-[state=active]:bg-pink-500/20 data-[state=active]:text-pink-400" },
  rules: { icon: BookOpen, label: "Rules", activeClass: "data-[state=active]:bg-red-500/20 data-[state=active]:text-red-400" },
  "system-prompt": { icon: FileText, label: "System Prompt", activeClass: "data-[state=active]:bg-teal-500/20 data-[state=active]:text-teal-400" },
  "compression-config": { icon: Sliders, label: "压缩配置", activeClass: "data-[state=active]:bg-orange-500/20 data-[state=active]:text-orange-400" },
  settings: { icon: Settings, label: "配置", activeClass: "data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-400" },
  extensions: { icon: Boxes, label: "Extensions", activeClass: "data-[state=active]:bg-cyan-500/20 data-[state=active]:text-cyan-400" },
};

const CORE_TAB_CONFIG: TabConfig[] = CORE_TAB_IDS.map((value) => ({
  value,
  ...CORE_TAB_METADATA[value],
}));

/** 页面路由映射 */
const CORE_PAGE_MAP: Record<CoreTabId, React.ComponentType> = {
  chat: ChatPage,
  dashboard: DashboardPage,
  graph: GraphPage,
  roundtable: RoundtablePage,
  orchestration: OrchestrationPage,
  workflow: WorkflowPage,
  cron: CronPage,
  skills: SkillsPage,
  rules: RulesPage,
  "system-prompt": SystemPromptPage,
  "compression-config": CompressionConfigPage,
  settings: SettingsPage,
  extensions: ExtensionsPage,
};

// 全局事件包装器组件（在 ToastProvider 内部使用）
function GlobalEventsWrapper() {
  useGlobalEvents();
  return null;
}

function PageLoadingFallback() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <span>正在加载页面...</span>
      </div>
    </div>
  );
}

function App() {
  const extensions = useExtensions();
  const [activeTab, setActiveTab] = useState("chat");
  const [wsConnected] = useState(true);
  const extensionPages = useMemo(() => extensions.flatMap((extension) => extension.pages || []), [extensions]);
  const tabs = useMemo<TabConfig[]>(() => [
    ...CORE_TAB_CONFIG,
    ...extensionPages.map((page) => ({
      value: page.id,
      icon: page.icon,
      label: page.label,
      activeClass: page.activeClass,
    })),
  ], [extensionPages]);
  const ExtensionPage = extensionPages.find((page) => page.id === activeTab)?.component;
  const CorePage = isCoreTabId(activeTab) ? CORE_PAGE_MAP[activeTab] : undefined;

  return (
    <ToastProvider>
      <GlobalEventsWrapper />
      <div className="min-h-screen bg-slate-900 flex flex-col">
      {/* Top Navigation Bar */}
      <header className="fixed top-0 left-0 right-0 z-50 h-14 bg-slate-800/80 backdrop-blur-sm border-b border-slate-700/50">
        <div className="h-full flex items-center gap-3 px-4">
          {/* Logo & Title */}
          <div className="flex shrink-0 items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-indigo-500 flex items-center justify-center">
              <span className="text-white text-sm font-bold">AI</span>
            </div>
            <h1 className="hidden text-lg font-bold text-slate-100 2xl:block">
              Agent Control Panel
            </h1>
          </div>

          {/* Tabs */}
          <Tabs className="min-w-0 flex-1" value={activeTab} onValueChange={setActiveTab}>
            <div className="overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              <TabsList className="w-max justify-start bg-slate-800/80 border border-slate-700/50" role="tablist" aria-label="主导航">
                {tabs.map((tab) => {
                  const Icon = tab.icon;
                  return (
                  <TabsTrigger
                    key={tab.value}
                    value={tab.value}
                    className={`gap-2 ${tab.activeClass}`}
                    role="tab"
                    aria-selected={activeTab === tab.value}
                  >
                    <Icon size={16} aria-hidden="true" />
                    <span>{tab.label}</span>
                  </TabsTrigger>
                  );
                })}
              </TabsList>
            </div>
          </Tabs>

          {/* Status Indicator */}
          <div className="flex shrink-0 items-center gap-3" aria-live="polite">
            <div className="flex items-center gap-2 text-sm">
              {wsConnected ? (
                <Wifi size={14} className="text-green-400" aria-hidden="true" />
              ) : (
                <WifiOff size={14} className="text-red-400" aria-hidden="true" />
              )}
              <span className={`hidden xl:inline ${wsConnected ? "text-green-400" : "text-red-400"}`}>
                {wsConnected ? "已连接" : "断开"}
              </span>
              <span className="sr-only">{wsConnected ? "WebSocket 已连接" : "WebSocket 连接断开"}</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 pt-14" role="main" aria-label="主内容区域">
        <Suspense fallback={<PageLoadingFallback />}>
          {CorePage ? <CorePage /> : ExtensionPage ? <ExtensionPage /> : null}
        </Suspense>
      </main>
    </div>
    </ToastProvider>
  );
}

export default App;
