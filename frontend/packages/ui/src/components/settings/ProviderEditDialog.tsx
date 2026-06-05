import { useState, useEffect, type FC } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { ProviderFormFields, type TestStatus } from "./ProviderFormFields";
import { useI18n } from "../../hooks/use-i18n";

export interface ProviderEditDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  providerId: string;
  providerName: string;
  providerKind: string;
  providerDisplayName: string;
  currentBaseUrl: string;
  currentProtocol: string | null;
  /** Models the channel already knows about — seeds the read-only
   *  "可用模型" list on open so users don't need to re-probe to look.
   *  Cleared when the user edits the API key (stale once the
   *  credential changes). */
  initialModels: string[];
  supportsCustomBaseUrl: boolean;
  supportsProtocolSelection: boolean;
  docsUrl: string;
  defaultBaseUrl: string;
  /**
   * When ``"oauth"``, the dialog hides the API Key / Endpoint /
   * connection-test affordances — those don't apply to subscription
   * providers (credentials live in the CLI's keychain).
   */
  authType?: "api_key" | "oauth";
  onSave: (
    providerId: string,
    payload: {
      base_url?: string;
      api_key?: string;
      protocol?: string;
      models?: string[];
      name?: string;
    },
  ) => Promise<void>;
  /** Triggered by [连接测试] — built-in channels probe ``/v1/models``
   *  to refresh the read-only preview. Returns the discovered ids. */
  onDiscoverModels: (providerId: string) => Promise<{ models: string[] }>;
  /** Triggered by [连接测试] for custom (compatible) channels — pings
   *  every model id and returns the verified subset. ``api_key`` may
   *  be empty when ``provider_id`` is supplied (edit-mode reuses the
   *  stored key from secret_store). */
  onPing?: (payload: {
    base_url: string;
    api_key?: string;
    protocol?: string;
    models: string[];
    provider_id?: string;
  }) => Promise<{
    ok: string[];
    failed: { model: string; reason: string }[];
  }>;
}

