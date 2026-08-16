export type EgressMode = "auto" | "direct" | "off";

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
