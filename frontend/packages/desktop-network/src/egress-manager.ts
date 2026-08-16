import { randomUUID } from "node:crypto";
import {
  EgressControlServer,
  type EgressBootstrap,
  type RuntimePhasePayload,
  type RuntimePhaseRecord,
} from "./control-server";
import { EgressDiagnostics, redactProxyUrl } from "./diagnostics";
import {
  ForwardProxy,
  type ForwardProxyDescriptor,
  type ForwardProxyRegistration,
  type ForwardProxyConnectionEvent,
} from "./forward-proxy";
import {
  applyConnectionOutcome,
  DEFAULT_CONNECT_DEGRADED_MS,
} from "./health";
import {
  ModelIngress,
  type ModelIngressConnectionEvent,
  type ModelIngressDescriptor,
  type ModelIngressRegistration,
} from "./model-ingress";
import {
  OutboundResolver,
  type OutboundResolverOptions,
} from "./outbound-resolver";
import type {
  EgressDiagnosticEvent,
  EgressMode,
  EgressManagerStatus,
  EgressRequestEvent,
  EgressResolution,
  EgressRuntime,
  EgressSnapshot,
} from "./types";
import { UpstreamConnector } from "./upstream-connector";

const PROXY_ENV_KEYS = [
  "http_proxy",
  "HTTP_PROXY",
  "https_proxy",
  "HTTPS_PROXY",
  "all_proxy",
  "ALL_PROXY",
  "no_proxy",
  "NO_PROXY",
] as const;

export const captureProxyEnvironment = (
  env: NodeJS.ProcessEnv,
): Record<string, string> => {
  const snapshot: Record<string, string> = {};
  for (const key of PROXY_ENV_KEYS) {
    const value = env[key];
    if (value !== undefined) snapshot[key] = value;
  }
  return snapshot;
};

export const resolveInitialEgressMode = (
  env: NodeJS.ProcessEnv,
  persistedMode: EgressMode = "off",
): EgressMode =>
  env.VALUZ_EGRESS_MODE?.trim().toLowerCase() === "off" ||
  persistedMode === "off"
    ? "off"
    : "auto";

export const resolveEgressFrontendsEnabled = (
  env: NodeJS.ProcessEnv,
  disabledBySwitch = false,
): boolean =>
  !disabledBySwitch &&
  env.VALUZ_EGRESS_FRONTENDS?.trim().toLowerCase() !== "0";

export interface EgressManagerOptions {
  mode: EgressMode;
  env: NodeJS.ProcessEnv;
  resolveSystemProxy: OutboundResolverOptions["resolveSystemProxy"];
  diagnostics?: EgressDiagnostics;
  now?: () => number;
  frontendsEnabled?: boolean;
  emergencyOverride?: boolean;
}

export interface ShadowResolveRequest {
  targetUrl: string;
  clientId: string;
  runtime: EgressRuntime;
}

/**
 * Electron-owned canary egress manager. It owns route resolution, diagnostics,
 * the loopback control plane and the two narrowly scoped traffic frontends.
 */
export class EgressManager {
  private mode: EgressMode;
  private readonly now: () => number;
  private readonly diagnostics: EgressDiagnostics;
  private readonly resolver: OutboundResolver;
  private readonly connector: UpstreamConnector;
  private readonly frontendsEnabled: boolean;
  private readonly emergencyOverride: boolean;
  private readonly modelIngress: ModelIngress;
  private readonly forwardProxy: ForwardProxy;
  private readonly controlServer: EgressControlServer;
  private readonly runtimePhases: RuntimePhaseRecord[] = [];
  private readonly runtimeMetadata = new Map<
    string,
    { runtime: EgressRuntime; targetOrigin?: string }
  >();
  private readonly activeTurns = new Map<string, string>();
  private readonly lastTerminalAttemptIds = new Map<string, Set<string>>();
  private readonly snapshots = new Map<string, EgressSnapshot>();
  private started = false;
  private lastErrorCode: string | undefined;

