export type EgressMode = "auto" | "direct" | "off";
export type PublicEgressMode = Exclude<EgressMode, "direct">;

export const NETWORK_EGRESS_CONTRACT_VERSION = 1 as const;
export const DESKTOP_CAPABILITIES_CHANNEL = "desktop_get_capabilities";
export const NETWORK_EGRESS_CHANNELS = {
  getDiagnostics: "egress_get_diagnostics",
  getSnapshots: "egress_get_snapshots",
  getMode: "egress_get_mode",
  getStatus: "egress_get_status",
  getRuntimePhases: "egress_get_runtime_phases",
  setMode: "egress_set_mode",
} as const;
export const NETWORK_EGRESS_EVENTS = {
  statusChanged: "egress-status-changed",
} as const;

export interface NetworkEgressPolicy {
  defaultMode: PublicEgressMode;
  allowedModes: readonly PublicEgressMode[];
  userConfigurable: boolean;
  lockedMode?: PublicEgressMode;
}

export const DEFAULT_NETWORK_EGRESS_POLICY: NetworkEgressPolicy = Object.freeze({
  defaultMode: "off",
  allowedModes: Object.freeze(["off", "auto"] as const),
  userConfigurable: true,
});

export interface NetworkEgressCapability {
  available: boolean;
  contractVersion: typeof NETWORK_EGRESS_CONTRACT_VERSION;
  policy: NetworkEgressPolicy;
}

export interface DesktopCapabilities {
  schemaVersion: 1;
  networkEgress: NetworkEgressCapability;
}

export interface EgressManagerStatus {
  mode: EgressMode;
  /** Whether the desktop egress capability is available (not the active mode). */
  enabled: boolean;
  /** Whether the local egress listeners are currently running. */
  started: boolean;
  emergencyOverride: boolean;
  snapshotCount: number;
  diagnosticEventCount: number;
  lastErrorCode?: string;
}
export type EgressRuntime =
  | "codex"
  | "claude"
  | "deepagents"
  | "provider_test";
export type EgressFrontend =
  | "shadow"
  | "model_ingress"
  | "forward_proxy"
  | "legacy";

export type EgressRoute =
  | {
      kind: "direct";
      source: "local" | "no_proxy" | "env" | "system" | "policy";
    }
  | {
      kind: "http_proxy";
      url: string;
      source: "env" | "system" | "policy";
    }
  | {
      kind: "socks5_proxy";
      url: string;
      source: "env" | "system" | "policy";
    };

export interface EgressResolution {
  targetOrigin: string;
  candidates: EgressRoute[];
  resolvedAt: number;
  ttlMs: number;
  status: "resolved" | "unknown";
  reason?: string;
}

export type PacParseResult =
  | { status: "resolved"; candidates: EgressRoute[] }
  | { status: "unknown"; reason: string };

export type EgressDiagnosticEvent = {
  event:
    | "egress.attempt.started"
    | "egress.route.resolved"
    | "egress.resolve.failed"
    | "egress.connect.succeeded"
    | "egress.stream.established"
    | "egress.connect.failed"
    | "egress.response.headers"
    | "egress.request.completed"
    | "egress.request.aborted"
    | "egress.request.failed"
    | "egress.request.cancelled";
  connectionAttemptId: string;
  clientId: string;
  runtime: EgressRuntime;
  frontend: EgressFrontend;
  targetOrigin: string;
  mode: Exclude<EgressMode, "off">;
  timestamp: number;
  resolveMs?: number;
  route?: EgressRoute["kind"];
  source?: EgressRoute["source"];
  redactedProxy?: string;
  candidateCount?: number;
  errorCode?: string;
  candidateIndex?: number;
  connectMs?: number;
  fallbackCount?: number;
  statusCode?: number;
  responseMs?: number;
  firstByteMs?: number;
  totalMs?: number;
};

export type EgressRequestPhase =
  | "headers_received"
  | "first_byte"
  | "completed"
  | "aborted"
  | "failed"
  | "cancelled";

/** Request-level signal emitted after route resolution and socket setup. */
export interface EgressRequestEvent {
  connectionAttemptId: string;
  startedAt: number;
  clientId: string;
  runtime: EgressRuntime;
  targetOrigin: string;
  phase: EgressRequestPhase;
  elapsedMs: number;
  connectMs: number;
  fallbackCount: number;
  statusCode?: number;
  errorCode?: string;
}

export interface EgressSnapshot {
  connectionAttemptId: string;
  clientId: string;
  /** True only while the owning runtime is executing a model turn. */
  activeTurn: boolean;
  /** True only until this individual upstream request reaches a terminal phase. */
  requestActive: boolean;
  runtime: EgressRuntime;
  frontend: EgressFrontend;
  targetOrigin: string;
  mode: EgressMode;
  route: EgressRoute["kind"] | "unknown";
  health: "unknown" | "healthy" | "degraded" | "failed";
  source?: EgressRoute["source"];
  redactedProxy?: string;
  resolveMs?: number;
  connectMs?: number;
  responseStatus?: number;
  responseMs?: number;
  firstByteMs?: number;
  totalMs?: number;
  reconnectCount: number;
  fallbackCount: number;
  lastErrorCode?: string;
  correlationConfidence: "exact_runtime" | "time_origin" | "none";
  updatedAt: number;
}

export interface EgressConnectionOutcome {
  success: boolean;
  connectMs?: number;
  fallbackCount?: number;
  reconnectCount?: number;
  errorCode?: string;
}

export interface EgressBootstrap {
  mode: "auto" | "direct";
  controlEndpoint: string;
  bootstrapToken: string;
  expiresAt: number;
}

export interface RuntimePhasePayload {
  turnAttemptId: string;
  clientId: string;
  phase:
    | "runtime_init_started"
    | "runtime_init"
    | "thread_init_started"
    | "thread_init"
    | "dispatch_started"
    | "dispatch"
    | "model_first_event"
    | "runtime_ready"
    | "runtime_prepare_failed"
    | "turn_complete"
    | "interrupted";
  monotonicMs: number;
}

/**
 * Electron stamps control-plane receipt time because Python's monotonic clock
 * and Node's performance clock do not share an origin. `observedAt` is the
 * cross-process timeline value; `monotonicMs` remains useful only for ordering
 * phases emitted by the same backend process.
 */
export interface RuntimePhaseRecord extends RuntimePhasePayload {
  observedAt: number;
  runtime?: string;
  targetOrigin?: string;
}
