import { KeyRound } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface PluginAdminTokenFormProps {
  value: string;
  onChange: (value: string) => void;
}

export function PluginAdminTokenForm({ value, onChange }: PluginAdminTokenFormProps) {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-4">
        <KeyRound className="mt-0.5 shrink-0" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">这是 DeterminFlow 管理授权</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            它用于授权远程安装、启停和仓库变更，不是 Git 仓库访问令牌。
          </p>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="plugin-admin-token">管理令牌</Label>
        <Input
          id="plugin-admin-token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="本机直连且服务端未配置时可留空"
        />
        <p className="text-xs leading-5 text-muted-foreground">
          仅保存在当前页面内存，刷新页面后清除。
        </p>
      </div>
    </div>
  );
}