  constructor(options: EgressManagerOptions) {
    this.mode = options.mode;
    this.now = options.now ?? Date.now;
    this.diagnostics = options.diagnostics ?? new EgressDiagnostics({ now: this.now });
    this.resolver = new OutboundResolver({
      env: captureProxyEnvironment(options.env),
      resolveSystemProxy: options.resolveSystemProxy,
      now: this.now,
    });
    this.frontendsEnabled = options.frontendsEnabled ?? false;
    this.emergencyOverride = options.emergencyOverride ?? false;
    const connector = new UpstreamConnector({ now: this.now });
    this.connector = connector;
    this.modelIngress = new ModelIngress({
      resolver: this.resolver,
      connector,
      mode: this.mode === "direct" ? "direct" : "auto",
      now: this.now,
      onConnection: (event) =>
        this.recordFrontendConnection("model_ingress", event),
      onRequest: (event) => this.recordFrontendRequest("model_ingress", event),
    });
    this.forwardProxy = new ForwardProxy({
      resolver: this.resolver,
      connector,
      mode: this.mode === "direct" ? "direct" : "auto",
      now: this.now,
      onConnection: (event) =>
        this.recordFrontendConnection("forward_proxy", event),
      onRequest: (event) => this.recordFrontendRequest("forward_proxy", event),
    });
    this.controlServer = new EgressControlServer({
      mode: this.mode === "direct" ? "direct" : "auto",
      registerModelIngress: (registration) =>
        this.registerModelIngress(registration),
      registerForwardProxy: (registration) =>
        this.registerForwardProxy(registration),
      revokeClient: (clientId) => this.revokeClient(clientId),
      renewClients: (clientIds, expiresAt) => {
        for (const clientId of clientIds) {
          this.modelIngress.renew(clientId, expiresAt);
          this.forwardProxy.renew(clientId, expiresAt);
        }
      },
      recordRuntimePhase: (payload) => this.recordRuntimePhase(payload),
      now: this.now,
    });
  }

  async start(): Promise<void> {
    if (this.mode === "off") return;
    this.lastErrorCode = undefined;
    this.started = true;
    if (!this.frontendsEnabled) return;
    try {
      await this.modelIngress.start();
      await this.forwardProxy.start();
      await this.controlServer.start();
      this.connector.setProtectedLoopbackPorts(
        [
          this.modelIngress.getListeningPort(),
          this.forwardProxy.getListeningPort(),
          this.controlServer.getListeningPort(),
        ].filter((port): port is number => port !== null),
      );
    } catch (error) {
      await Promise.allSettled([
        this.modelIngress.stop(),
        this.forwardProxy.stop(),
        this.controlServer.stop(),
      ]);
      this.started = false;
      this.lastErrorCode = "egress_frontend_start_failed";
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.started = false;
    await Promise.allSettled([
      this.controlServer.stop(),
      this.modelIngress.stop(),
      this.forwardProxy.stop(),
    ]);
    this.resolver.invalidate();
    this.connector.setProtectedLoopbackPorts([]);
    this.snapshots.clear();
    this.diagnostics.clear();
    this.runtimePhases.splice(0);
    this.runtimeMetadata.clear();
    this.activeTurns.clear();
    this.lastTerminalAttemptIds.clear();
  }

  /** Stop registrations first while allowing already-established streams to drain. */
  async quiesce(): Promise<void> {
    this.started = false;
    await this.controlServer.stop();
    this.modelIngress.revokeAll();
    this.forwardProxy.revokeAll();
  }

  async setMode(mode: EgressMode): Promise<void> {
    if (this.emergencyOverride && mode !== "off") {
      throw new Error("egress_mode_locked_by_environment");
    }
    this.mode = mode;
    this.resolver.invalidate();
    if (mode === "off") {
      this.lastErrorCode = undefined;
      await this.stop();
      return;
    }
    const activeMode = mode === "direct" ? "direct" : "auto";
    this.modelIngress.setMode(activeMode);
    this.forwardProxy.setMode(activeMode);
    this.controlServer.setMode(activeMode);
    if (!this.started) await this.start();
  }

  getMode(): EgressMode {
    return this.mode;
  }

  isStarted(): boolean {
    return this.started;
  }

  isFrontendsEnabled(): boolean {
    return this.frontendsEnabled;
  }

  getStatus(): EgressManagerStatus {
    return {
      mode: this.mode,
      enabled: this.frontendsEnabled,
      started: this.started,
      emergencyOverride: this.emergencyOverride,
      snapshotCount: this.snapshots.size,
      diagnosticEventCount: this.diagnostics.snapshot().length,
      lastErrorCode: this.lastErrorCode,
    };
  }

  getDiagnostics(): EgressDiagnosticEvent[] {
    return this.diagnostics.snapshot();
  }

  getSnapshots(): EgressSnapshot[] {
    return [...this.snapshots.values()].map((snapshot) => ({ ...snapshot }));
  }

  getRuntimePhases(): RuntimePhaseRecord[] {
    return this.runtimePhases.map((phase) => ({ ...phase }));
  }

  getBootstrap(): EgressBootstrap | null {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      return null;
    }
    return this.controlServer.bootstrap();
  }

