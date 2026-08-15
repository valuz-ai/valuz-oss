import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "sonner";
import { Cpu, FilePenLine, Lock, Plus, RefreshCw, Trash2 } from "lucide-react";
import type { ProviderOption } from "@valuz/ui";
import {
  Badge,
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  ProviderAddDialog,
  ProviderEditDialog,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
  SettingsSection,
  cn,
  IconBox,
} from "@valuz/ui";
import { useCapabilities, useTranslation } from "@valuz/core";
import { assetUrl } from "@valuz/shared";
import {
  useCliLoginFlow,
  type CliTool,
  type CliLoginStatus,
} from "@valuz/app/components";
import { usePlatform } from "@valuz/app/platform";
import { modelLabel, modelSelectionLabel } from "@valuz/shared";
import {
  providersApi,
  runtimesApi,
  settingsApi,
  isProviderRuntimeCompatible,
  compatibleRuntimes,
  RUNTIME_DISPLAY_NAME,
  DEFAULT_EFFORT_VALUES,
  EFFORT_FALLBACK,
  type LLMChannel,
  type LLMChannelDetail,
  type ProviderDescriptor,
  type RuntimeListItem,
  type ModelDefaults,
  type ModelOptionGroup,
  type ModelOptionProvider,
  type EffortLevel,
  type RuntimeId,
} from "@valuz/core";

// REP-107 Slice 4d: CLI login is now the entry path for *subscription*
// providers only (claude-subscription, codex-subscription). Surfacing
// "CLI 登录" on the api_key Anthropic / OpenAI cards predates the
// subscription providers and would now create two parallel paths to the
// same outcome -- collapse them onto the subscription cards by mapping
// only the subscription kinds. Users who want subscription auth pick
// it via the ProviderConnectionPicker instead of repurposing an
// api_key provider.
const CLI_TOOL_BY_PROVIDER_KIND: Record<string, CliTool> = {
  "claude-subscription": "claude",
  "codex-subscription": "codex",
};

const ModelAvailableBadge = ({ label }: { label: string }) => (
  <Badge variant="metaBrand" className="gap-1">
    <span className="h-1.5 w-1.5 rounded-full bg-brand-700" />
    {label}
  </Badge>
);

// A model-options provider is "usable" when its credential is actually
// reachable: subscription rows (``client_resolved``) depend on the local CLI
// login the server can't see, so the client decides from its keychain probe;
// system / api_key / org rows are server-authoritative via ``status``.
const isModelProviderUsable = (
  p: ModelOptionProvider,
  cliStatus: Partial<Record<CliTool, CliLoginStatus>>,
): boolean =>
  p.status === "client_resolved"
    ? p.cli_tool
      ? cliStatus[p.cli_tool as CliTool]?.state === "logged_in"
      : false
    : p.status === "available";

