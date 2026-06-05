/**
 * Single source of truth for runtime ↔ provider compatibility.
 *
 * The rules here drive three call sites and they all need to agree:
 *  1. Composer / quick-chat model picker (useComposerProviders).
 *  2. Settings → Default-config card (Connection dropdown is filtered by
 *     the chosen Runtime).
 *  3. Settings → Provider list (each row shows "可用于：X / Y" so the
 *     user can tell at a glance which runtimes a freshly-added DeepSeek
 *     channel will work with).
 *
 * Compatibility is NOT just "protocol matches". Two additional rules:
 *  - codex runtime can only authenticate to ``codex-subscription``.
 *    The codex CLI reads its own keychain and won't accept a plain
 *    OpenAI API key, so listing api-key OpenAI providers under codex is
 *    a UX trap.
 *  - deepagents (Valuz Agent) reads credentials from the provider row
 *    directly. It can drive any api-key provider regardless of wire
 *    shape, BUT it can't authenticate to subscription providers
 *    (``claude-subscription`` / ``codex-subscription``) because those
 *    tokens live in the provider CLI's keychain, not on the host.
 */

import type {
  ApiProtocol,
  ProviderDetail,
  ProviderListItem,
} from "./providers-api";
import {
  ALLOWED_PROTOCOLS_BY_RUNTIME,
  type RuntimeKey,
} from "./runtime-protocols";
import type { RuntimeId } from "./sessions-api";

const SUBSCRIPTION_PROVIDER_KINDS = new Set([
  "claude-subscription",
  "codex-subscription",
]);

const ANTHROPIC_PROVIDER_KINDS = new Set(["anthropic", "claude-subscription"]);

/** Subset of a provider this module needs. Accepts both ProviderListItem
 *  and ProviderDetail, plus any future shape that carries these fields. */
type CompatProvider = Pick<
  ProviderListItem,
  "provider_kind" | "protocol" | "effective_protocol" | "compatible_protocols"
>;

/**
 * Legacy 2-value protocol values for back-compat with pre-bba3014
 * payloads. Older backends emit ``"openai" | "anthropic"`` instead of
 * the 4-value hyphen form; we widen the check accordingly when the row
 * still uses the legacy shape.
 */
const ALL_OPENAI_HYPHEN_PROTOCOLS: ReadonlySet<string> = new Set([
  "openai",
  "openai-completion",
  "openai-response",
]);

const speaksAnyProtocolFrom = (
  p: CompatProvider,
  allowed: readonly ApiProtocol[],
): boolean => {
  if (p.compatible_protocols && p.compatible_protocols.length > 0) {
    return p.compatible_protocols.some((proto) =>
      (allowed as readonly string[]).includes(proto),
    );
  }
  // Legacy fallback for callers/payloads predating ``compatible_protocols``.
  // Map the row's bare ``protocol`` value (or ``provider_kind``) onto the
  // 4-value allowlist as best we can.
  const raw = (p.protocol ?? "").toLowerCase();
  if (raw === "anthropic") return allowed.includes("anthropic");
  if (ALL_OPENAI_HYPHEN_PROTOCOLS.has(raw)) {
    // Legacy bare "openai" → openai-completion (DeepAgents-compatible).
    const inferred: ApiProtocol =
      raw === "openai-response" ? "openai-response" : "openai-completion";
    return allowed.includes(inferred);
  }
  if (raw === "gemini") return allowed.includes("gemini");
  if (ANTHROPIC_PROVIDER_KINDS.has(p.provider_kind)) {
    return allowed.includes("anthropic");
  }
  return allowed.includes("openai-completion");
};

/** Decide whether a single (provider, runtime) pair can actually run.
 *  Mirrors the filter inside useComposerProviders so all three picker
 *  surfaces stay in lock-step. Now driven by the backend's
 *  ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` 4-value allowlist
 *  (kernel V5+bba3014). */
export const isProviderRuntimeCompatible = (
  provider: CompatProvider,
  runtime: RuntimeId,
): boolean => {
  if (runtime === "deepagents") {
    // Excludes subscription-only providers — their CLI keychain isn't
    // accessible to the deepagents (Valuz Agent) runtime. Otherwise any
    // wire shape the runtime's allowlist accepts is fair game.
    if (SUBSCRIPTION_PROVIDER_KINDS.has(provider.provider_kind)) {
      return false;
    }
    return speaksAnyProtocolFrom(
      provider,
      ALLOWED_PROTOCOLS_BY_RUNTIME.deepagents,
    );
  }
  if (runtime === "claude_agent") {
    // Claude Code SDK only sends anthropic-shape requests, so the
    // provider has to be able to speak anthropic — even a DeepSeek that
    // happens to expose both shapes qualifies.
    return speaksAnyProtocolFrom(
      provider,
      ALLOWED_PROTOCOLS_BY_RUNTIME.claude_agent,
    );
  }
  // codex: only its own subscription. The codex CLI walks its own
  // keychain and cannot drive a bare OpenAI api_key endpoint.
  return provider.provider_kind === "codex-subscription";
};

// Type-only assertion: the runtime allowlist matches the 4-value
// ``ApiProtocol`` enum. ``RuntimeKey`` is imported but otherwise unused;
// this satisfies-cast keeps the import live for the type system.
const _ALLOWLIST_TYPE_CHECK: Record<RuntimeKey, readonly ApiProtocol[]> =
  ALLOWED_PROTOCOLS_BY_RUNTIME;
void _ALLOWLIST_TYPE_CHECK;

const ALL_RUNTIMES: RuntimeId[] = ["claude_agent", "codex", "deepagents"];

/** Every runtime this provider could plausibly run on. Used by the
 *  Settings → Providers list to render "可用于" badges. */
export const compatibleRuntimes = (p: CompatProvider): RuntimeId[] =>
  ALL_RUNTIMES.filter((r) => isProviderRuntimeCompatible(p, r));

export const RUNTIME_DISPLAY_NAME: Record<RuntimeId, string> = {
  claude_agent: "Claude Code",
  codex: "OpenAI Codex",
  deepagents: "Deep Agents",
};

/** Helper for callers that have a ProviderDetail (model_options known)
 *  and need the same filter as useComposerProviders flattened in line.
 *  Returns ``true`` if the provider has at least one model that could
 *  run on the given runtime. */
export const providerHasUsableModelForRuntime = (
  p: ProviderDetail,
  runtime: RuntimeId,
): boolean => {
  if (!isProviderRuntimeCompatible(p, runtime)) return false;
  return p.model_options.length > 0 || !!p.default_model;
};
