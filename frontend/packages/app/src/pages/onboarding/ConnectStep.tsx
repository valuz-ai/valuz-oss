import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  Badge,
  Button,
  ProviderAddDialog,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  ModelSelectionHint,
  type ProviderOption,
} from "@valuz/ui";
import {
  providersApi,
  settingsApi,
  type ModelOption,
  type ModelOptionGroup,
  type ModelOptionProvider,
  type ModelOptionsResponse,
  type ProviderDescriptor,
} from "@valuz/core";
import { useCapabilities, useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import { modelLabel } from "@valuz/shared";
import { Check, Loader2, Lock, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  useCliLoginFlow,
  type CliLoginStatus,
  type CliTool,
} from "../../components";
import { usePlatform } from "../../platform";
import { OnboardingStep } from "./OnboardingShell";
import { StepFooter } from "./StepFooter";

/**
 * Step 2 · Connect a model channel.
 *
 * Renders the server's already-resolved picker (`GET /v1/settings/model-options`)
 * verbatim — the backend groups providers, resolves each model's runtimes,
 * collapses a system channel to one card, and disambiguates same-named models.
 * Picking a model writes the global default via `settingsApi.patchModelDefaults`
 * using the model's own `(default_runtime, provider_id, model_id)` — the EXACT
 * same default mechanism Settings → Models uses, no client-side runtime guessing.
 *
 * The one thing the server can't resolve is CLI-subscription login state (the
 * credential lives in the local keychain): those providers come back
 * `status="client_resolved"` and we fill availability in from `checkCliLogin`.
 */

const API_KEY_DISPLAY: Record<string, string> = {
  anthropic: "Anthropic (Claude)",
  openai: "OpenAI (ChatGPT)",
  deepseek: "DeepSeek",
  openrouter: "OpenRouter",
  moonshot: "Moonshot (Kimi)",
  minimax: "MiniMax",
};

const DEFAULT_BADGE_CLASS =
  "h-4 rounded-[4px] px-1 py-0 text-[10px] leading-none font-normal";
const AVAILABLE_BADGE_CLASS = "rounded-full text-2xs";

// group key → section header i18n key. api_key is handled separately (it always
// shows the "add channel" affordance even with no configured providers).
const SECTION_KEY: Record<string, string> = {
  subscription: "onboarding.sectionLocalSubscription",
  system: "onboarding.sectionSystem",
  org: "onboarding.sectionSystem",
};

const EMPTY_CURRENT: ModelOptionsResponse["current"] = {
  runtime: null,
  provider_id: null,
  model: null,
};

export const ConnectStep = ({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}) => {
  const { t } = useTranslation();
  // When an org policy disallows members configuring their own model channels
  // (member_custom_model_enabled=false → configureModelChannel=false), the whole
  // "API Key channel" affordance is hidden — members pick from the platform /
  // subscription channels only, with nothing to add.
  const { configureModelChannel } = useCapabilities();
  const { checkCliLogin } = usePlatform();
  const [groups, setGroups] = useState<ModelOptionGroup[]>([]);
  const [current, setCurrent] =
    useState<ModelOptionsResponse["current"]>(EMPTY_CURRENT);
  const [descriptors, setDescriptors] = useState<ProviderDescriptor[]>([]);
  const [cliStatus, setCliStatus] = useState<
    Partial<Record<CliTool, CliLoginStatus>>
  >({});
  const [loading, setLoading] = useState(true);
  // The model id currently being set as default (spinner on its card).
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  // Which channel the user has opened locally. The backend default remains the
  // source of truth; this only controls the inline model picker expansion.
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );

  const refresh = async () => {
    const res = await settingsApi.getModelOptions();
    setGroups(res.groups);
    setCurrent(res.current);
  };

  const cliLogin = useCliLoginFlow({
    onProviderMarkedOAuth: () => void refresh(),
  });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [, desc] = await Promise.all([
          refresh(),
          providersApi.listProviders(),
        ]);
        if (!cancelled) setDescriptors(desc.providers);
      } catch {
        /* surfaced as an empty list — the user can retry by reloading */
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (checkCliLogin) {
        try {
          const [claude, codex] = (await Promise.all([
            checkCliLogin("claude"),
            checkCliLogin("codex"),
          ])) as [CliLoginStatus, CliLoginStatus];
          if (!cancelled) setCliStatus({ claude, codex });
        } catch {
          /* stale status is fine */
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [checkCliLogin]);

  const hasDefault = Boolean(current.provider_id && current.model);

  const seededSelectionRef = useRef(false);
  useEffect(() => {
    if (current.provider_id && !seededSelectionRef.current) {
      setSelectedProviderId(current.provider_id);
      seededSelectionRef.current = true;
    }
  }, [current.provider_id]);

  const setAsDefault = async (m: ModelOption) => {
    setBusyModel(m.model_id);
    try {
      // The model carries its owning provider + preferred runtime — write the
      // coherent triple straight through (same path Settings → Models uses).
      await settingsApi.patchModelDefaults({
        default_runtime: m.default_runtime,
        default_provider_id: m.provider_id,
        default_model: m.model_id,
      });
      await refresh();
    } catch {
      toast.error(
        _t("onboarding.setDefaultFailed" as Parameters<typeof _t>[0]),
      );
    } finally {
      setBusyModel(null);
    }
  };

  const handleCreate = async (payload: {
    name: string;
    provider_kind: string;
    api_key?: string;
    base_url?: string;
    protocol?: string;
    models?: string[];
  }) => {
    await providersApi.create(payload);
    await refresh();
    toast.success(
      _t("onboarding.providerConnected" as Parameters<typeof _t>[0]),
    );
  };

  // api_key is rendered last with the always-present "add channel" button; the
  // other groups (subscription / system) render in server order above it.
  const apiKeyGroup = groups.find((g) => g.key === "api_key");
  const otherGroups = groups.filter((g) => g.key !== "api_key");

  const cliStatusFor = (p: ModelOptionProvider): CliLoginStatus | undefined =>
    p.cli_tool ? cliStatus[p.cli_tool as CliTool] : undefined;

  const renderCard = (p: ModelOptionProvider) => (
    <OptionCard
      key={p.provider_id}
      provider={p}
      cliStatus={cliStatusFor(p)}
      busyModel={busyModel}
      selected={p.provider_id === selectedProviderId}
      onPick={(m) => void setAsDefault(m)}
      onSelect={() =>
        setSelectedProviderId((prev) =>
          prev === p.provider_id ? null : p.provider_id,
        )
      }
      onLogin={() => p.cli_tool && void cliLogin.trigger(p.cli_tool as CliTool)}
    />
  );

  return (
    <OnboardingStep
      title={t("onboarding.connectTitle" as Parameters<typeof t>[0])}
      subtitle={t("onboarding.connectSubtitle" as Parameters<typeof t>[0])}
      footer={
        <StepFooter
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
        <>
          {otherGroups.map((g) => (
            <Section
              key={g.key}
              label={t(
                (SECTION_KEY[g.key] ?? g.key) as Parameters<typeof t>[0],
              )}
            >
              <div className="space-y-2.5">{g.providers.map(renderCard)}</div>
            </Section>
          ))}

          {/* BYOK "API Key channel" — hidden entirely when the org disallows
              members configuring their own model channels. */}
          {configureModelChannel && (
            <Section
              label={t("onboarding.sectionApiKey" as Parameters<typeof t>[0])}
            >
              <div className="space-y-2.5">
                {apiKeyGroup?.providers.map(renderCard)}
                <button
                  type="button"
                  onClick={() => setAddOpen(true)}
                  className="card-interactive flex w-full items-center gap-2.5 rounded-xl border border-dashed border-surface-border bg-surface px-4 py-3 text-left text-sm text-ink-body"
                >
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-soft">
                    <Plus className="h-4 w-4 text-ink-meta" />
                  </span>
                  {t("onboarding.addApiKeyChannel" as Parameters<typeof t>[0])}
                  <span className="ml-auto text-2xs text-ink-muted">
                    {t("onboarding.addApiKeyHint" as Parameters<typeof t>[0])}
                  </span>
                </button>
              </div>
            </Section>
          )}
        </>
      )}

      <ProviderAddDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        providers={descriptors
          .filter((p) => p.auth_type !== "oauth")
          .map(
            (p): ProviderOption => ({
              kind: p.kind,
              display_name: API_KEY_DISPLAY[p.kind] ?? p.display_name,
              supports_custom_base_url: p.supports_custom_base_url,
              supports_protocol_selection: p.supports_protocol_selection,
              default_base_url: p.default_base_url,
              anthropic_base_url: p.anthropic_base_url,
              default_model: p.default_model,
              model_options: p.model_options,
              docs_url: p.docs_url,
            }),
          )}
        onProbeModels={(payload) => providersApi.probeModels(payload)}
        onPing={(payload) =>
          providersApi.ping({
            base_url: payload.base_url,
            api_key: payload.api_key,
            protocol: payload.protocol ?? null,
            models: payload.models,
          })
        }
        onCreate={handleCreate}
      />
      {cliLogin.dialog}
    </OnboardingStep>
  );
};

