import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Badge,
  ProviderAddDialog,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  type ProviderOption,
} from "@valuz/ui";
import {
  providersApi,
  settingsApi,
  type ModelDefaults,
  type ProviderDescriptor,
  type ProviderListItem,
} from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
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
 * Step 2 · Connect a model channel — REAL providers, no mock. Lists the
 * backend's providers, lets the user connect one (CLI subscription login or an
 * API key), pick a model, and set it as the global default. The default is set
 * via settingsApi.patchModelDefaults — the EXACT same path Settings → Models
 * uses, so there's a single default mechanism, not two.
 */

const SUBSCRIPTION_TOOL: Record<string, CliTool> = {
  "claude-subscription": "claude",
  "codex-subscription": "codex",
};

const API_KEY_DISPLAY: Record<string, string> = {
  anthropic: "Anthropic (Claude)",
  openai: "OpenAI (ChatGPT)",
  deepseek: "DeepSeek",
  openrouter: "OpenRouter",
  moonshot: "Moonshot (Kimi)",
  minimax: "MiniMax",
};

const LOGIN_CMD: Record<CliTool, string> = {
  claude: "claude /login",
  codex: "codex login",
};
const INSTALL_CMD: Record<CliTool, string> = {
  claude: "npm install -g @anthropic-ai/claude-code",
  codex: "npm install -g @openai/codex",
};

