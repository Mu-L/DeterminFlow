import { createContext, useContext } from "react";

import type { ExtensionStatus, FrontendExtension } from "./types";
import type { ExtensionActivationError } from "./validation";

export interface ExtensionContextValue {
  extensions: FrontendExtension[];
  statuses: ExtensionStatus[];
  errors: ExtensionActivationError[];
}

export const EMPTY_EXTENSION_CONTEXT: ExtensionContextValue = { extensions: [], statuses: [], errors: [] };
export const ExtensionContext = createContext<ExtensionContextValue>(EMPTY_EXTENSION_CONTEXT);

export function useExtensions(): FrontendExtension[] {
  return useContext(ExtensionContext).extensions;
}

export function useExtensionActivationErrors(): ExtensionActivationError[] {
  return useContext(ExtensionContext).errors;
}

export function useExtensionStatuses(): ExtensionStatus[] {
  return useContext(ExtensionContext).statuses;
}
