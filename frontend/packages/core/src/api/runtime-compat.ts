/**
 * Runtime ↔ provider compatibility for the three Settings surfaces that need it:
 *  1. Composer / quick-chat model picker (useComposerProviders).
 *  2. Settings → Default-config card (Connection dropdown filtered by Runtime).
 *  3. Settings → Provider list ("可用于：X / Y" badges).
 *
 * Compatibility is read verbatim from each model's server-resolved ``runtimes``
 * (derived once in the backend ``runtimes_for``; see
 * docs/design/runtime-model-compat-single-source.md). This module no longer
 * re-derives it from ``protocol`` / ``provider_kind`` — a provider is compatible
 * with a runtime iff at least one of its models declares that runtime.
 */

import type { LLMChannel, LLMChannelDetail } from "./providers-api";
import type { RuntimeId } from "./sessions-api";

/** Subset of a provider this module needs — accepts both LLMChannel and
 *  LLMChannelDetail (both carry per-model ``runtimes``). */
type CompatProvider = Pick<LLMChannel, "models">;

/** Whether a provider can run on ``runtime`` — true iff one of its models
 *  declares it. Mirrors the model-level filter in useComposerProviders. */
export const isProviderRuntimeCompatible = (
  provider: CompatProvider,
  runtime: RuntimeId,
): boolean => provider.models.some((m) => (m.runtimes ?? []).includes(runtime));

const ALL_RUNTIMES: RuntimeId[] = [
  "claude_agent",
  "codex",
  "deepagents",
  "deepseek_harness",
];

/** Every runtime this provider could plausibly run on — the union of its
 *  models' ``runtimes``. Used by the Settings → Providers list "可用于" badges. */
export const compatibleRuntimes = (p: CompatProvider): RuntimeId[] =>
  ALL_RUNTIMES.filter((r) => isProviderRuntimeCompatible(p, r));

export const RUNTIME_DISPLAY_NAME: Record<RuntimeId, string> = {
  claude_agent: "Claude Code",
  codex: "OpenAI Codex",
  deepagents: "Deep Agents",
  deepseek_harness: "DeepSeek Harness",
};

/** Whether ``p`` has at least one model that could run on ``runtime``. Kept as a
 *  named helper for callers holding a full ``LLMChannelDetail``. */
export const providerHasUsableModelForRuntime = (
  p: LLMChannelDetail,
  runtime: RuntimeId,
): boolean => isProviderRuntimeCompatible(p, runtime);