/* -------------------------------------------------------------------------- */

const OptionCard = ({
  provider,
  cliStatus,
  busyModel,
  selected,
  onSelect,
  onPick,
  onLogin,
}: {
  provider: ModelOptionProvider;
  cliStatus?: CliLoginStatus;
  /** The model id currently being saved as default (drives the spinner). */
  busyModel: string | null;
  selected: boolean;
  onSelect: () => void;
  onPick: (model: ModelOption) => void;
  onLogin: () => void;
}) => {
  const { t } = useTranslation();
  // ``available`` / ``unavailable`` are server-authoritative. ``client_resolved``
  // (CLI subscriptions) is decided here from the local keychain probe.
  const isSubscription = provider.status === "client_resolved";
  const connected =
    provider.status === "available"
      ? true
      : isSubscription
        ? cliStatus?.state === "logged_in"
        : false;
  const needsLogin =
    isSubscription && !connected && cliStatus
      ? !cliStatus.installed || cliStatus.state === "logged_out"
      : false;

  // Onboarding prefers the Claude runtime: show only models runnable on
  // claude_agent (a system model is often configured as a Claude variant + a
  // Codex variant of the same product — surface the Claude one). Keep the
  // current default even if it's a non-Claude model. Fall back to all models
  // when a channel has none (e.g. a Codex-only subscription) so it's never
  // empty. Settings (with its runtime selector) shows the full set.
  const claudeModels = provider.models.filter(
    (m) => m.runtimes.includes("claude_agent") || m.is_current_default,
  );
  const models = claudeModels.length > 0 ? claudeModels : provider.models;

  const currentModel = models.find((m) => m.is_current_default) ?? null;
  const isDefault = currentModel !== null;
  const busy = models.some((m) => m.model_id === busyModel);
  const headerProps = connected
    ? {
        role: "button" as const,
        tabIndex: 0,
        onClick: onSelect,
        onKeyDown: (e: KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect();
          }
        },
      }
    : {};

  return (
    <div
      className={`rounded-xl border transition-all ${
        selected
          ? "border-brand/60 bg-brand-light/20 ring-1 ring-brand/40"
          : isDefault
            ? "border-brand/30 bg-brand-light/10"
            : "border-surface-border bg-surface"
      }`}
    >
      <div className="flex items-center gap-3.5 px-4 py-3.5" {...headerProps}>
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[13px] font-bold ${
            selected || isDefault
              ? "bg-brand/10 text-brand"
              : "bg-surface-soft text-ink-meta"
          }`}
        >
          {provider.label.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-heading">
              {provider.label}
            </span>
            {isDefault && (
              <Badge
                className={`${DEFAULT_BADGE_CLASS} bg-[#898F9C]/[0.08] text-[#898f9c]`}
              >
                {t("onboarding.defaultBadge" as Parameters<typeof t>[0])}
              </Badge>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin text-brand" />}
          {connected && !isDefault && (
            <Badge
              className={`${AVAILABLE_BADGE_CLASS} gap-1 bg-[#f3f2ff] text-[#725cf9]`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-[#725cf9]" />
              {t("onboarding.availableBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
          {connected && isDefault && <Check className="h-4 w-4 text-brand" />}
          {/* CLI subscription that isn't logged in → status label + login CTA. */}
          {needsLogin && (
            <>
              <span className="flex items-center gap-1 text-xs text-ink-meta">
                <Lock className="h-3 w-3" />
                {cliStatus && !cliStatus.installed
                  ? t("onboarding.notInstalledBadge" as Parameters<typeof t>[0])
                  : t("onboarding.notLoggedInBadge" as Parameters<typeof t>[0])}
              </span>
              <Button
                variant="outline"
                size="sm"
                className="text-xs"
                onClick={(e) => {
                  e.stopPropagation();
                  onLogin();
                }}
              >
                {cliStatus && !cliStatus.installed
                  ? t("settings.model.installCli" as Parameters<typeof t>[0])
                  : t("settings.model.cliLogin" as Parameters<typeof t>[0])}
              </Button>
            </>
          )}
          {/* System / api_key channel the server marked unavailable (e.g. not
              signed in to Valuz) → show the reason, no CLI affordance. */}
          {provider.status === "unavailable" && (
            <span className="flex items-center gap-1 text-xs text-ink-meta">
              <Lock className="h-3 w-3" />
              {provider.unavailable_reason ??
                t("onboarding.notLoggedInBadge" as Parameters<typeof t>[0])}
            </span>
          )}
        </div>
      </div>

      {connected && selected && models.length > 0 && (
        <div className="border-t border-brand/20 px-4 pb-4 pt-3">
          <div className="mb-2 text-xs font-medium text-ink-meta">
            {t("onboarding.modelLabel" as Parameters<typeof t>[0], {
              channel: provider.label,
            })}
          </div>
          <Select
            value={currentModel?.model_id ?? ""}
            onValueChange={(v) => {
              const m = models.find((x) => x.model_id === v);
              if (m) onPick(m);
            }}
            disabled={busy}
          >
            <SelectTrigger className="w-full text-xs">
              {currentModel ? (
                // Collapsed trigger: plain name only. ``SelectValue`` would
                // mirror the option row (including its hint cell), and the
                // hint belongs to the open list.
                <span className="truncate text-ink-heading">
                  {currentModel.label !== currentModel.model_id
                    ? currentModel.label
                    : modelLabel(currentModel.model_id)}
                </span>
              ) : (
                <SelectValue
                  placeholder={t(
                    "onboarding.pickModelHint" as Parameters<typeof t>[0],
                  )}
                />
              )}
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem
                  key={m.model_id}
                  value={m.model_id}
                  className="text-xs"
                >
                  {/* Server label wins when it's a real display name (system
                      channels carry the admin display_name). For subscription /
                      built-in rows the server has no label so ``m.label`` is the
                      raw id — fall back to the static brand catalog (e.g.
                      ``claude-sonnet-4-6`` → "Sonnet 4.6"), same as the Composer's
                      AgentModelPicker. */}
                  <span className="min-w-0 flex-1 truncate">
                    {m.label !== m.model_id ? m.label : modelLabel(m.model_id)}
                  </span>
                  <ModelSelectionHint hint={m.selection_hint} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
};

const Section = ({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) => (
  <div className="mt-6 first:mt-0">
    <div className="mb-2.5 text-2xs font-semibold uppercase tracking-[0.08em] text-ink-section">
      {label}
    </div>
    {children}
  </div>
);
