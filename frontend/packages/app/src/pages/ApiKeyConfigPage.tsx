import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  Button,
  ProviderFormFields,
  type TestStatus,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@valuz/ui";
import { providersApi, type ProviderDescriptor } from "@valuz/core";
import { useTranslation } from "@valuz/core";

export const ApiKeyConfigPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [providerKind, setProviderKind] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [protocol, setProtocol] = useState("openai-completion");
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMsg, setTestMsg] = useState("");
  const [discoveredModels, setDiscoveredModels] = useState<string[] | null>(
    null,
  );
  const [urlProvider, setUrlProvider] = useState<string | null>(null);

  const isCustom = providerKind === "compatible";

  const selectableProviders = useMemo(() => {
    const displayNames: Record<string, string> = {
      anthropic: "Anthropic (Claude)",
      openai: "OpenAI (ChatGPT)",
      deepseek: "DeepSeek",
      openrouter: "OpenRouter",
      moonshot: "Moonshot (Kimi)",
      minimax: "MiniMax",
      compatible: t("common.custom" as Parameters<typeof t>[0]),
    };
    return providers
      .filter((p) => p.auth_type !== "oauth")
      .map((p) => ({
        ...p,
        display_name: displayNames[p.kind] ?? p.display_name,
      }));
  }, [providers]);

  const provider = useMemo(
    () => selectableProviders.find((p) => p.kind === providerKind),
    [selectableProviders, providerKind],
  );

  // Built-in providers (DeepSeek, GLM, Moonshot, MiniMax, ...) ship with a
  // canonical protocol + endpoint baked into the descriptor. Exposing the
  // picker on the first-launch form forces users into a decision they
  // shouldn't need to make and lets them land on combinations (e.g.
  // DeepSeek + Anthropic) where ``/v1/models`` doesn't exist and the test
  // fails with "可用模型数量等于 0". The "compatible" channel is the one
  // place where the user is genuinely pointing valuz at an unknown
  // upstream and must pick both. Matches ProviderAddDialog so the
  // first-launch form and the settings form stay shape-consistent.
  const protocolEditable = isCustom;
  const showEndpoint = isCustom;

  const loadProviders = useCallback(async () => {
    try {
      const res = await providersApi.listProviders();
      setProviders(res.providers);
    } catch {
      // Backend unreachable
    }
  }, []);

  useEffect(() => {
    const p = searchParams.get("provider");
    if (p) setUrlProvider(p);
    void loadProviders();
  }, [searchParams, loadProviders]);

  useEffect(() => {
    if (!urlProvider) return;
    const desc = selectableProviders.find((d) => d.kind === urlProvider);
    if (desc) setProviderKind(desc.kind);
    setUrlProvider(null);
  }, [urlProvider, selectableProviders]);

  useEffect(() => {
    if (provider) {
      setBaseUrl(provider.default_base_url);
      setProtocol(
        provider.kind === "anthropic" ? "anthropic" : "openai-completion",
      );
    } else {
      setBaseUrl("");
      setProtocol("openai-completion");
    }
    setTestStatus("idle");
    setTestMsg("");
    setDiscoveredModels(null);
  }, [provider]);

  // No protocol-driven baseUrl flip needed: built-in providers no longer
  // expose the protocol picker (baseUrl stays at provider.default_base_url),
  // and the compatible channel has no default_base_url to flip between.

  const handleTest = async () => {
    if (!providerKind || !apiKey.trim()) return;
    setTestStatus("testing");
    setTestMsg("");
    try {
      const result = await providersApi.probeModels({
        provider_kind: providerKind,
        api_key: apiKey,
        base_url: baseUrl || undefined,
        protocol: protocolEditable ? protocol : undefined,
      });
      setDiscoveredModels(result.models);
      setTestStatus("ok");
      setTestMsg(
        `${t("common.success" as Parameters<typeof t>[0])} · ${result.models.length}`,
      );
    } catch (err) {
      setTestStatus("fail");
      setTestMsg(
        err instanceof Error
          ? err.message
          : t("common.error" as Parameters<typeof t>[0]),
      );
    }
  };

  const handleSave = async () => {
    if (!providerKind || !apiKey.trim()) return;
    if (testStatus === "idle") {
      if (!window.confirm(t("common.confirm" as Parameters<typeof t>[0])))
        return;
    }
    try {
      const name = provider?.display_name ?? providerKind;
      await providersApi.create({
        name,
        provider_kind: providerKind,
        api_key: apiKey,
        base_url: baseUrl || undefined,
        protocol: protocolEditable ? protocol : undefined,
      });
      navigate("/onboarding");
    } catch (err) {
      setTestStatus("fail");
      setTestMsg(
        err instanceof Error
          ? err.message
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background">
      <div className="w-full max-w-[420px] px-6">
        <button
          type="button"
          onClick={() => navigate("/welcome")}
          className="mb-6 flex items-center gap-1 text-xs text-ink-meta transition-colors hover:text-ink-heading"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("common.back" as Parameters<typeof t>[0])}
        </button>

        <h1 className="mb-1 text-lg font-semibold text-ink-heading">
          {t("onboarding.configApiKey" as Parameters<typeof t>[0])}
        </h1>
        <p className="mb-6 text-sm text-ink-body">
          {t("onboarding.configApiKeyDesc" as Parameters<typeof t>[0])}
        </p>

        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-label">
              {t("onboarding.providerLabel" as Parameters<typeof t>[0])}
            </label>
            <Select value={providerKind} onValueChange={setProviderKind}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={t(
                    "ui.providerForm.modelPlaceholder" as Parameters<
                      typeof t
                    >[0],
                  )}
                />
              </SelectTrigger>
              <SelectContent>
                {selectableProviders.map((p) => (
                  <SelectItem key={p.kind} value={p.kind}>
                    {p.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <ProviderFormFields
            protocol={protocol}
            onProtocolChange={setProtocol}
            showProtocol={protocolEditable}
            docsUrl={provider?.docs_url}
            apiKey={apiKey}
            onApiKeyChange={(v) => {
              setApiKey(v);
              setTestStatus("idle");
              setDiscoveredModels(null);
            }}
            baseUrl={baseUrl}
            onBaseUrlChange={(v) => {
              setBaseUrl(v);
              setTestStatus("idle");
              setDiscoveredModels(null);
            }}
            showEndpoint={showEndpoint}
            endpointReadOnly={!isCustom}
            testStatus={testStatus}
            testMsg={testMsg}
            discoveredModels={discoveredModels}
          />
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void handleTest();
            }}
            disabled={
              !providerKind || !apiKey.trim() || testStatus === "testing"
            }
          >
            {testStatus === "testing"
              ? t("ui.providerForm.testing" as Parameters<typeof t>[0])
              : t("common.test" as Parameters<typeof t>[0])}
          </Button>
          <div className="flex-1" />
          <Button
            size="sm"
            onClick={() => {
              void handleSave();
            }}
            disabled={!providerKind || !apiKey.trim()}
          >
            {t("common.save" as Parameters<typeof t>[0])}
          </Button>
        </div>
      </div>
    </div>
  );
};
