import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface PluginStaticPageProps {
  pluginName: string;
  pageUrl: string;
}

export function PluginStaticPage({ pluginName, pageUrl }: PluginStaticPageProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle className="text-base">插件页面</CardTitle>
            <CardDescription>
              此 iframe 只是页面挂载方式，不构成插件安全隔离。
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" asChild>
            <a href={pageUrl} target="_blank" rel="noreferrer">
              <ExternalLink data-icon="inline-start" aria-hidden="true" />
              独立打开
            </a>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <iframe
          key={pageUrl}
          src={pageUrl}
          title={`${pluginName} 插件页面`}
          loading="lazy"
          referrerPolicy="no-referrer"
          className="min-h-[32rem] w-full rounded-md border bg-background"
        />
      </CardContent>
    </Card>
  );
}