  registerModelIngress(
    registration: ModelIngressRegistration,
  ): ModelIngressDescriptor {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      throw new Error("egress_frontends_unavailable");
    }
    const descriptor = this.modelIngress.register(registration);
    this.runtimeMetadata.set(registration.clientId, {
      runtime: registration.runtime,
      targetOrigin: this.targetOrigin(registration.upstreamBaseUrl),
    });
    return descriptor;
  }

  registerForwardProxy(
    registration: ForwardProxyRegistration,
  ): ForwardProxyDescriptor {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      throw new Error("egress_frontends_unavailable");
    }
    const descriptor = this.forwardProxy.register(registration);
    this.runtimeMetadata.set(registration.clientId, {
      runtime: registration.runtime,
    });
    return descriptor;
  }

  revokeClient(clientId: string): void {
    this.modelIngress.revoke(clientId);
    this.forwardProxy.revoke(clientId);
    this.activeTurns.delete(clientId);
    this.lastTerminalAttemptIds.delete(clientId);
    this.runtimeMetadata.delete(clientId);
    for (const [key, snapshot] of this.snapshots) {
      if (snapshot.clientId === clientId) this.snapshots.delete(key);
    }
    for (let index = this.runtimePhases.length - 1; index >= 0; index -= 1) {
      if (this.runtimePhases[index]?.clientId === clientId) {
        this.runtimePhases.splice(index, 1);
      }
    }
  }

  private recordRuntimePhase(payload: RuntimePhasePayload): void {
    const observedAt = this.now();
    const metadata = this.runtimeMetadata.get(payload.clientId);
    this.runtimePhases.push({ ...payload, ...metadata, observedAt });
    this.runtimePhases.splice(0, Math.max(0, this.runtimePhases.length - 500));

    if (
      payload.phase === "runtime_init_started" ||
      payload.phase === "thread_init_started" ||
      payload.phase === "dispatch_started" ||
      payload.phase === "dispatch"
    ) {
      this.activeTurns.set(payload.clientId, payload.turnAttemptId);
      const terminalAttempts = this.lastTerminalAttemptIds.get(payload.clientId);
      for (const [key, snapshot] of this.snapshots) {
        if (
          snapshot.clientId === payload.clientId &&
          !terminalAttempts?.has(snapshot.connectionAttemptId)
        ) {
          this.snapshots.set(key, { ...snapshot, activeTurn: true });
        }
      }
      return;
    }
    if (
      payload.phase !== "runtime_ready" &&
      payload.phase !== "runtime_prepare_failed" &&
      payload.phase !== "turn_complete" &&
      payload.phase !== "interrupted"
    ) {
      return;
    }
    if (this.activeTurns.get(payload.clientId) !== payload.turnAttemptId) {
      return;
    }
    this.activeTurns.delete(payload.clientId);
    const terminalAttempts = new Set<string>();
    for (const [key, snapshot] of this.snapshots) {
      if (snapshot.clientId === payload.clientId) {
        terminalAttempts.add(snapshot.connectionAttemptId);
        if (snapshot.activeTurn) {
          this.snapshots.set(key, { ...snapshot, activeTurn: false });
        }
      }
    }
    this.lastTerminalAttemptIds.set(payload.clientId, terminalAttempts);
  }

  async resolveShadow(request: ShadowResolveRequest): Promise<EgressResolution> {
    if (!this.started || this.mode === "off") {
      return {
        targetOrigin: this.targetOrigin(request.targetUrl),
        candidates: [],
        resolvedAt: this.now(),
        ttlMs: 0,
        status: "unknown",
        reason: "egress_manager_off",
      };
    }

    const connectionAttemptId = randomUUID();
    const startedAt = this.now();
    const targetOrigin = this.targetOrigin(request.targetUrl);
    const activeMode = this.mode === "direct" ? "direct" : "auto";
    this.diagnostics.record({
      event: "egress.attempt.started",
      connectionAttemptId,
      clientId: request.clientId,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin,
      mode: activeMode,
      timestamp: startedAt,
    });

    const result = await this.resolver.resolve(request.targetUrl, activeMode);
    const finishedAt = this.now();
    const resolveMs = Math.max(0, finishedAt - startedAt);
    const first = result.candidates[0];
    const key = `${request.clientId}:${result.targetOrigin}`;

    if (result.status === "resolved" && first) {
      const redactedProxy =
        "url" in first ? redactProxyUrl(first.url) : undefined;
      this.diagnostics.record({
        event: "egress.route.resolved",
        connectionAttemptId,
        clientId: request.clientId,
        runtime: request.runtime,
        frontend: "shadow",
        targetOrigin: result.targetOrigin,
        mode: activeMode,
        timestamp: finishedAt,
        resolveMs,
        route: first.kind,
        source: first.source,
        redactedProxy,
        candidateCount: result.candidates.length,
      });
      this.snapshots.set(key, {
        connectionAttemptId,
        clientId: request.clientId,
        activeTurn: false,
        requestActive: false,
        runtime: request.runtime,
        frontend: "shadow",
        targetOrigin: result.targetOrigin,
        mode: this.mode,
        route: first.kind,
        health: "unknown",
        source: first.source,
        redactedProxy,
        resolveMs,
        reconnectCount: 0,
        fallbackCount: 0,
        correlationConfidence: "exact_runtime",
        updatedAt: finishedAt,
      });
      return result;
    }

    const errorCode = result.reason ?? "egress_resolve_unknown";
    this.diagnostics.record({
      event: "egress.resolve.failed",
      connectionAttemptId,
      clientId: request.clientId,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin: result.targetOrigin,
      mode: activeMode,
      timestamp: finishedAt,
      resolveMs,
      candidateCount: 0,
      errorCode,
    });
    this.snapshots.set(key, {
      connectionAttemptId,
      clientId: request.clientId,
      activeTurn: false,
      requestActive: false,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin: result.targetOrigin,
      mode: this.mode,
      route: "unknown",
      health: "unknown",
      resolveMs,
      reconnectCount: 0,
      fallbackCount: 0,
      lastErrorCode: errorCode,
      correlationConfidence: "exact_runtime",
      updatedAt: finishedAt,
    });
    return result;
  }

  private targetOrigin(raw: string): string {
    try {
      return new URL(raw).origin;
    } catch {
      return "invalid";
    }
  }

  private recordFrontendConnection(
    frontend: "model_ingress" | "forward_proxy",
    event: ModelIngressConnectionEvent | ForwardProxyConnectionEvent,
  ): void {
    const activeMode = this.mode === "direct" ? "direct" : "auto";
    const finishedAt = this.now();
    this.diagnostics.record({
      event: "egress.attempt.started",
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.registration.clientId,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: activeMode,
      timestamp: event.startedAt,
    });
    const key = `${event.registration.clientId}:${event.targetOrigin}`;

    if (event.connection) {
      const redactedProxy =
        "url" in event.connection.route
          ? redactProxyUrl(event.connection.route.url)
          : undefined;
      this.diagnostics.record({
        event: "egress.route.resolved",
        connectionAttemptId: event.connectionAttemptId,
        clientId: event.registration.clientId,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: activeMode,
        timestamp: event.startedAt + event.resolutionMs,
        resolveMs: event.resolutionMs,
        route: event.connection.route.kind,
        source: event.connection.route.source,
        redactedProxy,
        candidateIndex: event.connection.candidateIndex,
      });
      this.diagnostics.record({
        event: "egress.connect.succeeded",
        connectionAttemptId: event.connectionAttemptId,
        clientId: event.registration.clientId,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: activeMode,
        timestamp: finishedAt,
        route: event.connection.route.kind,
        source: event.connection.route.source,
        redactedProxy,
        candidateIndex: event.connection.candidateIndex,
        connectMs: event.connection.connectMs,
        fallbackCount: event.connection.fallbackCount,
      });
      const base: EgressSnapshot = {
        connectionAttemptId: event.connectionAttemptId,
        clientId: event.registration.clientId,
        activeTurn: this.activeTurns.has(event.registration.clientId),
        requestActive: true,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: this.mode,
        route: event.connection.route.kind,
        health: "unknown",
        source: event.connection.route.source,
        redactedProxy,
        resolveMs: event.resolutionMs,
        connectMs: event.connection.connectMs,
        reconnectCount: this.snapshots.get(key)?.reconnectCount ?? 0,
        fallbackCount:
          (this.snapshots.get(key)?.fallbackCount ?? 0) +
          event.connection.fallbackCount,
        correlationConfidence: "exact_runtime",
        updatedAt: finishedAt,
      };
      this.snapshots.set(key, base);
      return;
    }

    const errorCode = event.errorCode ?? "egress_connect_failed";
    this.diagnostics.record({
      event: "egress.connect.failed",
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.registration.clientId,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: activeMode,
      timestamp: finishedAt,
      resolveMs: event.resolutionMs,
      errorCode,
    });
    const base: EgressSnapshot = {
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.registration.clientId,
      activeTurn: this.activeTurns.has(event.registration.clientId),
      requestActive: false,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: this.mode,
      route: "unknown",
      health: "unknown",
      resolveMs: event.resolutionMs,
      reconnectCount: 0,
      fallbackCount: 0,
      correlationConfidence: "exact_runtime",
      updatedAt: finishedAt,
    };
    this.snapshots.set(
      key,
      applyConnectionOutcome(
        this.snapshots.get(key) ?? base,
        { success: false, errorCode },
        { now: () => finishedAt },
      ),
    );
  }

  private recordFrontendRequest(
    frontend: "model_ingress" | "forward_proxy",
    event: EgressRequestEvent,
  ): void {
    const activeMode = this.mode === "direct" ? "direct" : "auto";
    const key = `${event.clientId}:${event.targetOrigin}`;
    const snapshot = this.snapshots.get(key);
    const matchingSnapshot =
      snapshot?.connectionAttemptId === event.connectionAttemptId
        ? snapshot
        : undefined;
    const timestamp = event.startedAt + event.elapsedMs;
    const eventName: EgressDiagnosticEvent["event"] =
      event.phase === "headers_received"
        ? "egress.response.headers"
        : event.phase === "first_byte"
          ? "egress.stream.established"
          : event.phase === "completed"
            ? "egress.request.completed"
            : event.phase === "aborted"
              ? "egress.request.aborted"
              : event.phase === "cancelled"
                ? "egress.request.cancelled"
                : "egress.request.failed";
    this.diagnostics.record({
      event: eventName,
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.clientId,
      runtime: event.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: activeMode,
      timestamp,
      route:
        matchingSnapshot?.route === "unknown"
          ? undefined
          : matchingSnapshot?.route,
      source: matchingSnapshot?.source,
      redactedProxy: matchingSnapshot?.redactedProxy,
      connectMs: event.connectMs,
      fallbackCount: event.fallbackCount,
      statusCode: event.statusCode,
      responseMs:
        event.phase === "headers_received" ? event.elapsedMs : undefined,
      firstByteMs: event.phase === "first_byte" ? event.elapsedMs : undefined,
      totalMs:
        event.phase === "completed" ||
        event.phase === "aborted" ||
        event.phase === "failed" ||
        event.phase === "cancelled"
          ? event.elapsedMs
          : undefined,
      errorCode: event.errorCode,
    });

    if (!matchingSnapshot) return;
    const upstreamFailed = (event.statusCode ?? 0) >= 500;
    const requestFailed = event.phase === "aborted" || event.phase === "failed";
    const successfulSignal =
      event.phase === "headers_received" ||
      event.phase === "first_byte" ||
      event.phase === "completed";
    const degraded =
      matchingSnapshot.fallbackCount > 0 ||
      matchingSnapshot.reconnectCount > 0 ||
      event.connectMs > DEFAULT_CONNECT_DEGRADED_MS;
    const health =
      event.phase === "cancelled"
        ? matchingSnapshot.health
        : upstreamFailed || requestFailed
          ? "failed"
          : successfulSignal
            ? degraded
              ? "degraded"
              : "healthy"
            : matchingSnapshot.health;
    const lastErrorCode =
      event.phase === "cancelled"
        ? matchingSnapshot.lastErrorCode
        : upstreamFailed
          ? `upstream_http_${event.statusCode}`
          : requestFailed
            ? event.errorCode ?? "egress_request_failed"
            : successfulSignal
              ? undefined
              : matchingSnapshot.lastErrorCode;
    const requestActive =
      event.phase !== "completed" &&
      event.phase !== "aborted" &&
      event.phase !== "failed" &&
      event.phase !== "cancelled";
    this.snapshots.set(key, {
      ...matchingSnapshot,
      requestActive,
      health,
      connectMs: event.connectMs,
      responseStatus: event.statusCode ?? matchingSnapshot.responseStatus,
      responseMs:
        event.phase === "headers_received"
          ? event.elapsedMs
          : matchingSnapshot.responseMs,
      firstByteMs:
        event.phase === "first_byte"
          ? event.elapsedMs
          : matchingSnapshot.firstByteMs,
      totalMs:
        event.phase === "completed" ||
        event.phase === "aborted" ||
        event.phase === "failed" ||
        event.phase === "cancelled"
          ? event.elapsedMs
          : matchingSnapshot.totalMs,
      lastErrorCode,
      updatedAt: timestamp,
    });
  }
}
