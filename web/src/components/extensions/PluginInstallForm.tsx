import { useState, type FormEvent } from "react";
import { GitBranch, Loader2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isValidPluginId } from "@/extensions/plugin-model";
import type {
  InstallPluginRequest,
  PluginCatalogEntry,
} from "@/extensions/plugin-types";

interface PluginInstallFormProps {
  busy: boolean;
  readOnly: boolean;
  catalog: PluginCatalogEntry[];
  catalogError: string;
  adminToken: string;
  onAdminTokenChange: (value: string) => void;
  onInstall: (request: InstallPluginRequest) => Promise<boolean>;
}

const EMPTY_FORM = {
  pluginId: "",
  source: "",
  ref: "",
  subdirectory: "",
  resourcePrefix: "",
  acknowledgeRisk: false,
};

export function PluginInstallForm({
  busy,
  readOnly,
  catalog,
  catalogError,
  adminToken,
  onAdminTokenChange,
  onInstall,
}: PluginInstallFormProps) {
  const [form, setForm] = useState(EMPTY_FORM);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const pluginId = form.pluginId.trim();
    if (!isValidPluginId(pluginId)) return;
    const installed = await onInstall({
      plugin_id: pluginId,
      source: form.source.trim(),
      ref: form.ref.trim() || undefined,
      subdirectory: form.subdirectory.trim() || undefined,
      resource_prefix: form.resourcePrefix.trim() || undefined,
      acknowledge_risk: form.acknowledgeRisk,
    });
    if (installed) setForm(EMPTY_FORM);
  };

  if (readOnly) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GitBranch aria-hidden="true" />
            Plugin 包由 Release 管理
          </CardTitle>
          <CardDescription>
            当前生产部署使用不可变插件快照。安装、更新、回退或卸载需要构建并激活新 Release；启停和可视化配置仍可在下方修改，重启后生效。
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GitBranch aria-hidden="true" />
          从 Git 仓库安装
        </CardTitle>
        <CardDescription>
          先指定插件 ID，再从互联网 Git URL 或主进程可访问的本地仓库安装；安装会锁定精确 commit。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit}>
          <div className="flex flex-col gap-2">
            <Label htmlFor="plugin-admin-token">远程管理令牌</Label>
            <Input
              id="plugin-admin-token"
              type="password"
              autoComplete="off"
              value={adminToken}
              onChange={(event) => onAdminTokenChange(event.target.value)}
              placeholder="按服务端配置填写"
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">
              仅保存在当前页面内存。服务端未配置令牌且本机直连时可留空；一旦服务端配置，包含本机连接在内都必须填写。
            </p>
          </div>
          {catalog.length > 0 || catalogError ? (
            <div className="flex flex-col gap-2">
              <Label>官方插件目录</Label>
              {catalog.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {catalog.map((entry) => (
                    <Button
                      key={`${entry.source}:${entry.id}`}
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => setForm({
                        pluginId: entry.id,
                        source: entry.source,
                        ref: entry.ref,
                        subdirectory: entry.subdirectory,
                        resourcePrefix: "",
                        acknowledgeRisk: false,
                      })}
                    >
                      {entry.id}
                      <span className="text-xs text-muted-foreground">
                        {entry.source_name}
                      </span>
                    </Button>
                  ))}
                </div>
              ) : null}
              {catalogError ? (
                <p className="text-xs text-destructive">{catalogError}</p>
              ) : null}
              <p className="text-xs text-muted-foreground">
                选择后会填入锁定来源；仍需点击安装，且只修改重启后的目标状态。
              </p>
            </div>
          ) : null}
          <div className="grid gap-4 lg:grid-cols-[minmax(12rem,1fr)_minmax(0,2fr)]">
            <div className="flex flex-col gap-2">
              <Label htmlFor="plugin-id">插件 ID</Label>
              <Input
                id="plugin-id"
                value={form.pluginId}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  pluginId: event.target.value,
                }))}
                placeholder="novel-api"
                pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                title="仅支持小写字母、数字和单个连字符分隔"
                aria-describedby="plugin-id-description"
                required
                disabled={busy}
              />
              <p id="plugin-id-description" className="text-xs text-muted-foreground">
                必填，仅支持小写 kebab-case，例如 novel-api。
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="plugin-source">仓库地址</Label>
              <Input
                id="plugin-source"
                value={form.source}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  source: event.target.value,
                }))}
                placeholder="https://git.example.com/plugin.git 或 /path/to/plugin"
                required
                disabled={busy}
              />
            </div>
          </div>
          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">高级选项</summary>
            <div className="mt-4 grid gap-4 lg:grid-cols-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="plugin-ref">Git ref</Label>
                <Input
                  id="plugin-ref"
                  value={form.ref}
                  onChange={(event) => setForm((current) => ({ ...current, ref: event.target.value }))}
                  placeholder="main / tag / commit"
                  disabled={busy}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="plugin-subdirectory">子目录</Label>
                <Input
                  id="plugin-subdirectory"
                  value={form.subdirectory}
                  onChange={(event) => setForm((current) => ({ ...current, subdirectory: event.target.value }))}
                  placeholder="可选"
                  disabled={busy}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="plugin-resource-prefix">资源前缀覆盖</Label>
                <Input
                  id="plugin-resource-prefix"
                  value={form.resourcePrefix}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    resourcePrefix: event.target.value,
                  }))}
                  placeholder="默认使用插件声明"
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                  title="仅支持小写字母、数字和单个连字符分隔"
                  disabled={busy}
                />
                <p className="text-xs text-muted-foreground">
                  通常无需填写；仅在多个插件声明了相同前缀时覆盖。
                </p>
              </div>
            </div>
          </details>

          <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <ShieldAlert className="mt-0.5 text-destructive" aria-hidden="true" />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <p className="text-sm font-medium">第三方插件风险确认</p>
              <p id="third-party-risk-description" className="text-xs text-muted-foreground">
                官方地址由服务端精确识别。其他仓库中的代码会以 DeterminFlow 相同系统权限执行，不提供沙箱或进程隔离。
              </p>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="acknowledge-third-party-risk"
                  checked={form.acknowledgeRisk}
                  onCheckedChange={(checked) => setForm((current) => ({
                    ...current,
                    acknowledgeRisk: checked,
                  }))}
                  aria-describedby="third-party-risk-description"
                  disabled={busy}
                />
                <Label htmlFor="acknowledge-third-party-risk" className="text-xs font-normal">
                  此地址若非官方源，我理解并自行承担安装与运行风险
                </Label>
              </div>
            </div>
          </div>

          <div className="flex justify-end">
            <Button
              type="submit"
              disabled={
                busy
                || !isValidPluginId(form.pluginId.trim())
                || !form.source.trim()
              }
            >
              {busy ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" /> : null}
              安装并等待重启
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
