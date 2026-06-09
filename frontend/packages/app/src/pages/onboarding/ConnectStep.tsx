import { useEffect, useState } from "react";
import {
  Badge,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@valuz/ui";
import {
  providersApi,
  runtimesApi,
  settingsApi,
  isProviderRuntimeCompatible,
  RUNTIME_DISPLAY_NAME,
  type ModelDefaults,
  type ProviderListItem,
  type RuntimeId,
  type RuntimeListItem,
} from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import { Check, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { OnboardingStep } from "./OnboardingShell";
import { StepFooter } from "./StepFooter";

/**
 * Step 2 · Connect a model channel — RUNTIME-FIRST. One card per available
 * runtime (Claude Code / OpenAI Codex / Deep Agents). Each card's model picker
 * aggregates every model the user is allowed to use on that runtime, drawn from
 * all protocol-compatible providers — system (Valuz 系统模型), org models, and
 * (when the platform allows member-configured models) the member's own
 * channels. Personal channels are already filtered out of ``providersApi.list``
 * server-side when ``member_custom_model_enabled`` is off, so this component
 * just renders whatever it's given. Picking a model sets the global default
 * (runtime + provider + model) via the same path Settings → Models uses.
 */

type ModelOption = {
  key: string; // `${providerId}::${modelId}`
  providerId: string;
  providerName: string;
  modelId: string;
  // Human label from ``provider.model_labels[modelId]`` when set; falls back
  // to ``modelId``. Picker display only — ``modelId`` still rides the wire.
  modelLabel: string;
  source: string; // "system" | "builtin" | "user"
};

type RuntimeCard = {
  runtimeId: RuntimeId;
  runtimeName: string;
  options: ModelOption[];
};

const keyOf = (providerId: string, modelId: string) =>
  `${providerId}::${modelId}`;

export const ConnectStep = ({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}) => {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<ProviderListItem[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeListItem[]>([]);
  const [defaults, setDefaults] = useState<ModelDefaults | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    const [list, md] = await Promise.all([
      providersApi.list(),
      settingsApi.getModelDefaults(),
    ]);
    setProviders(list.providers);
    setDefaults(md);
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [, rt] = await Promise.all([refresh(), runtimesApi.list()]);
        if (!cancelled) setRuntimes(rt.runtimes);
      } catch {
        /* surfaced as empty cards — reload to retry */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const defaultRuntime = defaults?.default_runtime ?? null;
  const defaultProviderId = defaults?.default_provider_id ?? null;
  const defaultModel = defaults?.default_model ?? null;
  const hasDefault = Boolean(
    defaultRuntime && defaultProviderId && defaultModel,
  );
  // Footer status line should read the human label, not the wire-level id —
  // overlay system providers expose ``model_labels[id]`` mapping; api_key
  // providers' map is empty, so id stays as-is.
  const defaultModelLabel = (() => {
    if (!defaultModel) return null;
    const provider = providers.find((p) => p.id === defaultProviderId);
    return provider?.model_labels?.[defaultModel] || defaultModel;
  })();

  // Build one card per available runtime, aggregating models from every
  // protocol-compatible, usable provider. A provider is usable when it's
  // enabled and either carries a credential, is an OAuth subscription, or is a
  // system-managed channel (credentials live server-side).
  const cards: RuntimeCard[] = runtimes
    .filter((rt) => rt.available)
    .map((rt) => {
      const options: ModelOption[] = [];
      for (const p of providers) {
        const usable =
          p.enabled &&
          (p.credential_source !== "none" ||
            p.auth_type === "oauth" ||
            p.source === "system");
        if (!usable || !isProviderRuntimeCompatible(p, rt.id)) continue;
        const models =
          p.model_options.length > 0
            ? p.model_options
            : p.default_model
              ? [p.default_model]
              : [];
        for (const m of models) {
          options.push({
            key: keyOf(p.id, m),
            providerId: p.id,
            providerName: p.name,
            modelId: m,
            modelLabel: p.model_labels?.[m] || m,
            source: p.source,
          });
        }
      }
      return {
        runtimeId: rt.id,
        runtimeName: RUNTIME_DISPLAY_NAME[rt.id] ?? rt.display_name ?? rt.id,
        options,
      };
    })
    .filter((c) => c.options.length > 0);

  const setAsDefault = async (
    runtimeId: RuntimeId,
    providerId: string,
    model: string,
  ) => {
    setBusy(runtimeId);
    try {
      const next = await settingsApi.patchModelDefaults({
        default_runtime: runtimeId,
        default_provider_id: providerId,
        default_model: model,
      });
      setDefaults(next);
      await refresh();
    } catch {
      toast.error(
        _t("onboarding.setDefaultFailed" as Parameters<typeof _t>[0]),
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <OnboardingStep
      title={t("onboarding.connectTitle" as Parameters<typeof t>[0])}
      subtitle={t("onboarding.connectSubtitle" as Parameters<typeof t>[0])}
      footer={
        <StepFooter
          status={
            hasDefault
              ? t(
                  "onboarding.connectStatusHasDefault" as Parameters<
                    typeof t
                  >[0],
                  { model: defaultModelLabel ?? "" },
                )
              : t(
                  "onboarding.connectStatusNoDefault" as Parameters<
                    typeof t
                  >[0],
                )
          }
          onSkip={onSkip}
          skipLabel={t("onboarding.skip" as Parameters<typeof t>[0])}
          primaryLabel={t("onboarding.continue" as Parameters<typeof t>[0])}
          onPrimary={onContinue}
          primaryDisabled={!hasDefault}
        />
      }
    >
      {loading ? (
        <div className="flex items-center gap-3 rounded-xl border border-surface-border bg-surface-soft/60 px-4 py-4 text-sm text-ink-meta">
          <Loader2 className="h-4 w-4 animate-spin text-brand" />
          {t("onboarding.connectLoading" as Parameters<typeof t>[0])}
        </div>
      ) : (
        <div className="space-y-2.5">
          {cards.map((card) => {
            const selected = defaultRuntime === card.runtimeId;
            const selectedKey =
              selected && defaultProviderId
                ? keyOf(defaultProviderId, defaultModel ?? "")
                : "";
            const inThisCard = card.options.some((o) => o.key === selectedKey);
            return (
              <RuntimeCardRow
                key={card.runtimeId}
                name={card.runtimeName}
                options={card.options}
                value={inThisCard ? selectedKey : ""}
                isDefault={selected && inThisCard}
                busy={busy === card.runtimeId}
                onPick={(key) => {
                  const opt = card.options.find((o) => o.key === key);
                  if (opt)
                    void setAsDefault(
                      card.runtimeId,
                      opt.providerId,
                      opt.modelId,
                    );
                }}
              />
            );
          })}
        </div>
      )}
    </OnboardingStep>
  );
};

/* -------------------------------------------------------------------------- */

const RuntimeCardRow = ({
  name,
  options,
  value,
  isDefault,
  busy,
  onPick,
}: {
  name: string;
  options: ModelOption[];
  value: string;
  isDefault: boolean;
  busy: boolean;
  onPick: (key: string) => void;
}) => {
  const { t } = useTranslation();
  return (
    <div
      className={`rounded-xl border transition-all ${
        isDefault
          ? "border-brand/60 bg-brand-light/20 ring-1 ring-brand/40"
          : "border-surface-border bg-surface"
      }`}
    >
      <div className="flex items-center gap-3.5 px-4 py-3.5">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[13px] font-bold ${
            isDefault
              ? "bg-brand/10 text-brand"
              : "bg-surface-soft text-ink-meta"
          }`}
        >
          {name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-sm font-medium text-ink-heading">{name}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin text-brand" />}
          {isDefault && (
            <Badge variant="success" className="gap-1 text-2xs">
              <Check className="h-2.5 w-2.5" />
              {t("onboarding.defaultBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
        </div>
      </div>
      <div className="border-t border-surface-border/70 px-4 pb-4 pt-3">
        <Select value={value} onValueChange={onPick} disabled={busy}>
          <SelectTrigger className="w-full font-mono text-xs">
            <SelectValue
              placeholder={t(
                "onboarding.pickModelHint" as Parameters<typeof t>[0],
              )}
            />
          </SelectTrigger>
          <SelectContent>
            {options.map((o) => (
              <SelectItem
                key={o.key}
                value={o.key}
                className="font-mono text-xs"
              >
                {o.providerName} · {o.modelLabel}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
};
