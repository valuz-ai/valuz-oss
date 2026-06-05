import { useState, useEffect, useMemo, type FC } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { ProviderFormFields, type TestStatus } from "./ProviderFormFields";
import { useI18n } from "../../hooks/use-i18n";

export interface ProviderOption {
  kind: string;
  display_name: string;
  supports_custom_base_url: boolean;
  supports_protocol_selection: boolean;
  default_base_url: string;
  anthropic_base_url?: string;
  default_model: string;
  model_options: string[];
  docs_url: string;
}

export interface ProviderAddDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  providers: ProviderOption[];
  /** Stateless probe of ``<base>/v1/models`` — built-in channels only.
   *  Verifies the api_key and returns the upstream's real model list,
   *  which the dialog surfaces as the read-only "可用模型" preview. */
  onProbeModels?: (payload: {
    provider_kind: string;
    api_key: string;
    base_url?: string;
    protocol?: string;
  }) => Promise<{
    models: string[];
    suggested_default: string | null;
  }>;
  /** Stateless chat-ping for custom (compatible) channels — pings every
   *  model id in the textarea and returns the verified subset. The
   *  dialog surfaces per-model failures so the user can prune bad ids
   *  before save. */
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
  onCreate: (payload: {
    name: string;
    provider_kind: string;
    api_key?: string;
    base_url?: string;
    protocol?: string;
    models?: string[];
  }) => Promise<void>;
}

