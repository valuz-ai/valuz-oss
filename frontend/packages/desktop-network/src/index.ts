/**
 * @valuz/desktop-network — the Electron main-process side of the unified
 * network egress (docs/design/unified-network-egress.md).
 *
 * This package is deliberately free of Electron and third-party imports:
 * it only needs Node built-ins, so any desktop shell that spawns the valuz
 * backend as a sidecar (the OSS desktop, a commercial overlay's desktop, …)
 * can own the egress the same way. The shell wires:
 *
 *   - ``EgressManager`` — create once, ``start()`` before the first sidecar
 *     spawn, hand ``getBootstrap()`` to the sidecar over stdin, ``quiesce()``
 *     / ``stop()`` on shutdown, ``setMode()`` from the settings UI;
 *   - ``readPersistedEgressMode`` / ``writePersistedEgressMode`` — the
 *     user's mode choice under the app's userData dir;
 *   - ``resolveInitialEgressMode`` / ``resolveEgressFrontendsEnabled`` —
 *     env / command-line overrides;
 *   - the ``Egress*`` types for the IPC surface the settings page consumes.
 */
export {
  EgressManager,
  captureProxyEnvironment,
  resolveEgressFrontendsEnabled,
  resolveInitialEgressMode,
  type EgressManagerOptions,
  type ShadowResolveRequest,
} from "./egress-manager";
export {
  DEFAULT_BOOTSTRAP_TTL_MS,
  EgressControlServer,
  type EgressBootstrap,
  type EgressControlServerOptions,
  type RuntimePhasePayload,
  type RuntimePhaseRecord,
} from "./control-server";
export {
  readPersistedEgressMode,
  writePersistedEgressMode,
} from "./mode-store";
export { publishDevEgressBootstrap } from "./bootstrap-file";
export {
  DEFAULT_DIAGNOSTIC_MAX_AGE_MS,
  DEFAULT_DIAGNOSTIC_MAX_ENTRIES,
  EgressDiagnostics,
  redactProxyUrl,
  type EgressDiagnosticsOptions,
} from "./diagnostics";
export {
  DEFAULT_CONNECT_DEGRADED_MS,
  applyConnectionOutcome,
  type EgressHealthOptions,
} from "./health";
export type {
  EgressConnectionOutcome,
  EgressDiagnosticEvent,
  EgressFrontend,
  EgressManagerStatus,
  EgressMode,
  EgressRequestEvent,
  EgressRequestPhase,
  EgressResolution,
  EgressRoute,
  EgressRuntime,
  EgressSnapshot,
  PacParseResult,
} from "./types";