export const ProviderEditDialog: FC<ProviderEditDialogProps> = ({
  open,
  onOpenChange,
  providerId,
  providerName,
  providerKind,
  providerDisplayName,
  currentBaseUrl,
  currentProtocol,
  initialModels,
  docsUrl,
  defaultBaseUrl,
  authType = "api_key",
  onSave,
  onDiscoverModels,
  onPing,
}) => {
  const { t } = useI18n();
  const isOAuthProvider = authType === "oauth";
  const [baseUrl, setBaseUrl] = useState(currentBaseUrl);
  const [apiKey, setApiKey] = useState("");
  const [protocol, setProtocol] = useState(
    currentProtocol ?? "openai-completion",
  );
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMsg, setTestMsg] = useState("");
  const [saving, setSaving] = useState(false);
  // Seeded from ``initialModels`` so the read-only "可用模型" list is
  // populated on dialog open — the discover-models result is already
  // persisted on the row, no need to re-probe just to look.
  const [discoveredModels, setDiscoveredModels] = useState<string[] | null>(
    initialModels.length > 0 ? initialModels : null,
  );
  // Editable models text for the custom (compatible) channel. Seeded
  // from the stored ``initialModels`` on open so the user sees what
  // they had before, and can add / remove freely.
  const [modelsText, setModelsText] = useState(initialModels.join("\n"));
  // Editable display name — only the custom channel exposes this;
  // built-in rows reuse the descriptor's display name and don't let
  // users rename (would just be cosmetic drift). Seeded from the row.
  const [customName, setCustomName] = useState(providerName);

  const parseModels = (raw: string): string[] =>
    raw
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

  // Protocol + Endpoint are *creation-time* choices: they decide which
  // upstream the channel calls and aren't meaningful to edit later
  // (changing protocol would effectively switch to a different provider).
  // For built-in providers (DeepSeek, GLM, Moonshot, MiniMax, ...) we hide
  // both so the edit dialog stays focused on rotating the API key. Only
  // the "compatible" custom channel exposes them on edit, because that's
  // the one place users can legitimately point the channel elsewhere.
  const isCustom = providerKind === "compatible";
  const showProtocol = isCustom;
  const showEndpoint = isCustom && !isOAuthProvider;

  useEffect(() => {
    if (open) {
      setBaseUrl(currentBaseUrl);
      setProtocol(currentProtocol ?? "openai-completion");
      setApiKey("");
      setTestStatus("idle");
      setTestMsg("");
      setSaving(false);
      setDiscoveredModels(initialModels.length > 0 ? initialModels : null);
      setModelsText(initialModels.join("\n"));
      setCustomName(providerName);
    }
  }, [open, currentBaseUrl, currentProtocol, initialModels, providerName]);

  useEffect(() => {
    if (!open || !showProtocol || !defaultBaseUrl) return;
    const base = defaultBaseUrl.replace(/\/+$/, "");
    setBaseUrl(
      protocol === "anthropic" || protocol === "gemini"
        ? `${base}/anthropic`
        : base,
    );
    setTestStatus("idle");
  }, [protocol, open, showProtocol, defaultBaseUrl]);

  const handleTest = async () => {
    setTestStatus("testing");
    setTestMsg("");
    try {
      if (isCustom) {
        const parsedModels = parseModels(modelsText);
        if (parsedModels.length === 0) {
          throw new Error("请至少填写 1 个模型 id");
        }
        if (!baseUrl) {
          throw new Error("请填写 Endpoint");
        }
        if (!onPing) {
          throw new Error("ping handler missing");
        }
        // Edit mode: user often opens the dialog just to add a new
        // model id and doesn't touch the API Key field — we fall back
        // to the server's stored key by passing ``provider_id``. Add
        // mode (no provider_id) still requires the user to type one.
        const batch = await onPing({
          base_url: baseUrl,
          api_key: apiKey || undefined,
          protocol,
          models: parsedModels,
          provider_id: providerId,
        });
        if (batch.ok.length > 0) {
          setModelsText(batch.ok.join("\n"));
        }
        setDiscoveredModels(batch.ok.length > 0 ? batch.ok : null);
        if (batch.failed.length === 0) {
          setTestStatus("ok");
          setTestMsg(
            t("settings.model.connectSuccessDiscovered", {
              count: String(batch.ok.length),
            }),
          );
        } else if (batch.ok.length === 0) {
          const summary = batch.failed
            .map((f) => `${f.model}: ${f.reason}`)
            .join("；");
          throw new Error(`所有模型测试均失败：${summary}`);
        } else {
          const summary = batch.failed
            .map((f) => `${f.model}: ${f.reason}`)
            .join("；");
          setTestStatus("fail");
          setTestMsg(
            `${batch.ok.length} 个通过，${batch.failed.length} 个失败：${summary}`,
          );
        }
        return;
      }
      const result = await onDiscoverModels(providerId);
      setDiscoveredModels(result.models);
      setTestStatus("ok");
      setTestMsg(
        t("settings.model.connectSuccessDiscovered", {
          count: String(result.models.length),
        }),
      );
    } catch (err) {
      setTestStatus("fail");
      setTestMsg(
        err instanceof Error ? err.message : t("ui.providerEdit.connectFailed"),
      );
    }
  };

  const handleSave = async () => {
    if (isCustom) {
      const parsedModels = parseModels(modelsText);
      if (parsedModels.length === 0) {
        setTestStatus("fail");
        setTestMsg("请至少填写 1 个模型 id");
        return;
      }
      if (!customName.trim()) {
        setTestStatus("fail");
        setTestMsg("请填写服务商名称");
        return;
      }
    }
    setSaving(true);
    try {
      // Only send ``name`` for custom channels — built-ins reuse the
      // descriptor's display name and don't expose the edit affordance.
      const trimmedName = customName.trim();
      const nameChanged =
        isCustom && trimmedName && trimmedName !== providerName;
      await onSave(providerId, {
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
        protocol: showProtocol ? protocol : undefined,
        models: isCustom ? parseModels(modelsText) : undefined,
        name: nameChanged ? trimmedName : undefined,
      });
      onOpenChange(false);
    } catch (err) {
      setTestStatus("fail");
      setTestMsg(
        err instanceof Error ? err.message : t("ui.providerEdit.saveFailed"),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("ui.providerEdit.title")}</DialogTitle>
          <DialogDescription>
            {t("ui.providerEdit.description", { name: providerName })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-label">
              {t("ui.providerEdit.providerLabel")}
            </label>
            <input
              type="text"
              value={providerDisplayName}
              readOnly
              className="w-full rounded-lg border border-surface-border bg-surface-soft px-3 py-2 text-sm text-ink-meta"
            />
          </div>

          {/* Editable display name — compatible (custom) channel only.
              Built-in rows reuse the descriptor name and don't expose
              this affordance (would just create cosmetic drift). */}
          {isCustom && (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-label">
                名称
              </label>
              <input
                type="text"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="例：xiaomimo 中转、内部 GPT-4 代理"
                className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm focus:border-brand/30 focus:outline-none"
              />
            </div>
          )}

          <ProviderFormFields
            protocol={protocol}
            onProtocolChange={setProtocol}
            showProtocol={showProtocol}
            docsUrl={docsUrl}
            apiKey={apiKey}
            onApiKeyChange={(v) => {
              setApiKey(v);
              setTestStatus("idle");
              setDiscoveredModels(null);
            }}
            showApiKey={!isOAuthProvider}
            apiKeyPlaceholder={t("ui.providerEdit.apiKeyPlaceholder")}
            baseUrl={baseUrl}
            onBaseUrlChange={(v) => {
              setBaseUrl(v);
              setTestStatus("idle");
              setDiscoveredModels(null);
            }}
            showEndpoint={showEndpoint}
            testStatus={testStatus}
            testMsg={testMsg}
            discoveredModels={isCustom ? null : discoveredModels}
            showModelsTextarea={isCustom}
            modelsText={modelsText}
            onModelsTextChange={(v) => {
              setModelsText(v);
              setTestStatus("idle");
              setDiscoveredModels(null);
            }}
          />
        </div>

        <DialogFooter>
          {!isOAuthProvider && (
            <Button
              variant="outline"
              onClick={() => {
                void handleTest();
              }}
              disabled={testStatus === "testing"}
            >
              {testStatus === "testing"
                ? t("ui.providerEdit.testing")
                : t("ui.providerEdit.testButton")}
            </Button>
          )}
          <Button
            onClick={() => {
              void handleSave();
            }}
            disabled={saving}
          >
            {saving
              ? t("ui.providerEdit.saving")
              : t("ui.providerEdit.saveButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