export const ProviderAddDialog: FC<ProviderAddDialogProps> = ({
  open,
  onOpenChange,
  providers,
  onProbeModels,
  onPing,
  onCreate,
}) => {
  const { t } = useI18n();
  const [providerKind, setProviderKind] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [protocol, setProtocol] = useState("openai-completion");
  const [modelsText, setModelsText] = useState("");
  // Custom (compatible) channels need a user-supplied display name —
  // built-ins reuse the descriptor's ``display_name`` so the picker
  // shows "DeepSeek", "智谱 (GLM)" etc. without the user having to type.
  const [customName, setCustomName] = useState("");
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMsg, setTestMsg] = useState("");
  const [saving, setSaving] = useState(false);
  // Populated by [连接测试] when probe-models succeeds — rendered as
  // a read-only chip list so the user sees which models the channel
  // unlocks. Cleared on key / endpoint edit.
  const [discoveredModels, setDiscoveredModels] = useState<string[] | null>(
    null,
  );

  const parseModels = (raw: string): string[] =>
    raw
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

  const provider = useMemo(
    () => providers.find((p) => p.kind === providerKind),
    [providers, providerKind],
  );

  // Built-in providers (DeepSeek, GLM, Moonshot, MiniMax, ...) ship with a
  // canonical protocol + endpoint baked into the descriptor — exposing the
  // picker on the add path forces users to make a decision they shouldn't
  // need to. The "compatible" custom channel is the one place where the
  // user is genuinely pointing valuz at an unknown upstream and must pick
  // both. Anyone who needs a non-default protocol against a built-in
  // upstream can create a compatible channel instead.
  const isCustom = providerKind === "compatible";
  const showProtocol = isCustom;
  const showEndpoint = isCustom;

  useEffect(() => {
    if (!open) {
      setProviderKind("");
      setApiKey("");
      setBaseUrl("");
      setProtocol("openai-completion");
      setModelsText("");
      setCustomName("");
      setTestStatus("idle");
      setTestMsg("");
      setSaving(false);
      setDiscoveredModels(null);
    }
  }, [open]);

  // Reset form ONLY when the picked provider kind actually changes. Depending
  // on the memoized ``provider`` object would wipe the test result every time
  // the parent re-renders (parent rebuilds the ``providers`` array each render,
  // ``useMemo(providers.find...)`` then yields a new identity → this effect
  // fired → testStatus / discoveredModels reset). Use providerKind as the
  // stable signal and look the descriptor up inline.
  useEffect(() => {
    const p = providers.find((x) => x.kind === providerKind);
    if (p) {
      setBaseUrl(p.default_base_url);
      setProtocol(p.kind === "anthropic" ? "anthropic" : "openai-completion");
    } else {
      setBaseUrl("");
      setProtocol("openai-completion");
    }
    setTestStatus("idle");
    setTestMsg("");
    setDiscoveredModels(null);
    // ``providers`` is intentionally excluded — its identity flips on every
    // parent render and would re-trigger the reset; reading it inside the
    // effect via ``find`` is sufficient because providerKind is the only
    // input that legitimately invalidates the form state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerKind]);

  // No protocol-driven baseUrl flip needed: built-in providers no longer
  // expose the protocol picker (baseUrl stays at provider.default_base_url),
  // and the compatible channel has no default_base_url to flip between.

  const handleTest = async () => {
    if (!providerKind || !apiKey) return;
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
        const batch = await onPing({
          base_url: baseUrl,
          api_key: apiKey,
          protocol,
          models: parsedModels,
        });
        // Rewrite the textarea to the verified subset so save persists
        // only the working ids. Failures show in the test-result line.
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
          // Partial: surface the failures inline. The save flow runs
          // the batch again server-side, so dropped ids never persist.
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
      if (!onProbeModels) return;
      const result = await onProbeModels({
        provider_kind: providerKind,
        api_key: apiKey,
        base_url: baseUrl || undefined,
        protocol: showProtocol ? protocol : undefined,
      });
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
        err instanceof Error ? err.message : t("ui.providerAdd.connectFailed"),
      );
    }
  };

  const handleSave = async () => {
    if (!providerKind) return;
    if (isCustom) {
      const parsedModels = parseModels(modelsText);
      if (parsedModels.length === 0) {
        setTestStatus("fail");
        setTestMsg("请至少填写 1 个模型 id");
        return;
      }
    } else if (onProbeModels && apiKey && testStatus !== "ok") {
      await handleTest();
      if (testStatus === "fail") return;
    }
    setSaving(true);
    try {
      // Custom (compatible) channels get the user-supplied display
      // name so the picker shows something more useful than the
      // generic "自定义"; built-ins reuse the descriptor's display name.
      const name = isCustom
        ? customName.trim() || provider?.display_name || providerKind
        : (provider?.display_name ?? providerKind);
      await onCreate({
        name,
        provider_kind: providerKind,
        api_key: apiKey || undefined,
        base_url: baseUrl || undefined,
        protocol: showProtocol ? protocol : undefined,
        models: isCustom ? parseModels(modelsText) : undefined,
      });
      onOpenChange(false);
    } catch (err) {
      setTestStatus("fail");
      setTestMsg(
        err instanceof Error ? err.message : t("ui.providerAdd.saveFailed"),
      );
    } finally {
      setSaving(false);
    }
  };

  const canSave =
    providerKind &&
    (apiKey || isCustom) &&
    (!isCustom ||
      (parseModels(modelsText).length > 0 && customName.trim().length > 0));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("ui.providerAdd.title")}</DialogTitle>
          <DialogDescription>
            {t("ui.providerAdd.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Provider selector */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-label">
              {t("ui.providerAdd.providerLabel")}
            </label>
            <Select value={providerKind} onValueChange={setProviderKind}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={t("ui.providerAdd.providerPlaceholder")}
                />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.kind} value={p.kind}>
                    {p.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Display-name input — only for the compatible (custom)
              channel. Without it the picker list shows every custom
              row as "自定义", which is useless once the user has more
              than one. Built-ins reuse the descriptor name. */}
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
            docsUrl={provider?.docs_url}
            apiKey={apiKey}
            onApiKeyChange={(v) => {
              setApiKey(v);
              setTestStatus("idle");
              setTestMsg("");
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
            discoveredModels={isCustom ? null : discoveredModels}
            showModelsTextarea={isCustom}
            modelsText={modelsText}
            onModelsTextChange={(v) => {
              setModelsText(v);
              setTestStatus("idle");
              setTestMsg("");
              setDiscoveredModels(null);
            }}
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleTest()}
            disabled={!providerKind || !apiKey || testStatus === "testing"}
          >
            {testStatus === "testing"
              ? t("ui.providerAdd.testing")
              : t("ui.providerAdd.testButton")}
          </Button>
          <Button
            onClick={() => {
              void handleSave();
            }}
            disabled={!canSave || saving}
          >
            {saving
              ? t("ui.providerAdd.saving")
              : t("ui.providerAdd.saveButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