export const ConnectStep = ({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}) => {
  const { t } = useTranslation();
  const { checkCliLogin } = usePlatform();
  const [providers, setProviders] = useState<ProviderListItem[]>([]);
  const [descriptors, setDescriptors] = useState<ProviderDescriptor[]>([]);
  const [defaults, setDefaults] = useState<ModelDefaults | null>(null);
  const [cliStatus, setCliStatus] = useState<
    Partial<Record<CliTool, CliLoginStatus>>
  >({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  // Which channel the user has TAPPED on. The actual default lives on the
  // backend (defaults.default_provider_id); this is a local UI hint so the
  // model picker below has somewhere to anchor BEFORE the user picks a model.
  // Seeded from the backend default on load and after refresh — see the
  // useEffect below.
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );

  const refresh = async () => {
    const [list, md] = await Promise.all([
      providersApi.list(),
      settingsApi.getModelDefaults(),
    ]);
    setProviders(list.providers);
    setDefaults(md);
  };

  const cliLogin = useCliLoginFlow({
    onProviderMarkedOAuth: () => void refresh(),
  });

  // Initial load: providers list, model defaults, descriptors, CLI probe.
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

  const defaultId = defaults?.default_provider_id ?? null;
  const defaultModel = defaults?.default_model ?? null;
  const hasDefault = Boolean(defaultId && defaultModel);

  // Seed local selection from the backend default ONCE, the first time
  // defaultId becomes known. Don't keep re-syncing — if the user toggles a
  // row off (or picks a different one), that explicit intent must stick;
  // re-running this effect on subsequent defaultId changes would clobber it.
  const seededSelectionRef = useRef(false);
  useEffect(() => {
    if (defaultId && !seededSelectionRef.current) {
      setSelectedProviderId(defaultId);
      seededSelectionRef.current = true;
    }
  }, [defaultId]);

  const setAsDefault = async (providerId: string, model: string) => {
    setBusy(providerId);
    try {
      // Same call Settings → Models makes — one default mechanism.
      const next = await settingsApi.patchModelDefaults({
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

  const enableSubscription = async (providerId: string) => {
    setBusy(providerId);
    try {
      await providersApi.enable(providerId);
      await refresh();
    } catch {
      toast.error(_t("onboarding.enableFailed" as Parameters<typeof _t>[0]));
    } finally {
      setBusy(null);
    }
  };

  const subscriptions = providers.filter((p) => p.auth_type === "oauth");
  const apiKeyConnected = providers.filter(
    (p) => p.auth_type !== "oauth" && p.credential_source !== "none",
  );

  // Both OAuth and api-key channels surface their model list via the same
  // ``model_options`` field. For OAuth/subscription channels the backend
  // hydrates the descriptor from ``valuz_agent/resources/subscription_models.json``
  // (see ``_hydrate_subscription_providers``), so the picker stays in sync with
  // the system settings + agent settings dropdowns without a frontend duplicate.
  const modelsFor = (p: ProviderListItem): string[] => p.model_options;

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
                  { model: defaultModel ?? "" },
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
        <>
          {subscriptions.length > 0 && (
            <Section
              label={t(
                "onboarding.sectionLocalSubscription" as Parameters<
                  typeof t
                >[0],
              )}
            >
              <div className="space-y-2.5">
                {subscriptions.map((p) => (
                  <ProviderRow
                    key={p.id}
                    provider={p}
                    cliStatus={cliStatus[SUBSCRIPTION_TOOL[p.provider_kind]]}
                    isDefault={p.id === defaultId}
                    selected={p.id === selectedProviderId}
                    busy={busy === p.id}
                    models={modelsFor(p)}
                    currentModel={p.id === defaultId ? defaultModel : null}
                    onSelect={() =>
                      setSelectedProviderId((prev) =>
                        prev === p.id ? null : p.id,
                      )
                    }
                    onPick={(m) => void setAsDefault(p.id, m)}
                    onEnable={() => void enableSubscription(p.id)}
                    onLogin={() =>
                      void cliLogin.trigger(SUBSCRIPTION_TOOL[p.provider_kind])
                    }
                  />
                ))}
              </div>
            </Section>
          )}

          <Section
            label={t("onboarding.sectionApiKey" as Parameters<typeof t>[0])}
          >
            <div className="space-y-2.5">
              {apiKeyConnected.map((p) => (
                <ProviderRow
                  key={p.id}
                  provider={p}
                  isDefault={p.id === defaultId}
                  selected={p.id === selectedProviderId}
                  busy={busy === p.id}
                  models={modelsFor(p)}
                  currentModel={p.id === defaultId ? defaultModel : null}
                  onSelect={() => setSelectedProviderId(p.id)}
                  onPick={(m) => void setAsDefault(p.id, m)}
                />
              ))}
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

const ProviderRow = ({
  provider,
  cliStatus,
  isDefault,
  selected,
  busy,
  models,
  currentModel,
  onSelect,
  onPick,
  onEnable,
  onLogin,
}: {
  provider: ProviderListItem;
  cliStatus?: CliLoginStatus;
  /** True when this provider is the BACKEND default (gets the 「默认」 badge). */
  isDefault: boolean;
  /** True when the user has tapped this row. Drives the brand-tinted ring +
   *  reveals the inline model picker below. Selecting alone doesn't set a
   *  default — only picking a model in the inline picker does. */
  selected: boolean;
  busy?: boolean;
  /** Model options surfaced by the inline picker — only used when selected
   *  && connected. */
  models?: string[];
  /** Current model value to seed the inline picker — null when this row
   *  isn't the backend default. */
  currentModel?: string | null;
  onSelect: () => void;
  onPick?: (model: string) => void;
  onEnable?: () => void;
  onLogin?: () => void;
}) => {
  const { t } = useTranslation();
  const isOAuth = provider.auth_type === "oauth";
  const connected = isOAuth
    ? provider.enabled
    : provider.credential_source !== "none";
  // OAuth, installed-but-not-logged-in → show the terminal login hint.
  const needsLogin =
    isOAuth && !connected && cliStatus
      ? !cliStatus.installed || cliStatus.state === "logged_out"
      : false;
  const loggedInNotEnabled =
    isOAuth && !connected && cliStatus?.state === "logged_in";

  // Click-target ≠ <button> here because we want to host a real <Select>
  // (which renders as a button) inside on the inline expansion. Nested buttons
  // are invalid HTML; use role="button" on the header row instead.
  const headerProps = connected
    ? {
        role: "button" as const,
        tabIndex: 0,
        onClick: onSelect,
        onKeyDown: (e: React.KeyboardEvent) => {
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
      <div
        {...headerProps}
        className={`flex items-center gap-3.5 px-4 py-3.5 ${
          connected ? "cursor-pointer" : ""
        }`}
      >
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[13px] font-bold ${
            selected || isDefault
              ? "bg-brand/10 text-brand"
              : "bg-surface-soft text-ink-meta"
          }`}
        >
          {provider.name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-heading">
              {provider.name}
            </span>
            {isDefault && (
              <Badge variant="success" className="text-2xs">
                {t("onboarding.defaultBadge" as Parameters<typeof t>[0])}
              </Badge>
            )}
          </div>
          {!connected && (
            <p className="mt-1 text-xs leading-5 text-ink-body">
              {provider.provider_kind.includes("claude")
                ? t("onboarding.claudeSubDesc" as Parameters<typeof t>[0])
                : provider.provider_kind.includes("codex")
                  ? t("onboarding.codexSubDesc" as Parameters<typeof t>[0])
                  : provider.name}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {busy && <Loader2 className="h-4 w-4 animate-spin text-brand" />}
          {!connected && loggedInNotEnabled && (
            <Badge variant="success" className="gap-1 text-2xs">
              <Check className="h-2.5 w-2.5" />
              {t("onboarding.availableBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
          {!connected && needsLogin && (
            <Badge variant="outline" className="gap-1 text-2xs">
              <Lock className="h-2.5 w-2.5" />
              {cliStatus && !cliStatus.installed
                ? t("onboarding.notInstalledBadge" as Parameters<typeof t>[0])
                : t("onboarding.notLoggedInBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
          {/* Connected → show 「可用」 so the user can tell at a glance which
              channels are wired up. Inline model picker reveals on click. */}
          {connected && !selected && (
            <Badge variant="success" className="gap-1 text-2xs">
              <Check className="h-2.5 w-2.5" />
              {t("onboarding.availableBadge" as Parameters<typeof t>[0])}
            </Badge>
          )}
          {connected && selected && <Check className="h-4 w-4 text-brand" />}
        </div>
      </div>

      {/* Inline model picker — anchored to the selected row, sharing its
          card so the "Model · X" block visually belongs to it. */}
      {connected && selected && onPick && (
        <div className="border-t border-brand/20 px-4 pb-4 pt-3">
          <div className="mb-2 text-xs font-medium text-ink-meta">
            {t("onboarding.modelLabel" as Parameters<typeof t>[0], {
              channel: provider.name,
            })}
          </div>
          <Select
            value={currentModel ?? ""}
            onValueChange={onPick}
            disabled={busy || (models?.length ?? 0) === 0}
          >
            <SelectTrigger className="w-full font-mono text-xs">
              <SelectValue
                placeholder={t(
                  "onboarding.pickModelHint" as Parameters<typeof t>[0],
                )}
              />
            </SelectTrigger>
            <SelectContent>
              {(models ?? []).map((m) => (
                <SelectItem key={m} value={m} className="font-mono text-xs">
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Not-yet-connected OAuth: one-click enable (logged in) or login hint */}
      {!connected && isOAuth && (
        <div className="border-t border-surface-border/70 px-4 pb-3.5 pt-3">
          {loggedInNotEnabled ? (
            <button
              type="button"
              disabled={busy}
              onClick={onEnable}
              className="rounded-lg bg-brand px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-brand-hover disabled:opacity-50"
            >
              {t("onboarding.oneClickEnable" as Parameters<typeof t>[0])}
            </button>
          ) : (
            <SetupHint cliStatus={cliStatus} onLogin={onLogin} />
          )}
        </div>
      )}
    </div>
  );
};

const SetupHint = ({
  cliStatus,
  onLogin,
}: {
  cliStatus?: CliLoginStatus;
  onLogin?: () => void;
}) => {
  const { t } = useTranslation();
  const tool: CliTool = cliStatus?.cliPath?.includes("codex")
    ? "codex"
    : "claude";
  const installing = cliStatus ? !cliStatus.installed : false;
  const cmd = installing ? INSTALL_CMD[tool] : LOGIN_CMD[tool];
  return (
    <div>
      <div className="flex items-center gap-2 text-xs text-ink-body">
        {installing
          ? t("onboarding.installHint" as Parameters<typeof t>[0])
          : t("onboarding.loginHint" as Parameters<typeof t>[0])}
        <code className="rounded border border-surface-border bg-surface-soft px-2 py-0.5 font-mono text-2xs text-ink-heading">
          {cmd}
        </code>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(cmd);
            toast.success(
              _t("onboarding.copiedCommand" as Parameters<typeof _t>[0]),
            );
          }}
          className="text-ink-meta hover:text-ink-heading"
        >
          {t("onboarding.copyCommand" as Parameters<typeof t>[0])}
        </button>
      </div>
      <button
        type="button"
        onClick={onLogin}
        className="mt-2 rounded-lg border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-ink-body transition-colors hover:bg-surface-soft"
      >
        {t("onboarding.goLogin" as Parameters<typeof t>[0])}
      </button>
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
