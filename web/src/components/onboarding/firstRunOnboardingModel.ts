import type { PluginRecord } from "@/extensions/plugin-types";
import type { ExtensionStatus } from "@/extensions/types";
import type { ModelProvider, ProviderSchema } from "@/types";

export const DESKTOP_ONBOARDING_PENDING_VALUE = "pending";
export const DESKTOP_ONBOARDING_COMPLETE_VALUE = "complete";

type ProviderMap = Record<string, Omit<ModelProvider, "id">>;

export interface ProviderChoice {
  id: string;
  providerType: string;
  name: string;
  baseUrl: string;
  models: string[];
  configured: boolean;
  managedBy: string | null;
}

export interface PluginChange {
  id: string;
  enabled: boolean;
}

export interface ManagedModelStatus {
  serviceEnabled: boolean;
  signedIn: boolean;
  loginPending: boolean;
  loginEnabled: boolean;
  loginEndpoint: string;
  providerId: string | null;
  providerName: string;
  serviceNotice: string;
  models: string[];
}

export function shouldStartFirstRun({
  desktopRuntime,
  onboardingStatus,
  previewRequested,
}: {
  desktopRuntime: boolean;
  onboardingStatus: string | null;
  previewRequested: boolean;
}): boolean {
  if (previewRequested) return true;
  return desktopRuntime && onboardingStatus !== DESKTOP_ONBOARDING_COMPLETE_VALUE;
}

export function shouldConfirmManagedModelSelection({
  currentSelectionManaged,
  signedIn,
  confirmationAccepted,
}: {
  currentSelectionManaged: boolean;
  signedIn: boolean;
  confirmationAccepted: boolean;
}): boolean {
  return !currentSelectionManaged && !signedIn && !confirmationAccepted;
}

export function buildProviderChoices(
  providers: ProviderMap,
  schemas: Record<string, ProviderSchema>,
): ProviderChoice[] {
  const configuredChoices = Object.entries(providers).map(([id, provider]) => {
    const providerType = provider.provider_type
      || schemas[id]?.provider_type
      || "openai_compatible";
    const schema = schemas[providerType];
    return {
      id,
      providerType,
      name: provider.name || schema?.display_name || id,
      baseUrl: provider.base_url || schema?.default_base_url || "",
      models: provider.models || [],
      configured: provider.api_key === "***",
      managedBy: provider.managed_by?.trim() || null,
    };
  });

  const configuredTypes = new Set(
    configuredChoices.map((choice) => choice.providerType),
  );
  const templateChoices = Object.entries(schemas).flatMap(([providerType, schema]) => (
    configuredTypes.has(providerType)
      ? []
      : [{
          id: providerType,
          providerType,
          name: schema.display_name,
          baseUrl: schema.default_base_url,
          models: [],
          configured: false,
          managedBy: null,
        }]
  ));

  return [...configuredChoices, ...templateChoices];
}

export function findManagedModelExtension(
  extensions: ExtensionStatus[],
): ExtensionStatus | null {
  return extensions.find((extension) => (
    extension.enabled
    && (extension.status === "running" || extension.status === "degraded")
    && extension.capabilities.includes("model.providers")
    && Boolean(extension.header_status?.endpoint)
    && Boolean(extension.header_status?.refresh_endpoint)
  )) || null;
}

function safeApiEndpoint(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/api/")) return null;
  if (
    value.startsWith("//")
    || value.includes("?")
    || value.includes("#")
    || value.includes("\\")
    || value.split("/").includes("..")
  ) return null;
  return value;
}

export function parseManagedModelStatus(
  value: unknown,
  fallbackLoginEndpoint?: string,
): ManagedModelStatus | null {
  if (typeof value !== "object" || value === null) return null;
  const body = value as Record<string, unknown>;
  if (typeof body.ui !== "object" || body.ui === null) return null;
  const ui = body.ui as Record<string, unknown>;
  const providerName = typeof ui.provider_display_name === "string"
    ? ui.provider_display_name.trim()
    : "";
  const serviceNotice = typeof ui.service_notice === "string"
    ? ui.service_notice.trim()
    : "";
  const loginEndpoint = safeApiEndpoint(body.login_endpoint)
    || safeApiEndpoint(fallbackLoginEndpoint);
  if (
    typeof ui.service_enabled !== "boolean"
    || typeof ui.login_enabled !== "boolean"
    || typeof body.signed_in !== "boolean"
    || typeof body.login_pending !== "boolean"
    || !loginEndpoint
    || !providerName
    || providerName.length > 80
    || !serviceNotice
    || serviceNotice.length > 600
  ) return null;

  const providerId = typeof body.provider_id === "string" && body.provider_id.trim()
    ? body.provider_id.trim()
    : null;
  const models = Array.isArray(body.models)
    ? [...new Set(body.models.filter((model): model is string => (
        typeof model === "string" && Boolean(model.trim())
      )).map((model) => model.trim()))]
    : [];

  return {
    serviceEnabled: ui.service_enabled,
    signedIn: body.signed_in,
    loginPending: body.login_pending,
    loginEnabled: ui.login_enabled,
    loginEndpoint,
    providerId,
    providerName,
    serviceNotice,
    models,
  };
}

export function chooseInitialProviderId(
  choices: ProviderChoice[],
  preferredModel: string | null = null,
): string {
  const preferredProvider = preferredModel?.split(":", 1)[0];
  return choices.find((choice) => choice.id === preferredProvider)?.id
    || choices.find((choice) => choice.providerType === "deepseek")?.id
    || choices.find((choice) => choice.configured)?.id
    || choices[0]?.id
    || "";
}

export function chooseInitialModel(
  choice: ProviderChoice | undefined,
  preferredModel: string | null,
): string {
  if (!choice) return "";
  const separatorIndex = preferredModel?.indexOf(":") ?? -1;
  const preferredProvider = separatorIndex > 0 ? preferredModel?.slice(0, separatorIndex) : "";
  const model = separatorIndex > 0 ? preferredModel?.slice(separatorIndex + 1) : "";
  return preferredProvider === choice.id && model && choice.models.includes(model)
    ? model
    : choice.models[0] || "";
}

export function buildCredentialSignature(
  providerId: string,
  providerType: string,
  baseUrl: string,
  apiKey: string,
): string {
  return `${providerId}\n${providerType}\n${baseUrl.trim().replace(/\/$/, "")}\n${apiKey.trim()}`;
}

export function getPluginChanges(
  plugins: PluginRecord[],
  selection: Record<string, boolean>,
): PluginChange[] {
  return plugins.flatMap((plugin) => {
    const enabled = selection[plugin.id] ?? plugin.desired_enabled;
    return enabled === plugin.desired_enabled ? [] : [{ id: plugin.id, enabled }];
  });
}

export function requiresPluginRiskConfirmation(
  plugin: PluginRecord,
  enabled: boolean,
): boolean {
  return enabled && plugin.source.trust === "third_party";
}

export function normalizeApiError(reason: unknown, fallback: string): string {
  if (!(reason instanceof Error)) return fallback;
  const detailMatch = reason.message.match(/"detail"\s*:\s*"([^"]+)"/);
  if (detailMatch?.[1]) return detailMatch[1];
  return reason.message.replace(/^API Error \d+:\s*/, "").trim() || fallback;
}
