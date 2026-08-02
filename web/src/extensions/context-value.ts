import { createContext, useContext } from "react";

import type { FrontendExtension } from "./types";
import type { ExtensionActivationError } from "./validation";

export interface ExtensionContextValue {
  extensions: FrontendExtension[];
  errors: ExtensionActivationError[];
}

export const EMPTY_EXTENSION_CONTEXT: ExtensionContextValue = { extensions: [], errors: [] };
export const ExtensionContext = createContext<ExtensionContextValue>(EMPTY_EXTENSION_CONTEXT);

export function useExtensions(): FrontendExtension[] {
  return useContext(ExtensionContext).extensions;
}

export function useExtensionActivationErrors(): ExtensionActivationError[] {
  return useContext(ExtensionContext).errors;
}
