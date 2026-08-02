import { useEffect, useState } from "react";

import { CORE_TAB_IDS } from "@/core-tabs";
import { fetchExtensions } from "@/lib/api";
import {
  EMPTY_EXTENSION_CONTEXT,
  ExtensionContext,
  type ExtensionContextValue,
} from "./context-value";
import { loadRunningFrontendExtensions } from "./registry";

export function ExtensionProvider({ children }: { children: React.ReactNode }) {
  const [value, setValue] = useState<ExtensionContextValue>(EMPTY_EXTENSION_CONTEXT);

  useEffect(() => {
    let active = true;

    async function activateExtensions() {
      try {
        const data = await fetchExtensions();
        const nextValue = await loadRunningFrontendExtensions(data.extensions ?? [], CORE_TAB_IDS);
        if (active) setValue(nextValue);
      } catch (error) {
        if (!active) return;
        setValue({
          extensions: [],
          errors: [{
            extensionId: "frontend",
            message: `Extension 状态加载失败: ${error instanceof Error ? error.message : String(error)}`,
          }],
        });
      }
    }

    void activateExtensions();
    return () => {
      active = false;
    };
  }, []);

  return <ExtensionContext.Provider value={value}>{children}</ExtensionContext.Provider>;
}
