/**
 * Runtime-toggleable capability flags — "is this action allowed in the current
 * install?" Default profile turns everything on; OSS itself has no notion of
 * who would turn one off. Overlay editions (commercial / industry) flip them
 * based on org policy, license tier, etc.
 *
 * Distinct from `FeatureFlags`: a feature flag gates whether a whole module
 * (conversation, projects, …) is even present; a capability gates whether a
 * concrete action entry point (button / menu item) is exposed inside an
 * otherwise-enabled module.
 *
 * The `useCapabilities()` hook + `setCapabilities()` mutator are exported
 * from `./registry-store` to keep this file dependency-free (avoids a cycle
 * with personal-profile → capabilities → registry-store → personal-profile).
 */
export interface Capabilities {
  /**
   * Whether the user can add / edit / delete / test their own model provider
   * channels in Settings → Model. CLI-login flows for subscription providers
   * are NOT gated here — those credentials live in the CLI's own keychain
   * and would be reachable from a terminal anyway, so gating the in-app
   * button would only create UI inconsistency without enforcing anything.
   * System / managed providers are always read-only and unaffected.
   */
  configureModelChannel: boolean;
}

export const DEFAULT_CAPABILITIES: Capabilities = {
  configureModelChannel: true,
};
