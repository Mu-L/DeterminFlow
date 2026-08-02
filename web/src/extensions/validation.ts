import type { ExtensionStatus, FrontendExtension } from "./types";

export interface LoadedFrontendExtension {
  status: ExtensionStatus;
  extension: FrontendExtension;
}

export interface ExtensionActivationError {
  extensionId: string;
  message: string;
}

export interface FrontendExtensionValidationResult {
  extensions: FrontendExtension[];
  errors: ExtensionActivationError[];
}

export type LoadedFrontendExtensionResult =
  | { entry: LoadedFrontendExtension }
  | { error: ExtensionActivationError };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isComponentLike(value: unknown): boolean {
  return typeof value === "function" || isObject(value);
}

function isExtensionPage(value: unknown): boolean {
  return isObject(value)
    && typeof value.id === "string"
    && typeof value.label === "string"
    && typeof value.activeClass === "string"
    && isComponentLike(value.icon)
    && isComponentLike(value.component);
}

function isFrontendExtension(value: unknown): value is FrontendExtension {
  if (!isObject(value) || typeof value.id !== "string") return false;
  if (value.pages !== undefined && (!Array.isArray(value.pages) || !value.pages.every(isExtensionPage))) {
    return false;
  }
  return value.agentEditor === undefined || isComponentLike(value.agentEditor);
}

export function parseLoadedFrontendExtension(
  status: ExtensionStatus,
  value: unknown,
): LoadedFrontendExtensionResult {
  if (!isFrontendExtension(value)) {
    return {
      error: {
        extensionId: status.id,
        message: "前端模块 default export 格式无效",
      },
    };
  }
  return { entry: { status, extension: value } };
}

export function isRunningFrontendExtension(status: ExtensionStatus): boolean {
  return status.enabled && status.status === "running" && status.frontend.trim().length > 0;
}

export function validateFrontendExtensions(
  entries: LoadedFrontendExtension[],
  reservedPageIds: readonly string[],
): FrontendExtensionValidationResult {
  const extensions: FrontendExtension[] = [];
  const errors: ExtensionActivationError[] = [];
  const extensionIds = new Set<string>();
  const pageOwners = new Map(reservedPageIds.map((pageId) => [pageId, "core"]));

  for (const { status, extension } of entries) {
    const entryErrors: ExtensionActivationError[] = [];
    const frontendId = status.frontend.trim();
    const extensionId = extension.id.trim();
    const localPageIds = new Set<string>();

    if (!extensionId) {
      entryErrors.push({ extensionId: status.id, message: "前端模块缺少 extension id" });
    }
    if (extensionId !== status.id) {
      entryErrors.push({
        extensionId: status.id,
        message: `前端模块 id "${extension.id}" 与后端 extension id "${status.id}" 不一致`,
      });
    }
    if (extensionId !== frontendId) {
      entryErrors.push({
        extensionId: status.id,
        message: `前端模块 id "${extension.id}" 与 manifest frontend "${status.frontend}" 不一致`,
      });
    }
    if (extensionIds.has(extensionId)) {
      entryErrors.push({
        extensionId: status.id,
        message: `前端 extension id "${extensionId}" 重复`,
      });
    }

    for (const page of extension.pages ?? []) {
      const pageId = page.id.trim();
      if (!pageId) {
        entryErrors.push({ extensionId: status.id, message: "扩展页面缺少 page id" });
        continue;
      }
      if (pageId !== page.id) {
        entryErrors.push({
          extensionId: status.id,
          message: `扩展页面 id "${page.id}" 不能包含首尾空格`,
        });
      }
      if (localPageIds.has(pageId)) {
        entryErrors.push({
          extensionId: status.id,
          message: `扩展内部 page id "${pageId}" 重复`,
        });
        continue;
      }
      localPageIds.add(pageId);

      const owner = pageOwners.get(pageId);
      if (owner) {
        entryErrors.push({
          extensionId: status.id,
          message: owner === "core"
            ? `page id "${pageId}" 与核心 Tab 冲突`
            : `page id "${pageId}" 已被扩展 "${owner}" 使用`,
        });
      }
    }

    if (entryErrors.length > 0) {
      errors.push(...entryErrors);
      continue;
    }

    extensionIds.add(extensionId);
    for (const pageId of localPageIds) {
      pageOwners.set(pageId, extensionId);
    }
    extensions.push(extension);
  }

  return { extensions, errors };
}
