import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  LogIn,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import {
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import type { ExtensionStatus } from "@/extensions/types";
import {
  addModelProvider,
  discoverProviderModels,
  updateModelProvider,
  updateSessionModel,
} from "@/lib/api";
import type { ModelProvider, ProviderSchema } from "@/types";
import {
  ANONYMOUS_MANAGED_MODEL,
  ANONYMOUS_MANAGED_MODEL_NOTE,
  buildCredentialSignature,
  buildProviderChoices,
  chooseInitialProviderId,
  findManagedModelExtension,
  type ManagedModelStatus,
  normalizeApiError,
  parseManagedModelStatus,
  type ProviderChoice,
  shouldConfirmManagedModelSelection,
} from "./firstRunOnboardingModel";

type ProviderMap = Record<string, Omit<ModelProvider, "id">>;
type ModelSource = "public" | "owned";

interface FirstRunModelScreenProps {
  active: boolean;
  offset: number;
  providers: ProviderMap;
  schemas: Record<string, ProviderSchema>;
  extensions: ExtensionStatus[];
  preferredModel: string | null;
  currentMainModel: string | null;
  currentMainSessionId: string | null;
  onBack: () => void;
  onNext: () => void;
  onManagedProviderChange: (pluginId: string | null) => void;
}

async function requestManagedModelStatus(
  endpoint: string,
  method: "GET" | "POST" = "GET",
): Promise<ManagedModelStatus> {
  const response = await fetch(endpoint, {
    method,
    headers: { Accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body === "object"
      && body !== null
      && "detail" in body
      && typeof body.detail === "string"
      ? body.detail
      : "公益模型服务暂时无法连接";
    throw new Error(detail);
  }
  const fallbackLoginEndpoint = endpoint.endsWith("/status")
    ? `${endpoint.slice(0, -"/status".length)}/login`
    : undefined;
  const status = parseManagedModelStatus(body, fallbackLoginEndpoint);
  if (!status) throw new Error("公益模型服务返回了无法识别的状态");
  return status;
}

function ModelSourceChoice({
  active,
  title,
  description,
  recommended = false,
  onClick,
}: {
  active: boolean;
  title: string;
  description: string;
  recommended?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`first-run-model-source-choice ${active ? "is-selected" : ""}`}
      aria-pressed={active}
      onClick={onClick}
    >
      <span className="first-run-model-source-choice__radio" />
      <span>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      {recommended ? <em>推荐</em> : null}
    </button>
  );
}

export function FirstRunModelScreen({
  active,
  offset,
  providers,
  schemas,
  extensions,
  preferredModel,
  currentMainModel,
  currentMainSessionId,
  onBack,
  onNext,
  onManagedProviderChange,
}: FirstRunModelScreenProps) {
  const choices = useMemo(() => buildProviderChoices(providers, schemas), [providers, schemas]);
  const managedExtension = useMemo(
    () => findManagedModelExtension(extensions),
    [extensions],
  );
  const standardChoices = useMemo(
    () => choices.filter((choice) => !choice.managedBy),
    [choices],
  );
  const initialProviderId = chooseInitialProviderId(standardChoices, preferredModel);
  const initialChoice = standardChoices.find((choice) => choice.id === initialProviderId);
  const currentProviderId = currentMainModel?.split(":", 1)[0] || "";
  const currentProviderManaged = Boolean(
    currentProviderId
      && providers[currentProviderId]?.managed_by === managedExtension?.id,
  );

  const [source, setSource] = useState<ModelSource>(
    managedExtension || currentProviderManaged ? "public" : "owned",
  );
  const [providerId, setProviderId] = useState(initialProviderId);
  const [baseUrl, setBaseUrl] = useState(initialChoice?.baseUrl || "");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [showBaseUrl, setShowBaseUrl] = useState(false);
  const [models, setModels] = useState<string[]>(initialChoice?.models || []);
  const [selectedModel, setSelectedModel] = useState("");
  const [validatedSignature, setValidatedSignature] = useState("");
  const [validationState, setValidationState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [managedStatus, setManagedStatus] = useState<ManagedModelStatus | null>(null);
  const [managedLoading, setManagedLoading] = useState(Boolean(managedExtension));
  const [loginLoading, setLoginLoading] = useState(false);
  const [selectedPublicModel, setSelectedPublicModel] = useState(ANONYMOUS_MANAGED_MODEL);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [managedConfirmationOpen, setManagedConfirmationOpen] = useState(false);
  const [managedConfirmationAccepted, setManagedConfirmationAccepted] = useState(currentProviderManaged);
  const modelSelectRef = useRef<HTMLSelectElement>(null);
  const managedConfirmRef = useRef<HTMLButtonElement>(null);

  const selectedChoice = standardChoices.find((choice) => choice.id === providerId);
  const credentialSignature = buildCredentialSignature(
    providerId,
    selectedChoice?.providerType || "openai_compatible",
    baseUrl,
    apiKey,
  );
  const ownedValidated = validationState === "success"
    && validatedSignature === credentialSignature;
  const publicModels = managedStatus?.signedIn
    ? managedStatus.models
    : [ANONYMOUS_MANAGED_MODEL];
  const publicReady = Boolean(
    managedStatus?.serviceEnabled
      && (!managedStatus.signedIn || (
        managedStatus.providerId
        && managedStatus.models.length > 0
      )),
  );

  const loadManagedStatus = useCallback(async (showLoading = true) => {
    const endpoint = managedExtension?.header_status?.endpoint;
    if (!endpoint) return;
    if (showLoading) setManagedLoading(true);
    try {
      const status = await requestManagedModelStatus(endpoint);
      setManagedStatus(status);
      setError(status.serviceEnabled ? "" : "公益模型服务暂时不可用");
    } catch (reason) {
      setError(normalizeApiError(reason, "公益模型服务暂时无法连接"));
    } finally {
      if (showLoading) setManagedLoading(false);
    }
  }, [managedExtension]);

  useEffect(() => {
    if (!active || !managedExtension) return;
    void loadManagedStatus();
  }, [active, loadManagedStatus, managedExtension]);

  useEffect(() => {
    if (!active || !managedStatus?.loginPending) return;
    const interval = window.setInterval(() => {
      void loadManagedStatus(false);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [active, loadManagedStatus, managedStatus?.loginPending]);

  useEffect(() => {
    const nextModels = managedStatus?.signedIn
      ? managedStatus.models
      : [ANONYMOUS_MANAGED_MODEL];
    setSelectedPublicModel((current) => (
      nextModels.includes(current) ? current : nextModels[0] || ""
    ));
  }, [managedStatus?.models, managedStatus?.signedIn]);

  useEffect(() => {
    if (!active) return;
    onManagedProviderChange(source === "public" ? managedExtension?.id || null : null);
  }, [active, managedExtension?.id, onManagedProviderChange, source]);

  useEffect(() => {
    if (!managedConfirmationOpen) return;
    managedConfirmRef.current?.focus();
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setManagedConfirmationOpen(false);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [managedConfirmationOpen]);

  const selectOwnedProvider = (choice: ProviderChoice) => {
    setProviderId(choice.id);
    setBaseUrl(choice.baseUrl);
    setApiKey("");
    setModels(choice.models);
    setSelectedModel("");
    setValidatedSignature("");
    setValidationState("idle");
    setError("");
  };

  const startLogin = async () => {
    if (!managedStatus?.loginEnabled || managedStatus.loginPending) return;
    setLoginLoading(true);
    setError("");
    try {
      setManagedStatus(await requestManagedModelStatus(
        managedStatus.loginEndpoint,
        "POST",
      ));
    } catch (reason) {
      setError(normalizeApiError(reason, "无法开始登录"));
    } finally {
      setLoginLoading(false);
    }
  };

  const validateCredential = async () => {
    if (!providerId || !baseUrl.trim() || (!apiKey.trim() && !selectedChoice?.configured)) {
      setValidationState("error");
      setError("请填写访问密钥与 API 地址");
      return;
    }
    setValidationState("loading");
    setError("");
    try {
      const result = await discoverProviderModels({
        provider_id: providerId,
        provider_type: selectedChoice?.providerType,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || undefined,
      });
      if (result.models.length === 0) throw new Error("供应商未返回可用模型");
      setModels(result.models);
      setSelectedModel((current) => (
        result.models.includes(current) ? current : result.models[0]
      ));
      setValidatedSignature(credentialSignature);
      setValidationState("success");
      window.requestAnimationFrame(() => modelSelectRef.current?.focus());
    } catch (reason) {
      setValidationState("error");
      setError(normalizeApiError(reason, "模型连接验证失败"));
    }
  };

  const persistModel = async () => {
    setSaving(true);
    setError("");
    try {
      if (!currentMainSessionId) {
        throw new Error("Main 会话尚未准备好，请重试");
      }
      let modelId = "";
      if (source === "public") {
        let status = managedStatus;
        if (!status?.signedIn) {
          const endpoint = managedExtension?.header_status?.refresh_endpoint;
          if (!endpoint) throw new Error("公益模型服务暂时不可用");
          status = await requestManagedModelStatus(endpoint, "POST");
          setManagedStatus(status);
        }
        if (
          !status.serviceEnabled
          || !status.providerId
          || !status.models.includes(selectedPublicModel)
        ) {
          throw new Error("所选公益模型暂时不可用，请重试");
        }
        modelId = `${status.providerId}:${selectedPublicModel}`;
      } else {
        if (!ownedValidated || !selectedModel || !selectedChoice) return;
        const existing = providers[providerId];
        if (existing) {
          await updateModelProvider(providerId, {
            base_url: baseUrl.trim(),
            ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
            models,
          });
        } else {
          await addModelProvider({
            provider_id: providerId,
            provider_type: selectedChoice.providerType,
            name: selectedChoice.name,
            base_url: baseUrl.trim(),
            api_key: apiKey.trim(),
            models,
          });
        }
        modelId = `${providerId}:${selectedModel}`;
      }
      await updateSessionModel(currentMainSessionId, modelId, null);
      onNext();
    } catch (reason) {
      setError(normalizeApiError(reason, "保存模型配置失败"));
    } finally {
      setSaving(false);
    }
  };

  const saveModel = () => {
    if (source === "public" && shouldConfirmManagedModelSelection({
      currentSelectionManaged: currentProviderManaged,
      signedIn: Boolean(managedStatus?.signedIn),
      confirmationAccepted: managedConfirmationAccepted,
    })) {
      setManagedConfirmationOpen(true);
      return;
    }
    void persistModel();
  };

  const loginBusy = loginLoading || Boolean(managedStatus?.loginPending);
  const nextDisabled = saving || (
    source === "public"
      ? !publicReady || !selectedPublicModel
      : !ownedValidated || !selectedModel
  );

  return (
    <section
      className={`first-run-slide first-run-model ${active ? "is-active" : ""}`}
      aria-hidden={!active}
      {...(!active ? ({ inert: "" } as Record<string, string>) : {})}
      aria-labelledby="first-run-model-title"
      style={{ "--first-run-offset": offset } as CSSProperties}
    >
      <div className="first-run-slide__content first-run-model__layout">
        <div className="first-run-section-heading">
          <span>02 / 03</span>
          <h2 id="first-run-model-title">选择 Main 使用的模型</h2>
          <p>选择一个模型即可开始，之后仍可在设置中修改。</p>
        </div>

        <div className="first-run-model-source-switcher" aria-label="模型来源">
          {managedExtension ? (
            <ModelSourceChoice
              active={source === "public"}
              title="使用公益模型"
              description="无需配置，立即体验"
              recommended
              onClick={() => {
                setSource("public");
                setError("");
              }}
            />
          ) : null}
          <ModelSourceChoice
            active={source === "owned"}
            title="使用自有 API"
            description="配置自己的模型服务"
            onClick={() => {
              setSource("owned");
              setError("");
            }}
          />
        </div>

        {source === "public" ? (
          <section className={`first-run-public-model-panel ${managedStatus?.signedIn ? "is-authenticated" : ""}`}>
            <div className="first-run-public-model-hero">
              <span className="first-run-public-model-hero__mark"><Sparkles size={19} /></span>
              <div>
                <h3>{managedStatus?.providerName || "笔枢公益模型"}</h3>
                <p>
                  {managedLoading
                    ? "正在读取服务说明…"
                    : managedStatus?.serviceNotice || "公益模型服务说明暂时无法获取。"}
                </p>
              </div>
              {managedStatus?.loginEnabled ? (
                <button
                  type="button"
                  className={`first-run-public-login ${managedStatus.signedIn ? "is-authenticated" : ""}`}
                  onClick={() => void startLogin()}
                  disabled={loginBusy || managedStatus.signedIn}
                >
                  {loginBusy ? <Loader2 className="first-run-spinner" size={15} /> : <LogIn size={15} />}
                  {managedStatus.signedIn ? "已登录" : loginBusy ? "等待登录" : "登录"}
                </button>
              ) : null}
            </div>

            <div className="first-run-public-model-grid">
              <div className="first-run-field">
                <label htmlFor="first-run-public-main-model">Main 模型</label>
                <select
                  ref={modelSelectRef}
                  id="first-run-public-main-model"
                  value={selectedPublicModel}
                  disabled={managedLoading || publicModels.length === 0}
                  onChange={(event) => setSelectedPublicModel(event.target.value)}
                >
                  {publicModels.map((model) => (
                    <option value={model} key={model}>{model}</option>
                  ))}
                </select>
              </div>
              <div className="first-run-public-model-summary">
                {managedStatus?.signedIn ? (
                  <>
                    <strong>{publicModels.length} 个模型可用</strong>
                    <small>登录成功，模型列表已更新</small>
                  </>
                ) : (
                  <>
                    <strong>匿名用户</strong>
                    <small>{ANONYMOUS_MANAGED_MODEL_NOTE}</small>
                  </>
                )}
              </div>
            </div>
          </section>
        ) : (
          <section className="first-run-owned-model-panel">
            <div className="first-run-owned-model-head">
              <h3>自有 API</h3>
              <label htmlFor="first-run-owned-provider">
                <span>供应商</span>
                <select
                  id="first-run-owned-provider"
                  value={providerId}
                  onChange={(event) => {
                    const choice = standardChoices.find((item) => item.id === event.target.value);
                    if (choice) selectOwnedProvider(choice);
                  }}
                >
                  {standardChoices.map((choice) => (
                    <option value={choice.id} key={choice.id}>{choice.name}</option>
                  ))}
                </select>
              </label>
            </div>

            <form
              className="first-run-owned-model-form"
              onSubmit={(event) => {
                event.preventDefault();
                void validateCredential();
              }}
            >
              <div className="first-run-field">
                <label htmlFor="first-run-api-key">访问密钥</label>
                <div className={`first-run-secret-input ${validationState === "error" ? "has-error" : ""} ${ownedValidated ? "is-valid" : ""}`}>
                  <KeyRound size={17} aria-hidden="true" />
                  <input
                    id="first-run-api-key"
                    type={showApiKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(event) => {
                      setApiKey(event.target.value);
                      setValidationState("idle");
                      setError("");
                    }}
                    autoComplete="off"
                    placeholder={selectedChoice?.configured
                      ? "使用已保存的密钥，或输入新密钥"
                      : `输入 ${selectedChoice?.name || "供应商"} 的访问密钥`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey((current) => !current)}
                    aria-label={showApiKey ? "隐藏访问密钥" : "显示访问密钥"}
                  >
                    {showApiKey ? <EyeOff size={17} /> : <Eye size={17} />}
                  </button>
                </div>
              </div>

              <div className="first-run-api-endpoint-control">
                <button
                  type="button"
                  className="first-run-disclosure"
                  onClick={() => setShowBaseUrl((current) => !current)}
                  aria-expanded={showBaseUrl}
                >
                  API 地址
                  <ChevronDown className={showBaseUrl ? "is-open" : ""} size={15} />
                </button>
                {showBaseUrl ? (
                  <div className="first-run-field first-run-field--technical">
                    <label className="sr-only" htmlFor="first-run-base-url">API 地址</label>
                    <input
                      id="first-run-base-url"
                      value={baseUrl}
                      onChange={(event) => {
                        setBaseUrl(event.target.value);
                        setValidationState("idle");
                        setError("");
                      }}
                    />
                  </div>
                ) : null}
              </div>

              <button type="submit" className="first-run-verify-button" disabled={validationState === "loading"}>
                {validationState === "loading" ? <Loader2 className="first-run-spinner" size={16} /> : ownedValidated ? <Check size={16} /> : <RefreshCw size={16} />}
                {validationState === "loading" ? "正在验证" : ownedValidated ? "连接已验证" : "验证并读取模型"}
              </button>

              <div className="first-run-field">
                <label htmlFor="first-run-owned-main-model">Main 模型</label>
                <select
                  ref={modelSelectRef}
                  id="first-run-owned-main-model"
                  value={selectedModel}
                  disabled={!ownedValidated}
                  onChange={(event) => setSelectedModel(event.target.value)}
                >
                  <option value="">{ownedValidated ? "选择模型" : "验证后选择模型"}</option>
                  {models.map((model) => <option value={model} key={model}>{model}</option>)}
                </select>
              </div>
            </form>
          </section>
        )}

        {error ? <p className="first-run-error" role="alert">{error}</p> : null}

        <div className="first-run-slide-actions">
          <button type="button" className="first-run-secondary-button" onClick={onBack}>
            <ArrowLeft size={16} />上一步
          </button>
          <button
            type="button"
            className="first-run-primary-button"
            disabled={nextDisabled}
            onClick={saveModel}
          >
            {saving ? <Loader2 className="first-run-spinner" size={16} /> : null}
            {saving ? (source === "public" ? "正在准备模型" : "正在保存") : "使用此模型并继续"}
            {!saving ? <ArrowRight size={16} /> : null}
          </button>
        </div>
      </div>
      {managedConfirmationOpen ? createPortal((
        <div
          className="first-run-risk-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setManagedConfirmationOpen(false);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="first-run-public-model-title"
            className="first-run-risk-dialog first-run-public-model-dialog"
          >
            <span className="first-run-risk-dialog__icon"><ShieldAlert size={21} /></span>
            <div>
              <small>服务说明</small>
              <h3 id="first-run-public-model-title">使用公益模型？</h3>
              <p>{managedStatus?.serviceNotice || "公益模型由笔枢提供，确认后将为本机申请使用凭据。"}</p>
            </div>
            <div className="first-run-risk-dialog__actions">
              <button
                type="button"
                className="first-run-secondary-button"
                onClick={() => setManagedConfirmationOpen(false)}
              >
                取消
              </button>
              <button
                ref={managedConfirmRef}
                type="button"
                className="first-run-risk-confirm"
                onClick={() => {
                  setManagedConfirmationAccepted(true);
                  setManagedConfirmationOpen(false);
                  void persistModel();
                }}
              >
                我已了解，使用公益模型
              </button>
            </div>
          </div>
        </div>
      ), document.body) : null}
    </section>
  );
}
