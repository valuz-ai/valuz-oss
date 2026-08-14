import { useEffect, useMemo, useState } from "react";
import type { LLMChannel } from "../api/providers-api";
import { getComposerCatalogAdapter } from "../edition/composer-catalog";

/** Runtime identifiers used by the runtime filter. Server-resolved onto each
 *  model's ``runtimes`` — not re-derived here. */
export type RuntimeProvider =
  | "claude_agent"
  | "codex"
  | "deepagents"
  | "deepseek_harness";

export type ComposerProviderChannelStatus = "loading" | "ready" | "error";

export interface ComposerProviderChannelState {
  providers: LLMChannel[];
  status: ComposerProviderChannelStatus;
}

/**
 * A provider is "usable" when picking it in the model dropdown could actually
 * run a turn. The frontend can't fully prove this — only the backend knows
 * whether the OAuth keychain is logged in — but it can cheaply reject the
 * obvious dead-ends:
 *
 * - ``credential_source == "secret_ref"``: the user configured an API key. Usable.
 * - ``credential_source == "account_connection"``: linked OAuth account. Usable.
 * - ``credential_source == "none"`` + ``auth_type == "oauth"``: OAuth
 *   subscription provider (claude /login, codex /login) — usable once logged in.
 * - ``credential_source == "none"`` + ``auth_type == "api_key"``: no credentials
 *   configured. Always 422 → hide.
 *
 * Exported because the Settings → Providers list applies the same rule. REP-107.
 */
export const providerHasUsableCredentials = (
  c: Pick<LLMChannel, "credential_source" | "auth_type">,
): boolean => {
  if (c.credential_source === "secret_ref") return true;
  if (c.credential_source === "account_connection") return true;
  if (c.auth_type === "oauth") return true;
  return false;
};

/**
 * Load the composer's server-resolved model channels through the active
 * edition's catalog adapter. OSS treats ``targetId`` as opaque and its default
 * adapter always uses the providers API's module default.
 *
 * Switching scopes clears the previous list immediately. An obsolete response
 * is ignored if it resolves later, so a slower old-scope request can never
 * overwrite the active scope.
 */
export const useComposerProviderChannelState = (
  targetId?: string | null,
): ComposerProviderChannelState => {
  const adapter = getComposerCatalogAdapter();
  const scopeKey = adapter.getScopeKey({ targetId });
  const [loaded, setLoaded] = useState<{
    scopeKey: string;
    providers: LLMChannel[];
    status: ComposerProviderChannelStatus;
  }>({ scopeKey, providers: [], status: "loading" });
  let current = loaded;
  if (loaded.scopeKey !== scopeKey) {
    // Adjust during render so a scope switch can never paint the previous
    // scope's catalog as current. React discards this render and immediately
    // retries with the loading state before committing the UI.
    current = { scopeKey, providers: [], status: "loading" };
    setLoaded(current);
  }

  useEffect(() => {
    let active = true;

    void adapter
      .listProviderChannels({ targetId })
      .then(({ providers: channels }) => {
        if (active) {
          setLoaded({
            scopeKey,
            providers: channels.filter((channel) => channel.enabled),
            status: "ready",
          });
        }
      })
      .catch(() => {
        if (active) {
          setLoaded({ scopeKey, providers: [], status: "error" });
        }
      });

    return () => {
      active = false;
    };
  }, [adapter, scopeKey, targetId]);

  return { providers: current.providers, status: current.status };
};

/** Compatibility wrapper for consumers that only need the current channels. */
export const useComposerProviderChannels = (targetId?: string | null) =>
  useComposerProviderChannelState(targetId).providers;

/**
 * Transforms enabled ``LLMChannel[]`` (from the gated list —
 * ``providersApi.list({gated: true})``, one request, server-side
 * subscription-login gate) into flat ``ModelSelectorItem[]`` for the
 * composer / agent model selector, keeping only the (provider, model)
 * pairs whose model can run on ``runtimeFilter``.
 *
 * Runtime compatibility is read verbatim from ``model.runtimes`` — server-resolved
 * via ``runtimes_for`` (see docs/design/runtime-model-compat-single-source.md).
 * The hook no longer re-derives it from ``protocol`` / ``provider_kind``:
 * subscription exclusion, dual-protocol channels, and the openai-response→codex
 * rule are all already encoded in that field. A channel that resolves to a single
 * ``default_model`` is materialized backend-side as one model row (carrying
 * ``runtimes``), so there is no client-side fallback here.
 *
 * ``runtimeFilter`` undefined = pre-runtime-picker fallback: every credentialed
 * model. API-key providers with no credentials are dropped (they always 422 at
 * session-create, so surfacing them is pure noise).
 */
export const useComposerProviders = (
  providers: LLMChannel[],
  runtimeFilter?: RuntimeProvider,
) =>
  useMemo(
    () =>
      providers
        .filter((c) => c.enabled)
        .filter(providerHasUsableCredentials)
        .flatMap((c) =>
          c.models
            .filter(
              (m) =>
                !runtimeFilter || (m.runtimes ?? []).includes(runtimeFilter),
            )
            .map((m) => ({
              providerId: c.id,
              providerName: c.name,
              modelId: m.id,
              isDefault: c.is_default && m.id === c.default_model,
              source: c.source,
            })),
        ),
    [providers, runtimeFilter],
  );
