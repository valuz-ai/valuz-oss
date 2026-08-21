type NetworkHealth = "unknown" | "healthy" | "degraded" | "failed";

export const isManagedNetworkMode = (mode?: string): boolean =>
  mode === "auto" || mode === "direct";

export const shouldShowNetworkDiagnosticsAction = (
  health: NetworkHealth,
  hasDiagnostics: boolean,
): boolean =>
  hasDiagnostics && (health === "degraded" || health === "failed");

export const currentNetworkSnapshots = <
  T extends {
    activeTurn: boolean;
    requestActive?: boolean;
    totalMs?: number;
    updatedAt: number;
  },
>(snapshots: T[]): T[] =>
  snapshots
    .filter(
      (snapshot) =>
        snapshot.activeTurn &&
        (snapshot.requestActive ?? snapshot.totalMs === undefined),
    )
    .sort((left, right) => right.updatedAt - left.updatedAt);

export interface RuntimeActivity {
  id: string;
  runtime: string;
  targetOrigin?: string;
  stage:
    | "runtimeInit"
    | "threadInit"
    | "modelConnecting"
    | "waitingResponse"
    | "streaming";
  startedAt: number;
  updatedAt: number;
}

export const currentRuntimeActivities = (
  phases: Array<{
    clientId?: unknown;
    turnAttemptId?: unknown;
    phase?: unknown;
    observedAt?: unknown;
    runtime?: unknown;
    targetOrigin?: unknown;
  }>,
  now = Date.now(),
): RuntimeActivity[] => {
  const grouped = new Map<string, RuntimeActivity & { terminal: boolean }>();
  for (const phase of phases) {
    if (
      typeof phase.clientId !== "string" ||
      typeof phase.turnAttemptId !== "string" ||
      typeof phase.phase !== "string" ||
      typeof phase.observedAt !== "number"
    ) {
      continue;
    }
    const id = `${phase.clientId}:${phase.turnAttemptId}`;
    const previous = grouped.get(id);
    const stage: RuntimeActivity["stage"] =
      phase.phase === "runtime_init_started" || phase.phase === "runtime_init"
        ? "runtimeInit"
        : phase.phase === "thread_init_started" || phase.phase === "thread_init"
          ? "threadInit"
          : phase.phase === "dispatch_started"
            ? "modelConnecting"
            : phase.phase === "dispatch"
              ? "waitingResponse"
              : "streaming";
    grouped.set(id, {
      id,
      runtime:
        typeof phase.runtime === "string"
          ? phase.runtime
          : previous?.runtime ?? "model",
      targetOrigin:
        typeof phase.targetOrigin === "string"
          ? phase.targetOrigin
          : previous?.targetOrigin,
      stage,
      startedAt: previous?.startedAt ?? phase.observedAt,
      updatedAt: phase.observedAt,
      terminal: [
        "runtime_ready",
        "runtime_prepare_failed",
        "turn_complete",
        "interrupted",
      ].includes(phase.phase),
    });
  }
  return [...grouped.values()]
    .filter((activity) => !activity.terminal && now - activity.updatedAt < 5 * 60_000)
    .sort((left, right) => right.updatedAt - left.updatedAt)
    .map(
      (activity): RuntimeActivity => ({
        id: activity.id,
        runtime: activity.runtime,
        targetOrigin: activity.targetOrigin,
        stage: activity.stage,
        startedAt: activity.startedAt,
        updatedAt: activity.updatedAt,
      }),
    );
};

export const networkRuntimeLabel = (runtime: string): string =>
  ({
    codex: "Codex",
    claude: "Claude Code",
    deepagents: "Valuz Agent",
    deepseek_harness: "DeepSeek Harness",
    provider_test: "Provider Test",
  })[runtime] ?? runtime;

export const networkRouteKey = (
  route: string,
):
  | "settings.network.route.direct"
  | "settings.network.route.httpProxy"
  | "settings.network.route.socks5Proxy"
  | "settings.network.route.unknown" =>
  route === "direct"
    ? "settings.network.route.direct"
    : route === "http_proxy"
      ? "settings.network.route.httpProxy"
      : route === "socks5_proxy"
        ? "settings.network.route.socks5Proxy"
        : "settings.network.route.unknown";

export const networkHealthDetailKey = (snapshot: {
  health: NetworkHealth;
  connectMs?: number;
}):
  | "settings.network.healthDetail.waitingRequest"
  | "settings.network.healthDetail.waitingResponse"
  | "settings.network.healthDetail.healthy"
  | "settings.network.healthDetail.degraded"
  | "settings.network.healthDetail.failed" => {
  if (snapshot.health === "healthy") {
    return "settings.network.healthDetail.healthy";
  }
  if (snapshot.health === "degraded") {
    return "settings.network.healthDetail.degraded";
  }
  if (snapshot.health === "failed") {
    return "settings.network.healthDetail.failed";
  }
  return snapshot.connectMs === undefined
    ? "settings.network.healthDetail.waitingRequest"
    : "settings.network.healthDetail.waitingResponse";
};
