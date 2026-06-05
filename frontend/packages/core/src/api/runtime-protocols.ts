/**
 * Per-runtime API-protocol allowlist (kernel V5+bba3014).
 *
 * Mirrors ``backend/src/runtimes/factory.ALLOWED_PROTOCOLS_BY_RUNTIME``
 * verbatim. The backend route validator 400s any session create / patch
 * that pairs a runtime with a protocol outside its allowlist; this
 * frontend mirror lets the New Session / Edit Capabilities dialogs
 * filter the protocol dropdown so the user can't pick an invalid
 * combination in the first place.
 *
 * Uses the user-facing hyphen form (``openai-completion`` /
 * ``openai-response``). The backend's ``provider_resolver`` translates
 * to the kernel's underscore form at the adapter seam.
 */
import type { ApiProtocol } from "./providers-api";

export type RuntimeKey = "claude_agent" | "codex" | "deepagents";

export const ALLOWED_PROTOCOLS_BY_RUNTIME: Record<
  RuntimeKey,
  readonly ApiProtocol[]
> = {
  claude_agent: ["anthropic"] as const,
  codex: ["openai-response"] as const,
  deepagents: ["anthropic", "openai-completion", "gemini"] as const,
} as const;

/**
 * The default api_protocol for a runtime — the first entry of its
 * allowlist. New Session dialog calls this when the user switches
 * runtime so the protocol field auto-resets to a valid value (rather
 * than carrying over a stale incompatible choice).
 */
export function defaultProtocolFor(runtime: RuntimeKey): ApiProtocol {
  return ALLOWED_PROTOCOLS_BY_RUNTIME[runtime][0];
}

/**
 * Whether ``protocol`` is allowed for ``runtime``. Mirrors the backend
 * ``validate_api_protocol`` check.
 */
export function isProtocolAllowed(
  runtime: RuntimeKey,
  protocol: ApiProtocol,
): boolean {
  return (
    ALLOWED_PROTOCOLS_BY_RUNTIME[runtime] as readonly ApiProtocol[]
  ).includes(protocol);
}
