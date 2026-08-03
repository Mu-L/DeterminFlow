import {
  AlertTriangle,
  GitBranch,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { PluginCatalogSource } from "@/extensions/plugin-types";

interface PluginRepositoryListProps {
  sources: PluginCatalogSource[];
  busyAction: string;
  readOnly: boolean;
  onBrowse: (sourceId: string) => void;
  onEdit: (source: PluginCatalogSource) => void;
  onDeleteRequest: (source: PluginCatalogSource) => void;
  onRefresh: () => void;
}

function shortCommit(value: string): string {
  return value ? value.slice(0, 8) : "-";
}

export function PluginRepositoryList({
  sources,
  busyAction,
  readOnly,
  onBrowse,
  onEdit,
  onDeleteRequest,
  onRefresh,
}: PluginRepositoryListProps) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 border-b pb-4">
        <div>
          <CardTitle className="text-base">插件仓库</CardTitle>
          <CardDescription className="mt-1">
            仓库会持久化保存，用于浏览插件和检查已安装插件更新。
          </CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={Boolean(busyAction)}
        >
          {busyAction === "catalog:refresh"
            ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
            : <RefreshCw data-icon="inline-start" aria-hidden="true" />}
          刷新全部
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {sources.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 p-6 text-center">
            <GitBranch className="text-muted-foreground" aria-hidden="true" />
            <p className="text-sm font-medium">还没有可用的插件仓库</p>
            <p className="text-xs text-muted-foreground">添加仓库并拉取目录后，插件会显示在这里。</p>
          </div>
        ) : (
          <div className="divide-y">
            {sources.map((source) => {
              return (
                <article
                  key={source.id}
                  className="grid gap-4 px-5 py-4 lg:grid-cols-[minmax(18rem,1.5fr)_minmax(10rem,0.7fr)_minmax(8rem,0.5fr)_auto] lg:items-center"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted text-sm font-semibold">
                      {source.name.trim().slice(0, 1) || "R"}
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-medium">{source.name}</h3>
                        <Badge variant={source.builtin ? "secondary" : "outline"}>
                          {source.builtin
                            ? <ShieldCheck aria-hidden="true" />
                            : <ShieldAlert aria-hidden="true" />}
                          {source.builtin ? "内置官方" : "第三方"}
                        </Badge>
                      </div>
                      <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={source.url}>
                        {source.url}
                      </p>
                      <p className="mt-1 font-mono text-xs text-muted-foreground">{source.ref}</p>
                    </div>
                  </div>

                  <div>
                    {source.error ? (
                      <Badge variant="destructive"><AlertTriangle aria-hidden="true" />同步失败</Badge>
                    ) : (
                      <Badge variant="secondary">已同步</Badge>
                    )}
                    <p className={`mt-1 line-clamp-1 text-xs ${source.error ? "text-destructive" : "text-muted-foreground"}`} title={source.error || source.resolved_commit}>
                      {source.error || `commit ${shortCommit(source.resolved_commit)}`}
                    </p>
                  </div>

                  <div>
                    <p className="text-sm font-medium">{source.plugin_count} 个插件</p>
                    <p className="mt-1 text-xs text-muted-foreground">目录索引</p>
                  </div>

                  <div className="flex flex-wrap justify-end gap-2">
                    {!source.builtin ? (
                      <>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => onEdit(source)}
                          disabled={Boolean(busyAction) || readOnly}
                          aria-label={`编辑 ${source.name}`}
                        >
                          <Pencil aria-hidden="true" />管理
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => onDeleteRequest(source)}
                          disabled={Boolean(busyAction) || readOnly}
                        >
                          <Trash2 aria-hidden="true" />删除
                        </Button>
                      </>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => onBrowse(source.id)}
                      disabled={Boolean(busyAction) || Boolean(source.error)}
                    >
                      查看插件
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
