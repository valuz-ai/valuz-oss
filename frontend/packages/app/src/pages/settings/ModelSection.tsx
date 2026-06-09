import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import {
  Check,
  Cpu,
  FilePenLine,
  Lock,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
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
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import {
  useCliLoginFlow,
  type CliTool,
  type CliLoginStatus,
} from "@valuz/app/components";
import { usePlatform } from "@valuz/app/platform";
import {
  providersApi,
  runtimesApi,
  settingsApi,
  isProviderRuntimeCompatible,
  compatibleRuntimes,
  RUNTIME_DISPLAY_NAME,
  DEFAULT_EFFORT_VALUES,
  EFFORT_FALLBACK,
  type ProviderListItem,
  type ProviderDetail,
  type ProviderDescriptor,
  type RuntimeListItem,
  type ModelDefaults,
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

export const ModelSection = () => {
  const { t } = useTranslation();
  const { checkCliLogin: platformCheckCliLogin } = usePlatform();

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
  const [providersList, setProvidersList] = useState<ProviderListItem[]>([]);
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<ProviderDetail | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProviderListItem | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  const loadProvidersList = useCallback(async () => {
    try {
      const res = await providersApi.list();
      // Hide:
      // - managed (Reportify) -- those live under the Connectors module.
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
            (p.credential_source !== "none" || p.auth_type === "oauth"),
        ),
      );
    } catch {
      // silently fail
    }
  }, []);

  const cliLogin = useCliLoginFlow({
    onProviderMarkedOAuth: () => {
      void loadProvidersList();
    },
  });

  const loadProviders = useCallback(async () => {
    try {
      const res = await providersApi.listProviders();
      setProviders(res.providers);
    } catch {
      // silently fail
    }
  }, []);

  useEffect(() => {
    void loadProvidersList();
  }, [loadProvidersList]);
  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

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
    let cancelled = false;
    void (async () => {
      try {
        const [claude, codex] = (await Promise.all([
          platformCheckCliLogin!("claude"),
          platformCheckCliLogin!("codex"),
        ])) as [CliLoginStatus, CliLoginStatus];
        if (!cancelled) setCliStatus({ claude, codex });
      } catch {
        // best-effort -- stale cliStatus is fine
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [providersList]);

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
    runtime_provider?: "claude_agent" | "codex" | "deepagents";
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

  return (
    <>
      <SettingsSection
        title={t("settings.model.title")}
        desc={t("settings.model.desc")}
      >
        {/* No provider warning */}
        {providersList.filter((c) => c.enabled).length === 0 && (
          <div className="mt-5 flex items-center gap-3 rounded-xl border border-error-text/20 bg-error-light px-4 py-3 text-xs text-error-text">
            <Lock className="h-4 w-4" />
            <span className="flex-1">{t("settings.model.noModelWarning")}</span>
            <Button size="sm" onClick={() => setAddOpen(true)}>
              {t("settings.model.configureModel")}
            </Button>
          </div>
        )}

        {/* -- Default card -- */}
        {providersList.filter((c) => c.enabled).length > 0 && (
          <>
            <div className="mt-5 mb-3">
              <div className="text-sm font-medium text-ink-heading">
                {t("settings.model.defaultConfig" as Parameters<typeof t>[0])}
              </div>
              <div className="mt-0.5 text-xs text-ink-meta">
                {t(
                  "settings.model.defaultConfigDesc" as Parameters<typeof t>[0],
                )}
              </div>
            </div>
            {(() => {
              type ModelOption = {
                key: string;
                providerId: string;
                providerName: string;
                modelId: string;
                itemLabel: string;
              };
              const usableProviders = providersList.filter(
                (p) =>
                  p.enabled &&
                  (p.credential_source !== "none" || p.auth_type === "oauth") &&
                  isProviderRuntimeCompatible(p, modelDefaults.default_runtime),
              );
              const allOptions: ModelOption[] = [];
              const groups: {
                providerId: string;
                providerName: string;
                options: ModelOption[];
              }[] = [];
              for (const p of usableProviders) {
                const models =
                  p.model_options.length > 0
                    ? p.model_options
                    : p.auth_type === "oauth"
                      ? [""]
                      : p.default_model
                        ? [p.default_model]
                        : [];
                const groupOptions: ModelOption[] = [];
                if (models.length === 0) {
                  groupOptions.push({
                    key: `${p.id}::__nomodel__`,
                    providerId: p.id,
                    providerName: p.name,
                    modelId: "",
                    itemLabel: t(
                      "settings.model.discoveredModels" as Parameters<
                        typeof t
                      >[0],
                    ),
                  });
                } else {
                  for (const m of models) {
                    // Prefer the admin-supplied display label when the provider
                    // surfaces one (overlay-contributed system channels do —
                    // user api_key providers' model_labels is ``{}``). Wire-level
                    // ``modelId`` is untouched.
                    const labeled = p.model_labels?.[m];
                    groupOptions.push({
                      key: `${p.id}::${m}`,
                      providerId: p.id,
                      providerName: p.name,
                      modelId: m,
                      itemLabel:
                        labeled ||
                        m ||
                        t(
                          "settings.model.followSubscription" as Parameters<
                            typeof t
                          >[0],
                        ),
                    });
                  }
                }
                allOptions.push(...groupOptions);
                groups.push({
                  providerId: p.id,
                  providerName: p.name,
                  options: groupOptions,
                });
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
                      {selectedOption && (
                        <span className="shrink-0 truncate text-2xs text-ink-meta">
                          {selectedOption.providerName}
                        </span>
                      )}
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
        )}

        {/* -- Model channel list -- */}
        <div className="mt-6 mb-3 flex items-end justify-between">
          <div>
            <div className="text-sm font-medium text-ink-heading">
              {t("settings.model.modelChannel" as Parameters<typeof t>[0])}
            </div>
            <div className="mt-0.5 text-xs text-ink-meta">
              {t("settings.model.modelChannelDesc" as Parameters<typeof t>[0])}
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
                        src="./logo.png"
                        alt="Valuz"
                        className="h-9 w-9 shrink-0 object-contain"
                      />
                    ) : (
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-surface-border bg-surface-soft text-brand">
                        <Cpu className="h-4 w-4" />
                      </div>
                    )}

                    {/* Info */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-ink-heading">
                          {provider.name}
                        </span>
                        {isManaged && (
                          <Badge variant="brand">
                            {t("settings.model.builtIn")}
                          </Badge>
                        )}
                        {isSystem && (
                          <Badge variant="brand">
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
                              API Key: {maskApiKey(provider.credential_source)}
                            </span>
                          )}
                        {isManaged && isConfigured && (
                          <span>{t("settings.model.modelFollowsPlan")}</span>
                        )}
                        {!isManaged && provider.model_options.length > 0 && (
                          <span>
                            {t(
                              "common.modelsCount" as Parameters<typeof t>[0],
                              {
                                count: String(provider.model_options.length),
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
                      <Badge variant="success" className="gap-1">
                        <Check className="h-2.5 w-2.5" />{" "}
                        {t("common.available")}
                      </Badge>
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
                      <Badge variant="success">
                        <Check className="h-2.5 w-2.5" />{" "}
                        {t("common.available")}
                      </Badge>
                    )}
                    {!isManaged && provider.test_status === "success" && (
                      <Badge variant="success">
                        <Check className="h-2.5 w-2.5" />{" "}
                        {t("common.available")}
                      </Badge>
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
                        <Badge variant="outline">{t("common.notTested")}</Badge>
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
                          <Badge variant="success" className="gap-1">
                            <Check className="h-2.5 w-2.5" />{" "}
                            {t("common.available")}
                          </Badge>
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
                            CLI_TOOL_BY_PROVIDER_KIND[provider.provider_kind];
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
                        {provider.deletable && (
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
          initialModels={editProvider.model_options}
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
            return { models: result.merged };
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