export const ModelSection = () => {
  const { t } = useTranslation();
  const { checkCliLogin: platformCheckCliLogin } = usePlatform();
  // Runtime capability gate. OSS defaults to true; overlay editions flip
  // it off when an org policy disallows configuring model channels.
  // CLI-login flow stays exposed regardless — those credentials live in
  // the CLI's keychain and remain reachable outside Valuz anyway.
  const { configureModelChannel } = useCapabilities();

  const [modelDefaults, setModelDefaults] = useState<ModelDefaults>({
    default_runtime: "claude_agent",
    default_provider_id: null,
    default_model: null,
    // Optimistic initial value before /v1/settings/model-defaults
    // returns. Same EFFORT_FALLBACK the backend coerces unset rows
    // to, so the dropdown trigger never flashes a different state.
    default_effort: EFFORT_FALLBACK,
  });
  const [runtimes, setRuntimes] = useState<RuntimeListItem[]>([]);
  // Auto-detected CLI state for the OAuth subscription rows. Drives both
  // the status badge and whether
  // the [CLI 登录] / [安装 CLI] button shows up. Re-checked on every
  // providersList reload so flipping to logged_in from a terminal
  // updates the row without manual refresh.
  const [cliStatus, setCliStatus] = useState<
    Partial<Record<CliTool, CliLoginStatus>>
  >({});

  // -- Provider state --
  const [providersList, setProvidersList] = useState<LLMChannel[]>([]);
  // False until the first provider-list fetch settles, so the "模型通道"
  // (manage-channels) section isn't shown with an empty card during the
  // initial page load — only after we actually have the list.
  const [providersListLoaded, setProvidersListLoaded] = useState(false);
  // The default-config model picker reads from the resolved model-options read
  // model (runtime-tagged, system channels included), NOT providersList — that
  // list is for the manage-channels section below. Keeps the picker's "which
  // models can run on the selected runtime" logic server-side.
  const [modelOptions, setModelOptions] = useState<ModelOptionGroup[]>([]);
  // False until the first model-options fetch settles. The "no usable model"
  // warning keys off this so it never flashes during the initial page load —
  // we only assert "no model" once we've actually looked.
  const [modelOptionsLoaded, setModelOptionsLoaded] = useState(false);
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<LLMChannelDetail | null>(
    null,
  );
  const [deleteTarget, setDeleteTarget] = useState<LLMChannel | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadProvidersList = useCallback(async () => {
    try {
      const res = await providersApi.list();
      // Hide:
      // - managed (Reportify) -- those live under the Connectors module.
      // - system (platform-provided, e.g. "Valuz 系统模型") -- these need no
      //   setup and aren't user-managed, so they're noise in the
      //   manage-your-channels list. They still appear in onboarding's
      //   model-default picker (ConnectStep), which has its own filter.
      // - Unconfigured api_key descriptors -- they're "quick-add" seeds
      //   that show up as "未配置" dead rows if the user never touched
      //   them. Two exceptions kept:
      //     * OAuth subscription providers (claude/codex /login):
      //       credential_source === "none" but the CLI keychain makes
      //       them usable.
      //     * Already-configured providers: credential_source !== "none".
      setProvidersList(
        res.providers.filter(
          (p) =>
            p.source !== "managed" &&
            p.source !== "system" &&
            (p.credential_source !== "none" || p.auth_type === "oauth"),
        ),
      );
    } catch {
      // silently fail
    } finally {
      // First resolution done — gate the manage-channels section on this so it
      // doesn't render an empty card during the initial load.
      setProvidersListLoaded(true);
    }
  }, []);

  // Re-check the CLI subscription login state (Claude/Codex). The login runs
  // in an external terminal, so the app has to re-poll to notice it.
  const refreshCliStatus = useCallback(async () => {
    if (!platformCheckCliLogin) return;
    try {
      const [claude, codex] = (await Promise.all([
        platformCheckCliLogin("claude"),
        platformCheckCliLogin("codex"),
      ])) as [CliLoginStatus, CliLoginStatus];
      setCliStatus({ claude, codex });
    } catch {
      // best-effort -- stale cliStatus is fine
    }
  }, [platformCheckCliLogin]);

  // (B) After a login terminal is launched, poll that tool's status until it
  // flips to logged_in (or ~3 min passes), then refresh the row. One timer
  // per tool; cleared on success / timeout / unmount.
  const cliPollRef = useRef<Partial<Record<CliTool, number>>>({});
  const pollCliLogin = useCallback(
    (tool: CliTool) => {
      if (!platformCheckCliLogin) return;
      const deadline = Date.now() + 180_000;
      const tick = async () => {
        if (Date.now() > deadline) {
          delete cliPollRef.current[tool];
          return;
        }
        try {
          const status = (await platformCheckCliLogin(tool)) as CliLoginStatus;
          if (status.state === "logged_in") {
            setCliStatus((prev) => ({ ...prev, [tool]: status }));
            void loadProvidersList();
            delete cliPollRef.current[tool];
            return;
          }
        } catch {
          // keep polling
        }
        cliPollRef.current[tool] = window.setTimeout(tick, 2500);
      };
      if (cliPollRef.current[tool] !== undefined) {
        window.clearTimeout(cliPollRef.current[tool]);
      }
      cliPollRef.current[tool] = window.setTimeout(tick, 2500);
    },
    [platformCheckCliLogin, loadProvidersList],
  );

  const cliLogin = useCliLoginFlow({
    onProviderMarkedOAuth: () => {
      void loadProvidersList();
    },
    onLoginLaunched: pollCliLogin,
  });

  const loadProviders = useCallback(async () => {
    try {
      const res = await providersApi.listProviders();
      setProviders(res.providers);
    } catch {
      // silently fail
    }
  }, []);

  const loadModelOptions = useCallback(async () => {
    try {
      const res = await settingsApi.getModelOptions();
      setModelOptions(res.groups);
    } catch {
      // soft-fail: picker shows no options until a reload succeeds
    } finally {
      // First resolution done (success or fail) — lets the no-model warning
      // render only after we've actually checked, not during initial load.
      setModelOptionsLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadProvidersList();
  }, [loadProvidersList]);
  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);
  // Reload the picker options whenever the manage-channels list changes (add /
  // delete / enable) — providersList is the cheapest "something changed" signal.
  useEffect(() => {
    void loadModelOptions();
  }, [loadModelOptions, providersList]);

  useEffect(() => {
    void settingsApi
      .getModelDefaults()
      .then(setModelDefaults)
      .catch(() => {
        // soft-fail: UI falls back to local default ("medium" / claude_agent)
      });
    void runtimesApi
      .list()
      .then((res) => setRuntimes(res.runtimes))
      .catch(() => {
        // soft-fail: runtime picker hides itself if list is empty
      });
  }, []);

  // Detect CLI login state for the OAuth subscription providers on
  // mount and whenever the provider list refreshes -- a successful
  // claude/codex /login from a terminal then flips the badge to
  // "available" without the user having to reload. Failures degrade
  // silently (status stays ``undefined``, badge hides).
  useEffect(() => {
    void refreshCliStatus();
  }, [providersList, refreshCliStatus]);

  // Auto-materialize a logged-in subscription into a real channel. The CLI
  // keychain is local + invisible to the server, so "available" is detected
  // client-side; the moment a subscription kind is seen logged in but still
  // un-materialized (a virtual template: auth_type "oauth" + !deletable — e.g.
  // an external `codex login` the in-app login flow never enabled), tell the
  // backend to enable it. This is what makes "可用 = ready to configure an
  // agent" actually true: without it the row stays a template whose `ch-*` id
  // 400s ("provider not found") at session creation. Idempotent + guarded so it
  // fires once per kind — after enable the row turns deletable, so it no longer
  // matches. The ref stays populated after a successful enable (circuit
  // breaker): if the backend response ever fails to flip `deletable` (e.g. a
  // legacy seeded row before the server-side normalization existed), relying on
  // the row shape alone turns this effect into an infinite enable→reload loop.
  const materializingRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const pending = providersList.filter((p) => {
      if (p.auth_type !== "oauth" || p.deletable) return false;
      const tool = CLI_TOOL_BY_PROVIDER_KIND[p.provider_kind];
      if (!tool) return false;
      return (
        cliStatus[tool]?.state === "logged_in" &&
        !materializingRef.current.has(p.id)
      );
    });
    if (pending.length === 0) return;
    void (async () => {
      pending.forEach((p) => materializingRef.current.add(p.id));
      try {
        await Promise.all(pending.map((p) => providersApi.enable(p.id)));
        await loadProvidersList();
      } catch {
        // best-effort: the session-creation backstop still materializes on
        // use. Re-arm only on failure so a focus/visibility recheck retries;
        // a successful enable keeps the id guarded for this mount.
        pending.forEach((p) => materializingRef.current.delete(p.id));
      }
    })();
  }, [providersList, cliStatus, loadProvidersList]);

  // (A) Re-check when the window regains focus / becomes visible — the user
  // typically switches back to the app right after finishing the terminal
  // login, so the badge flips without a manual refresh.
  useEffect(() => {
    const recheck = () => {
      void refreshCliStatus();
      void loadProvidersList();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") recheck();
    };
    window.addEventListener("focus", recheck);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", recheck);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshCliStatus, loadProvidersList]);

  // Stop any in-flight CLI-login polls on unmount.
  useEffect(() => {
    const polls = cliPollRef.current;
    return () => {
      Object.values(polls).forEach(
        (id) => id !== undefined && window.clearTimeout(id),
      );
    };
  }, []);

  const handleSetEffort = async (value: EffortLevel) => {
    try {
      const next = await settingsApi.patchModelDefaults({
        default_effort: value,
      });
      setModelDefaults(next);
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.updateFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  const handleSetRuntime = async (value: RuntimeId) => {
    // If the existing (provider, model) tuple can't run on the new
    // runtime, clear it together with the runtime change. No silent
    // promotion -- user re-picks deliberately from the filtered list.
    const currentProvider = providersList.find(
      (p) => p.id === modelDefaults.default_provider_id,
    );
    const stillCompat =
      !!currentProvider && isProviderRuntimeCompatible(currentProvider, value);
    try {
      const next = await settingsApi.patchModelDefaults({
        default_runtime: value,
        ...(stillCompat ? {} : { default_provider_id: "", default_model: "" }),
      });
      setModelDefaults(next);
      if (!stillCompat && modelDefaults.default_provider_id) {
        toast.info(
          t(
            "settings.model.runtimeSwitchedReSelect" as Parameters<typeof t>[0],
          ),
        );
      }
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.updateFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  // value is ``"<providerId>::<modelId>"`` (modelId can be empty for subscription types)
  const handleSetDefaultModelCombo = async (combinedKey: string) => {
    const [providerId, modelId = ""] = combinedKey.split("::");
    try {
      const next = await settingsApi.patchModelDefaults({
        default_provider_id: providerId,
        default_model: modelId,
      });
      setModelDefaults(next);
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("common.updateFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  const handleTestProvider = async (providerId: string) => {
    try {
      const result = await providersApi.test(providerId);
      if (result.success) {
        toast.success(
          result.latency_ms
            ? t("settings.model.testSuccessLatency", {
                latency: result.latency_ms,
              })
            : t("settings.model.testSuccess"),
        );
      } else {
        toast.error(
          result.error_message || t("settings.model.connectionTestFailed"),
        );
      }
      await loadProvidersList();
    } catch {
      toast.error(t("settings.model.testFailed"));
    }
  };

  const handleAddProvider = async (payload: {
    name: string;
    provider_kind: string;
    api_key?: string;
    base_url?: string;
    default_model?: string;
    protocol?: string;
    runtime_provider?: "claude_agent" | "codex" | "deepagents" | "deepseek_harness";
    models?: string[];
  }) => {
    await providersApi.create(payload);
    toast.success(t("settings.model.modelAdded"));
    await loadProvidersList();
  };

  const handleOpenEdit = async (providerId: string) => {
    try {
      const detail = await providersApi.get(providerId);
      setEditProvider(detail);
    } catch {
      toast.error(t("settings.model.detailLoadFailed"));
    }
  };

  const handleSaveEdit = async (
    providerId: string,
    payload: {
      base_url?: string;
      api_key?: string;
      default_model?: string;
      protocol?: string;
      models?: string[];
      name?: string;
    },
  ) => {
    await providersApi.update(providerId, payload);
    toast.success(t("settings.model.configSaved"));
    setEditProvider(null);
    await loadProvidersList();
  };

  const handleDeleteProvider = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await providersApi.delete(deleteTarget.id);
      toast.success(t("settings.model.modelDeleted"));
      setDeleteTarget(null);
      await loadProvidersList();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("settings.connectors.deleteFailed"),
      );
    } finally {
      setDeleting(false);
    }
  };

  const maskApiKey = (source: string) => {
    if (source === "none") return null;
    return "••••••••";
  };

  // The picker can select something iff some channel exposes a usable model
  // (any runtime). Drives the "no model configured" warning — which must follow
  // what's actually pickable in the default-config card (system channels
  // included via model-options), NOT the manage-channels ``providersList`` that
  // deliberately omits system channels.
  const hasUsableModel = modelOptions.some((g) =>
    g.providers.some(
      (p) => isModelProviderUsable(p, cliStatus) && p.models.length > 0,
    ),
  );

  return (
    <>
      <SettingsSection
        title={t("settings.model.title")}
        desc={t("settings.model.desc")}
      >
        {/* No-model warning — gated on what the picker can actually select
            (model-options), so it disappears the moment a usable model exists,
            including system channels absent from ``providersList``.
            ``modelOptionsLoaded`` suppresses it during the initial fetch so it
            never flashes before we've checked. */}
        {modelOptionsLoaded && !hasUsableModel && (
          <div className="mt-5 flex items-center gap-3 rounded-xl border border-error-text/20 bg-error-light px-4 py-3 text-xs text-error-text">
            <Lock className="h-4 w-4" />
            <span className="flex-1">{t("settings.model.noModelWarning")}</span>
            {configureModelChannel && (
              <Button size="sm" onClick={() => setAddOpen(true)}>
                {t("settings.model.configureModel")}
              </Button>
            )}
          </div>
        )}

        {/* -- Default card -- always rendered. Previously gated on
            `providersList.filter(c => c.enabled).length > 0`, which hid the
            entire runtime / model / reasoning-effort picker until a channel was
            configured. The model picker reads the server-resolved model-options
            (system channels included) and shows a "no models for this runtime"
            placeholder when empty, so it is safe to always render. */}
        <>
          <div className="mt-5 mb-3">
            <div className="text-sm font-medium text-ink-heading">
              {t("settings.model.defaultConfig" as Parameters<typeof t>[0])}
            </div>
            <div className="mt-0.5 text-xs text-ink-meta">
              {t("settings.model.defaultConfigDesc" as Parameters<typeof t>[0])}
            </div>
          </div>
          {(() => {
            type ModelOpt = {
              key: string;
              providerId: string;
              providerName: string;
              modelId: string;
              itemLabel: string;
            };
            const runtime = modelDefaults.default_runtime;
            const allOptions: ModelOpt[] = [];
            const groups: {
              providerId: string;
              providerName: string;
              options: ModelOpt[];
            }[] = [];
            for (const grp of modelOptions) {
              for (const p of grp.providers) {
                if (!isModelProviderUsable(p, cliStatus)) continue;
                // The picker is runtime-first: show only models that can run
                // on the selected runtime. ``provider_id`` on each model is its
                // OWNING channel (a merged system card spans descriptors), so
                // the (provider, model) the user picks resolves correctly.
                const models = p.models.filter((m) =>
                  m.runtimes.includes(runtime),
                );
                if (models.length === 0) continue;
                const groupOptions: ModelOpt[] = models.map((m) => ({
                  key: `${m.provider_id}::${m.model_id}`,
                  providerId: m.provider_id,
                  providerName: p.label,
                  modelId: m.model_id,
                  // Server label wins when it's a real display name; subscription
                  // / built-in rows have none (``m.label`` === raw id) → fall back
                  // to the static brand catalog, same as the Composer picker.
                  itemLabel: modelSelectionLabel(
                    m.label !== m.model_id ? m.label : modelLabel(m.model_id),
                    m.selection_hint,
                  ),
                }));
                allOptions.push(...groupOptions);
                groups.push({
                  providerId: p.provider_id,
                  providerName: p.label,
                  options: groupOptions,
                });
              }
            }
            const selectedKey = modelDefaults.default_provider_id
              ? `${modelDefaults.default_provider_id}::${modelDefaults.default_model ?? ""}`
              : "";
            const selectedOption =
              allOptions.find((o) => o.key === selectedKey) ?? null;
            return (
              <Card className="rounded-xl shadow-xs">
                <CardContent className="px-5 py-0">
                  {/* Runtime */}
                  <div className="flex items-center gap-4 border-b border-[#f7f8fa] px-0 py-3 dark:border-surface-border">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink-heading">
                        {t("cron.runtime" as Parameters<typeof t>[0])}
                      </div>
                      <div className="mt-0.5 text-2xs text-ink-meta">
                        {t(
                          "settings.model.agentEngine" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </div>
                    </div>
                    <Select
                      value={modelDefaults.default_runtime}
                      onValueChange={(v) => {
                        void handleSetRuntime(v as RuntimeId);
                      }}
                    >
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[200px] text-xs"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {runtimes.map((r) => (
                          <SelectItem
                            key={r.id}
                            value={r.id}
                            disabled={!r.available}
                          >
                            {r.display_name}
                            {!r.available && r.unavailable_reason
                              ? ` · ${r.unavailable_reason}`
                              : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Model (provider x model combined) */}
                  <div className="flex items-center gap-4 border-b border-[#f7f8fa] px-0 py-3 dark:border-surface-border">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink-heading">
                        {t("common.model" as Parameters<typeof t>[0])}
                      </div>
                      <div className="mt-0.5 text-2xs text-ink-meta">
                        {allOptions.length === 0
                          ? t(
                              "settings.model.noModelsForRuntime" as Parameters<
                                typeof t
                              >[0],
                            )
                          : t(
                              "settings.model.selectDefaultProvider" as Parameters<
                                typeof t
                              >[0],
                            )}
                      </div>
                    </div>
                    <Select
                      value={selectedOption ? selectedKey : ""}
                      onValueChange={(v) => {
                        void handleSetDefaultModelCombo(v);
                      }}
                      disabled={allOptions.length === 0}
                    >
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[200px] text-xs"
                      >
                        {selectedOption ? (
                          <span className="truncate text-ink-heading">
                            {selectedOption.itemLabel}
                          </span>
                        ) : (
                          <SelectValue
                            placeholder={t(
                              "settings.model.selectDefaultModel" as Parameters<
                                typeof t
                              >[0],
                            )}
                          />
                        )}
                      </SelectTrigger>
                      <SelectContent>
                        {groups.map((g, idx) => (
                          <SelectGroup key={g.providerId}>
                            {idx > 0 && (
                              <div className="my-1 border-t border-[#f7f8fa] dark:border-surface-border" />
                            )}
                            <SelectLabel className="text-2xs font-medium text-ink-meta uppercase tracking-wide">
                              {g.providerName}
                            </SelectLabel>
                            {g.options.map((o) => (
                              <SelectItem key={o.key} value={o.key}>
                                {o.itemLabel}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Reasoning effort (kernel V5+bba3014 ModelSettings.effort).
                        Mirrors the Composer's EffortSelector -- 5 values
                        + "Default" (= null, let the runtime SDK pick).
                        Setting written here is applied to new sessions
                        that don't pass an explicit effort. */}
                  <div className="flex items-center gap-4 px-0 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink-heading">
                        {t(
                          "settings.model.thinkingDepth" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </div>
                      <div className="mt-0.5 text-2xs text-ink-meta">
                        {t(
                          "settings.model.thinkingDepthDesc" as Parameters<
                            typeof t
                          >[0],
                        )}
                      </div>
                    </div>
                    <Select
                      value={modelDefaults.default_effort}
                      onValueChange={(v) => {
                        void handleSetEffort(v as EffortLevel);
                      }}
                    >
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[200px] text-xs"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {DEFAULT_EFFORT_VALUES.map((key) => (
                          <SelectItem key={key} value={key}>
                            {t(`effort.${key}` as Parameters<typeof t>[0])}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              </Card>
            );
          })()}
        </>

        {/* Model-channel management — the whole section (header + list + Add
            button) is gated on ``configureModelChannel``. The commercial overlay
            flips this off when the org's ``member_custom_model_enabled`` policy
            is false, hiding the entire area; OSS single-run keeps it true.
            Also gated on ``providersListLoaded`` so the section doesn't flash an
            empty card during the initial page load. */}
        {configureModelChannel && providersListLoaded && (
          <>
            {/* -- Model channel list -- */}
            <div className="mt-6 mb-3 flex items-end justify-between">
              <div>
                <div className="text-sm font-medium text-ink-heading">
                  {t("settings.model.modelChannel" as Parameters<typeof t>[0])}
                </div>
                <div className="mt-0.5 text-xs text-ink-meta">
                  {t(
                    "settings.model.modelChannelDesc" as Parameters<
                      typeof t
                    >[0],
                  )}
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={() => setAddOpen(true)}
              >
                <Plus className="h-3.5 w-3.5" />
                {t("common.add" as Parameters<typeof t>[0])}
              </Button>
            </div>

            <Card className="rounded-xl shadow-xs">
              <CardContent className="px-5 py-0">
                {providersList
                  // Hide Valuz managed providers (Reportify) -- these are
                  // internal infrastructure, not user-visible models.
                  // All other providers are shown regardless of config state.
                  .filter((c) => c.source !== "managed")
                  .map((provider, idx) => {
                    const isManaged = provider.source === "managed";
                    // ADR-007: overlay-contributed system providers
                    // (e.g. Valuz system model). Read-only -- no edit /
                    // delete / test / CLI-login affordances.
                    const isSystem = provider.source === "system";
                    // CLI-OAuth providers (the CLI's
                    // own keychain holds the credential), so they read as
                    // ``credential_source === "none"`` from the API. Treat
                    // them as configured anyway -- the provider IS reachable.
                    const isConfigured =
                      provider.credential_source !== "none" ||
                      provider.auth_type === "oauth";

                    return (
                      <div
                        key={provider.id}
                        className={cn(
                          "flex items-center gap-3 border-b border-[#f7f8fa] px-0 py-3 last:border-b-0 dark:border-surface-border",
                          isManaged && "bg-brand/5",
                          !isConfigured && !isManaged && "opacity-60",
                          idx === 0 && "rounded-t-xl",
                        )}
                      >
                        {/* Icon */}
                        {isManaged ? (
                          <img
                            src={assetUrl("logo.png")}
                            alt="Valuz"
                            className="h-9 w-9 shrink-0 object-contain"
                          />
                        ) : (
                          <IconBox variant="outline" className="text-brand">
                            <Cpu className="h-4 w-4" />
                          </IconBox>
                        )}

                        {/* Info */}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-ink-heading">
                              {provider.name}
                            </span>
                            {isManaged && (
                              <Badge variant="metaBrand">
                                {t("settings.model.builtIn")}
                              </Badge>
                            )}
                            {isSystem && (
                              <Badge variant="metaBrand">
                                {t(
                                  "settings.model.systemProvider" as Parameters<
                                    typeof t
                                  >[0],
                                )}
                              </Badge>
                            )}
                            {provider.is_default && (
                              <Badge variant="outline">
                                {t("settings.model.default")}
                              </Badge>
                            )}
                          </div>
                          <div className="mt-0.5 flex items-center gap-3 text-2xs text-ink-meta">
                            {provider.auth_type !== "oauth" &&
                              maskApiKey(provider.credential_source) && (
                                <span className="font-mono">
                                  API Key:{" "}
                                  {maskApiKey(provider.credential_source)}
                                </span>
                              )}
                            {isManaged && isConfigured && (
                              <span>
                                {t("settings.model.modelFollowsPlan")}
                              </span>
                            )}
                            {!isManaged && provider.models.length > 0 && (
                              <span>
                                {t(
                                  "common.modelsCount" as Parameters<
                                    typeof t
                                  >[0],
                                  {
                                    count: String(provider.models.length),
                                  },
                                )}
                              </span>
                            )}
                            {(() => {
                              const compat = compatibleRuntimes(provider);
                              if (compat.length === 0) return null;
                              return (
                                <span>
                                  {t(
                                    "settings.model.availableFor" as Parameters<
                                      typeof t
                                    >[0],
                                  )}{" "}
                                  {compat
                                    .map((r) => RUNTIME_DISPLAY_NAME[r])
                                    .join(" · ")}
                                </span>
                              );
                            })()}
                          </div>
                        </div>

                        {/* Status badge */}
                        {isSystem && provider.enabled && (
                          <ModelAvailableBadge label={t("common.available")} />
                        )}
                        {isSystem && !provider.enabled && (
                          <Badge variant="outline" className="gap-1">
                            <Lock className="h-2.5 w-2.5" />
                            {provider.unavailable_reason ??
                              t("settings.model.notLoggedIn")}
                          </Badge>
                        )}
                        {isManaged && !isConfigured && (
                          <Badge variant="outline" className="gap-1">
                            <Lock className="h-2.5 w-2.5" />{" "}
                            {t("settings.model.needsConnection")}
                          </Badge>
                        )}
                        {isManaged && isConfigured && (
                          <ModelAvailableBadge label={t("common.available")} />
                        )}
                        {!isManaged && provider.test_status === "success" && (
                          <ModelAvailableBadge label={t("common.available")} />
                        )}
                        {!isManaged &&
                          provider.auth_type !== "oauth" &&
                          provider.test_status === "failed" && (
                            <Badge variant="error">
                              {t("settings.model.connectionFailed")}
                            </Badge>
                          )}
                        {!isManaged &&
                          provider.auth_type !== "oauth" &&
                          isConfigured &&
                          provider.test_status !== "success" &&
                          provider.test_status !== "failed" && (
                            <Badge variant="outline">
                              {t("common.notTested")}
                            </Badge>
                          )}
                        {!isManaged &&
                          provider.auth_type !== "oauth" &&
                          !isConfigured && (
                            <Badge variant="outline">
                              {t("common.notConfigured")}
                            </Badge>
                          )}
                        {!isManaged &&
                          provider.auth_type === "oauth" &&
                          (() => {
                            // OAuth subscription badges follow real CLI
                            // keychain state, not the seeded test_status.
                            // While detection is pending we render
                            // nothing to avoid flashing a wrong state.
                            const tool =
                              CLI_TOOL_BY_PROVIDER_KIND[provider.provider_kind];
                            const status = tool ? cliStatus[tool] : undefined;
                            if (!status) return null;
                            if (status.state === "unsupported") {
                              return (
                                <Badge variant="outline">
                                  {t(
                                    "settings.model.platformNotSupported" as Parameters<
                                      typeof t
                                    >[0],
                                  )}
                                </Badge>
                              );
                            }
                            if (!status.installed) {
                              return (
                                <Badge variant="outline" className="gap-1">
                                  <Lock className="h-2.5 w-2.5" />
                                  {t(
                                    "settings.model.notInstalled" as Parameters<
                                      typeof t
                                    >[0],
                                  )}
                                </Badge>
                              );
                            }
                            if (status.state === "logged_out") {
                              return (
                                <Badge variant="outline" className="gap-1">
                                  <Lock className="h-2.5 w-2.5" />
                                  {t(
                                    "settings.model.notLoggedIn" as Parameters<
                                      typeof t
                                    >[0],
                                  )}
                                </Badge>
                              );
                            }
                            return (
                              <ModelAvailableBadge
                                label={t("common.available")}
                              />
                            );
                          })()}

                        {/* Actions -- system providers are read-only. The hosted
                        "managed" (Reportify) provider path was removed with the
                        account OAuth subsystem; managed providers no longer
                        exist (also filtered out above). */}
                        {isSystem ? null : (
                          <>
                            {(() => {
                              // Show the CLI-login affordance only when
                              // it's actually needed: not installed or
                              // not logged in. Already-logged-in
                              // subscription rows hide the button so
                              // the row reads as "ready to go".
                              const tool =
                                CLI_TOOL_BY_PROVIDER_KIND[
                                  provider.provider_kind
                                ];
                              if (!tool) return null;
                              const status = cliStatus[tool];
                              if (!status) return null;
                              if (status.state === "unsupported") return null;
                              if (status.state === "logged_in") return null;
                              const label = status.installed
                                ? t("settings.model.cliLogin")
                                : t(
                                    "settings.model.installCli" as Parameters<
                                      typeof t
                                    >[0],
                                  );
                              return (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="gap-1.5 text-xs"
                                  onClick={() => {
                                    void cliLogin.trigger(tool);
                                  }}
                                >
                                  {label}
                                </Button>
                              );
                            })()}
                            <Button
                              variant="outline"
                              size="sm"
                              className="gap-1.5 text-xs"
                              onClick={() => {
                                void handleOpenEdit(provider.id);
                              }}
                            >
                              <FilePenLine className="h-3 w-3" />
                              {t("common.edit")}
                            </Button>
                            {provider.auth_type !== "oauth" && (
                              <Button
                                variant="outline"
                                size="sm"
                                aria-label={t("common.refresh")}
                                className="h-8 w-8 p-0 text-[#131313] hover:text-[#131313]"
                                onClick={() => {
                                  void handleTestProvider(provider.id);
                                }}
                              >
                                <RefreshCw className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            {/* Subscription channels (auth_type "oauth") are not
                            user-deletable: their availability mirrors the CLI
                            login state (auto-materialized on login, gone when the
                            user logs the CLI out), so a delete button here would
                            be futile — the channel reappears on the next login
                            probe. Keeps codex and Claude symmetric. Only
                            user-added api_key channels get the trash action. */}
                            {provider.deletable &&
                              provider.auth_type !== "oauth" && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  aria-label={t("common.delete")}
                                  className="h-8 w-8 p-0 text-[#131313] hover:text-[#f54b4b]"
                                  onClick={() => setDeleteTarget(provider)}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              )}
                          </>
                        )}
                      </div>
                    );
                  })}
              </CardContent>
            </Card>
          </>
        )}
      </SettingsSection>

      {/* Provider Add Dialog */}
      <ProviderAddDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        providers={providers
          // OAuth-typed providers (claude-subscription, codex-subscription)
          // are reached through the ProviderConnectionPicker's OAuth cards
          // -- surfacing them here in the api_key form's provider dropdown
          // would let the user pick "Claude (订阅)" then be asked for an
          // API key, which makes no sense.
          .filter((p) => p.auth_type !== "oauth")
          .map((p): ProviderOption => {
            const displayNames: Record<string, string> = {
              anthropic: "Anthropic (Claude)",
              openai: "OpenAI (ChatGPT)",
              deepseek: "DeepSeek",
              openrouter: "OpenRouter",
              moonshot: "Moonshot (Kimi)",
              minimax: "MiniMax",
              compatible: t("common.custom" as Parameters<typeof t>[0]),
            };
            return {
              kind: p.kind,
              display_name: displayNames[p.kind] ?? p.display_name,
              supports_custom_base_url: p.supports_custom_base_url,
              supports_protocol_selection: p.supports_protocol_selection,
              default_base_url: p.default_base_url,
              anthropic_base_url: p.anthropic_base_url,
              default_model: p.default_model,
              model_options: p.model_options,
              docs_url: p.docs_url,
            };
          })}
        onProbeModels={(payload) =>
          providersApi.probeModels({
            provider_kind: payload.provider_kind,
            api_key: payload.api_key,
            base_url: payload.base_url,
            protocol: payload.protocol,
          })
        }
        onPing={(payload) =>
          providersApi.ping({
            base_url: payload.base_url,
            api_key: payload.api_key,
            protocol: payload.protocol ?? null,
            models: payload.models,
          })
        }
        onCreate={(payload) => handleAddProvider(payload)}
      />

      {/* Provider Edit Dialog */}
      {editProvider && (
        <ProviderEditDialog
          // Force a fresh instance per provider so switching which row is edited
          // never carries another provider's in-progress endpoint/key state.
          key={editProvider.id}
          open={editProvider !== null}
          onOpenChange={(v) => {
            if (!v) setEditProvider(null);
          }}
          providerId={editProvider.id}
          providerName={editProvider.name}
          providerKind={editProvider.provider_kind}
          providerDisplayName={
            editProvider.provider_kind === "compatible"
              ? t("common.custom" as Parameters<typeof t>[0])
              : (providers.find((p) => p.kind === editProvider.provider_kind)
                  ?.display_name ?? editProvider.provider_kind)
          }
          currentBaseUrl={editProvider.base_url ?? ""}
          currentProtocol={editProvider.protocol ?? null}
          initialModels={editProvider.models.map((m) => m.id)}
          supportsCustomBaseUrl={editProvider.supports_custom_base_url}
          supportsProtocolSelection={
            providers.find((p) => p.kind === editProvider.provider_kind)
              ?.supports_protocol_selection ?? false
          }
          docsUrl={
            providers.find((p) => p.kind === editProvider.provider_kind)
              ?.docs_url ?? ""
          }
          defaultBaseUrl={
            providers.find((p) => p.kind === editProvider.provider_kind)
              ?.default_base_url ?? ""
          }
          authType={editProvider.auth_type}
          onSave={handleSaveEdit}
          onDiscoverModels={async (id) => {
            const result = await providersApi.discoverModels(id);
            // Refresh channel list in the background so the row badge
            // reflects the new model_options without blocking the
            // dialog feedback.
            void loadProviders();
            return {
              models: result.merged,
              model_labels: result.model_labels,
            };
          }}
          onPing={(payload) =>
            providersApi.ping({
              base_url: payload.base_url,
              api_key: payload.api_key ?? null,
              protocol: payload.protocol ?? null,
              models: payload.models,
              provider_id: payload.provider_id ?? null,
            })
          }
        />
      )}

      {/* Provider Delete Confirm */}
      <DeleteConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(v) => {
          if (!v) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? t("settings.model.deleteConfirmTitle", {
                name: deleteTarget.name,
              })
            : t("common.confirm")
        }
        description={t("settings.model.deleteConfirmDesc")}
        confirmLabel={t("common.delete")}
        loading={deleting}
        onConfirm={() => {
          void handleDeleteProvider();
        }}
      />

      {cliLogin.dialog}
    </>
  );
};
