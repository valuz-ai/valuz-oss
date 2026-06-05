import { useMemo } from "react";
import type { ProviderDetail } from "../api/providers-api";

/** Runtime identifiers used by the runtime filter. Derived server-side
 *  from provider_kind / protocol — not carried on the API wire anymore. */
export type RuntimeProvider = "claude_agent" | "codex" | "deepagents";

/**
 * A provider is "usable" when picking it in the model dropdown could
 * actually run a turn. The frontend can't fully prove this — only the
 * backend knows whether the OAuth keychain is logged in — but it can
 * cheaply reject the obvious dead-ends:
 *
 * - ``credential_source == "secret_ref"``: the user configured an API
 *   key. Usable.
 * - ``credential_source == "account_connection"``: linked to an OAuth
 *   account (e.g. Reportify). Usable while connected; the picker still
 *   shows it so the user can revisit the linked-account UI rather than
 *   hide the option.
 * - ``credential_source == "none"`` + ``auth_type == "oauth"``: OAuth
 *   subscription provider (claude /login, codex /login). The host
 *   doesn't store credentials but the runtime SDK reads the CLI's
 *   keychain — so it's usable as long as the user has logged in.
 * - ``credential_source == "none"`` + ``auth_type == "api_key"``: no
 *   credentials configured. Always picks 422 → hide.
 *
 * Exported because the Settings → Providers list applies the same rule
 * (an unconfigured api_key provider is just noise — picking it goes
 * nowhere). REP-107.
 */
export const providerHasUsableCredentials = (
  c: Pick<ProviderDetail, "credential_source" | "auth_type">,
): boolean => {
  if (c.credential_source === "secret_ref") return true;
  if (c.credential_source === "account_connection") return true;
  if (c.auth_type === "oauth") return true;
  return false;
};

/**
 * OAuth-subscription provider kinds — providers backed by these can
 * only run on their dedicated CLI runtime (``claude_agent`` /
 * ``codex``) because authentication lives in that CLI's keychain.
 * They're hidden from the picker for runtime=deepagents (Valuz
 * Agent), which expects an api_key it can read.
 */
const SUBSCRIPTION_PROVIDER_KINDS = new Set([
  "claude-subscription",
  "codex-subscription",
]);

/** Whether the provider can drive an anthropic-shape session. Trusts
 * the backend's ``compatible_protocols`` first — that field is built
 * from the descriptor's ``supports_protocol_selection`` flag and so
 * correctly marks DeepSeek / 智谱 (GLM) / Moonshot / MiniMax as
 * anthropic capable. Falls back to the row's ``protocol`` /
 * ``provider_kind`` for old API responses that pre-date the field. */
const ANTHROPIC_PROVIDER_KINDS = new Set(["anthropic", "claude-subscription"]);

const canDriveAnthropic = (
  c: Pick<
    ProviderDetail,
    "compatible_protocols" | "protocol" | "provider_kind"
  >,
): boolean => {
  if (c.compatible_protocols && c.compatible_protocols.length > 0) {
    return c.compatible_protocols.includes("anthropic");
  }
  // Legacy fallback (pre-bba3014). Hyphen-form openai-* and bare
  // "openai" both signal "not anthropic".
  if (c.protocol === "anthropic") return true;
  if (
    c.protocol === "openai" ||
    c.protocol === "openai-completion" ||
    c.protocol === "openai-response" ||
    c.protocol === "gemini"
  ) {
    return false;
  }
  return ANTHROPIC_PROVIDER_KINDS.has(c.provider_kind);
};

/**
 * Transforms enabled ProviderDetail[] into flat ModelSelectorItem[]
 * for the Composer model selector dropdown.
 *
 * REP-107 + follow-up: ``runtimeFilter`` controls which providers are
 * surfaced.
 * - ``claude_agent`` / ``codex``: only providers bound to that exact
 *   runtime — those SDKs each speak one wire shape and won't talk to
 *   anything else.
 * - ``deepagents`` (Valuz Agent): every credentialed provider EXCEPT
 *   the OAuth-subscription ones (``claude-subscription`` /
 *   ``codex-subscription``). Valuz Agent is wire-shape-flexible (both
 *   OpenAI and Anthropic) and reads credentials from the provider row,
 *   so it can drive any api_key provider — but it can't authenticate
 *   to a subscription provider because that token lives inside the
 *   provider CLI's keychain, not the host.
 * - ``undefined``: pre-runtime-picker fallback — flatten everything
 *   credentialed.
 *
 * API-key providers with no credentials configured (``credential_source ==
 * "none"`` + ``auth_type == "api_key"``) are filtered out: choosing
 * them would always 422 at session-creation, so showing them as picker
 * options is pure noise. The user can re-add them by configuring a key
 * in Settings → Providers.
 */
export const useComposerProviders = (
  providers: ProviderDetail[],
  runtimeFilter?: RuntimeProvider,
) =>
  useMemo(
    () =>
      providers
        .filter((c) => c.enabled)
        .filter(providerHasUsableCredentials)
        .filter((c) => {
          if (!runtimeFilter) return true;
          if (runtimeFilter === "deepagents") {
            return !SUBSCRIPTION_PROVIDER_KINDS.has(c.provider_kind);
          }
          if (runtimeFilter === "claude_agent") {
            // ``compatible_protocols`` is the source of truth — backed
            // by the descriptor's ``supports_protocol_selection`` flag
            // server-side, so DeepSeek / 智谱 / Moonshot / MiniMax all
            // surface here (they speak anthropic via ``<base>/anthropic``)
            // alongside the single-protocol anthropic providers and the
            // Claude Pro / Max subscription.
            return canDriveAnthropic(c);
          }
          // codex: only codex-subscription — the codex CLI reads its
          // own keychain (codex /login) and cannot authenticate to
          // Anthropic's API, so claude-subscription is excluded.
          return c.provider_kind === "codex-subscription";
        })
        .flatMap((c) => {
          const models =
            c.model_options.length > 0
              ? c.model_options
              : c.default_model
                ? [c.default_model]
                : [];
          return models.map((m) => ({
            providerId: c.id,
            providerName: c.name,
            modelId: m,
            isDefault: c.is_default && m === c.default_model,
            source: c.source,
          }));
        }),
    [providers, runtimeFilter],
  );
