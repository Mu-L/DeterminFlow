import assert from "node:assert/strict";
import test from "node:test";

import type { PluginRecord } from "@/extensions/plugin-types";
import type { ExtensionStatus } from "@/extensions/types";
import type { ModelProvider, ProviderSchema } from "@/types";
import {
  buildCredentialSignature,
  buildProviderChoices,
  chooseInitialModel,
  chooseInitialProviderId,
  DESKTOP_ONBOARDING_COMPLETE_VALUE,
  DESKTOP_ONBOARDING_PENDING_VALUE,
  findManagedModelExtension,
  getPluginChanges,
  normalizeApiError,
  parseManagedModelStatus,
  requiresPluginRiskConfirmation,
  shouldStartFirstRun,
} from "./firstRunOnboardingModel";

const provider = (apiKey: string): Omit<ModelProvider, "id"> => ({
  name: "DeepSeek",
  provider_type: "deepseek",
  base_url: "https://api.deepseek.com/v1",
  api_key: apiKey,
  models: ["deepseek-chat"],
  hyperparameter_values: {},
});

const schema: ProviderSchema = {
  provider_type: "openai",
  display_name: "OpenAI",
  default_base_url: "https://api.openai.com/v1",
  api_format: "openai",
  reasoning_efforts: [],
  model_params: {},
  hyperparams: {},
};

const plugin = (id: string, desiredEnabled: boolean): PluginRecord => ({
  id,
  name: id,
  description: "",
  resource_prefix: id,
  runtime_status: desiredEnabled ? "running" : "disabled",
  error: "",
  active_enabled: desiredEnabled,
  desired_enabled: desiredEnabled,
  active_version: "1.0.0",
  desired_version: "1.0.0",
  restart_required: false,
  pending_action: null,
  dependencies: [],
  capabilities: [],
  source: {
    url: "https://example.invalid/plugins.git",
    ref: "main",
    subdirectory: `plugins/${id}`,
    trust: "official",
    resolved_commit: "1234567890abcdef",
    content_sha256: "sha256",
  },
  settings_schema: null,
  settings: {},
  config_present: false,
  page_url: null,
  processes: [],
});

test("standard web runtime never starts first-run onboarding", () => {
  assert.equal(shouldStartFirstRun({
    desktopRuntime: false,
    onboardingStatus: null,
    previewRequested: false,
  }), false);
  assert.equal(shouldStartFirstRun({
    desktopRuntime: false,
    onboardingStatus: DESKTOP_ONBOARDING_PENDING_VALUE,
    previewRequested: false,
  }), false);
});

test("desktop installs without a completion marker enter onboarding once", () => {
  assert.equal(shouldStartFirstRun({
    desktopRuntime: true,
    onboardingStatus: null,
    previewRequested: false,
  }), true);
  assert.equal(shouldStartFirstRun({
    desktopRuntime: true,
    onboardingStatus: DESKTOP_ONBOARDING_PENDING_VALUE,
    previewRequested: false,
  }), true);
  assert.equal(shouldStartFirstRun({
    desktopRuntime: true,
    onboardingStatus: DESKTOP_ONBOARDING_COMPLETE_VALUE,
    previewRequested: false,
  }), false);
});

test("development preview remains available without changing desktop state", () => {
  assert.equal(shouldStartFirstRun({
    desktopRuntime: false,
    onboardingStatus: null,
    previewRequested: true,
  }), true);
});

test("provider choices combine schemas and installed providers without duplicates", () => {
  const choices = buildProviderChoices(
    { deepseek: provider("***") },
    { openai: schema },
  );
  assert.deepEqual(choices.map((choice) => choice.id), ["deepseek", "openai"]);
  assert.equal(choices[0].configured, true);
  assert.equal(choices[0].managedBy, null);
  assert.equal(chooseInitialProviderId(choices, "openai:gpt-5"), "openai");
  assert.equal(chooseInitialProviderId(choices), "deepseek");
  assert.equal(chooseInitialModel(choices[0], "deepseek:deepseek-chat"), "deepseek-chat");
});

test("managed model extensions resolve before a provider exists", () => {
  const extension: ExtensionStatus = {
    id: "public-api",
    name: "公益模型",
    version: "1.0.0",
    description: "",
    enabled: true,
    status: "running",
    error: "",
    dependencies: [],
    capabilities: ["api.routes", "model.providers"],
    frontend: "",
    header_status: {
      endpoint: "/api/public-api/status",
      refresh_endpoint: "/api/public-api/renew",
    },
  };

  assert.equal(findManagedModelExtension([extension])?.id, "public-api");
  assert.equal(findManagedModelExtension([{ ...extension, status: "disabled" }]), null);
  assert.equal(findManagedModelExtension([{ ...extension, capabilities: [] }]), null);
});

test("managed model status keeps dynamic copy while allowing an anonymous empty catalog", () => {
  assert.deepEqual(parseManagedModelStatus({
    state: "unavailable",
    signed_in: false,
    login_pending: false,
    provider_id: null,
    models: [],
    login_endpoint: "/api/public-api/login",
    ui: {
      service_enabled: true,
      login_enabled: true,
      provider_display_name: " 笔枢公益模型 ",
      service_notice: " 动态服务说明 ",
    },
  }), {
    serviceEnabled: true,
    signedIn: false,
    loginPending: false,
    loginEnabled: true,
    loginEndpoint: "/api/public-api/login",
    providerId: null,
    providerName: "笔枢公益模型",
    serviceNotice: "动态服务说明",
    models: [],
  });
  assert.equal(parseManagedModelStatus({
    signed_in: false,
    login_pending: false,
    models: [],
    login_endpoint: "https://example.test/login",
    ui: {
      service_enabled: true,
      login_enabled: true,
      provider_display_name: "公益模型",
      service_notice: "说明",
    },
  }), null);
  assert.equal(parseManagedModelStatus({
    signed_in: false,
    login_pending: false,
    models: [],
    ui: {
      service_enabled: true,
      login_enabled: true,
      provider_display_name: "公益模型",
      service_notice: "说明",
    },
  }, "/api/public-api/login")?.loginEndpoint, "/api/public-api/login");
});

test("credential signatures normalize a trailing slash", () => {
  assert.equal(
    buildCredentialSignature("deepseek", "deepseek", "https://api.deepseek.com/v1/", " key "),
    buildCredentialSignature("deepseek", "deepseek", "https://api.deepseek.com/v1", "key"),
  );
});

test("plugin changes only contain desired-state differences", () => {
  assert.deepEqual(
    getPluginChanges(
      [plugin("enabled", true), plugin("disabled", false)],
      { enabled: true, disabled: true },
    ),
    [{ id: "disabled", enabled: true }],
  );
});

test("third-party plugins require confirmation only when being enabled", () => {
  const thirdParty = plugin("community", false);
  thirdParty.source.trust = "third_party";
  assert.equal(requiresPluginRiskConfirmation(thirdParty, true), true);
  assert.equal(requiresPluginRiskConfirmation(thirdParty, false), false);
  assert.equal(requiresPluginRiskConfirmation(plugin("official", false), true), false);
});

test("API error details are reduced to actionable copy", () => {
  assert.equal(
    normalizeApiError(
      new Error('API Error 400: {"detail":"API Key 无效或无权读取模型列表"}'),
      "验证失败",
    ),
    "API Key 无效或无权读取模型列表",
  );
});
