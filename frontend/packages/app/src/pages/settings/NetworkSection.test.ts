import { describe, expect, it } from "vitest";
import { buildEgressDiagnosticsExport } from "./network-diagnostics";
import {
  currentNetworkSnapshots,
  currentRuntimeActivities,
  isManagedNetworkMode,
  networkHealthDetailKey,
  networkRouteKey,
  networkRuntimeLabel,
  shouldShowNetworkDiagnosticsAction,
} from "./network-presentation";

describe("network status presentation", () => {
  it("shows only requests that are still transferring inside active turns", () => {
    expect(
      currentNetworkSnapshots([
        {
          id: "finished-turn",
          activeTurn: false,
          requestActive: true,
          updatedAt: 400,
        },
        {
          id: "completed-request",
          activeTurn: true,
          requestActive: false,
          updatedAt: 300,
        },
        {
          id: "active-old",
          activeTurn: true,
          requestActive: true,
          updatedAt: 100,
        },
        {
          id: "active-new",
          activeTurn: true,
          requestActive: true,
          updatedAt: 200,
        },
        {
          id: "legacy-completed",
          activeTurn: true,
          totalMs: 50,
          updatedAt: 500,
        },
        {
          id: "legacy-active",
          activeTurn: true,
          updatedAt: 50,
        },
      ]).map((snapshot) => snapshot.id),
    ).toEqual(["active-new", "active-old", "legacy-active"]);
  });

  it("groups auto and temporary direct under Valuz-managed networking", () => {
    expect(isManagedNetworkMode("auto")).toBe(true);
    expect(isManagedNetworkMode("direct")).toBe(true);
    expect(isManagedNetworkMode("off")).toBe(false);
  });

  it("shows local runtime initialization before a model request exists", () => {
    expect(
      currentRuntimeActivities(
        [
          {
            clientId: "client-1",
            turnAttemptId: "turn-1",
            phase: "runtime_init_started",
            observedAt: 1_000,
            runtime: "codex",
            targetOrigin: "https://chatgpt.com",
          },
          {
            clientId: "client-1",
            turnAttemptId: "turn-1",
            phase: "thread_init_started",
            observedAt: 2_000,
            runtime: "codex",
          },
        ],
        3_000,
      ),
    ).toEqual([
      expect.objectContaining({
        runtime: "codex",
        stage: "threadInit",
        startedAt: 1_000,
      }),
    ]);
    expect(
      currentRuntimeActivities(
        [
          {
            clientId: "client-1",
            turnAttemptId: "turn-1",
            phase: "runtime_init_started",
            observedAt: 1_000,
          },
          {
            clientId: "client-1",
            turnAttemptId: "turn-1",
            phase: "runtime_ready",
            observedAt: 2_000,
          },
        ],
        3_000,
      ),
    ).toEqual([]);
  });

  it("offers diagnostic export only when a real connection has a problem", () => {
    expect(shouldShowNetworkDiagnosticsAction("healthy", true)).toBe(false);
    expect(shouldShowNetworkDiagnosticsAction("unknown", true)).toBe(false);
    expect(shouldShowNetworkDiagnosticsAction("degraded", true)).toBe(true);
    expect(shouldShowNetworkDiagnosticsAction("failed", true)).toBe(true);
    expect(shouldShowNetworkDiagnosticsAction("failed", false)).toBe(false);
  });

  it("turns runtime, route and lifecycle state into user-facing labels", () => {
    expect(networkRuntimeLabel("codex")).toBe("Codex");
    expect(networkRuntimeLabel("claude")).toBe("Claude Code");
    expect(networkRouteKey("http_proxy")).toBe(
      "settings.network.route.httpProxy",
    );
    expect(networkRouteKey("direct")).toBe("settings.network.route.direct");
    expect(networkHealthDetailKey({ health: "unknown" })).toBe(
      "settings.network.healthDetail.waitingRequest",
    );
    expect(networkHealthDetailKey({ health: "unknown", connectMs: 224 })).toBe(
      "settings.network.healthDetail.waitingResponse",
    );
    expect(networkHealthDetailKey({ health: "failed", connectMs: 224 })).toBe(
      "settings.network.healthDetail.failed",
    );
  });
});

describe("buildEgressDiagnosticsExport", () => {
  it("copies only the documented diagnostic schema", () => {
    const exported = buildEgressDiagnosticsExport(
      {
        mode: "auto",
        enabled: true,
        started: true,
        emergencyOverride: false,
        snapshotCount: 1,
        diagnosticEventCount: 1,
      },
      [
        {
          runtime: "codex",
          frontend: "model_ingress",
          targetOrigin: "https://api.example",
          mode: "auto",
          route: "http_proxy",
          health: "healthy",
          fallbackCount: 0,
          reconnectCount: 0,
          responseMs: 120,
          firstByteMs: 140,
          totalMs: 800,
          updatedAt: 100,
          clientId: "runtime-client-secret",
          secret: "must-not-be-exported",
        } as never,
      ],
      [
        {
          event: "egress.stream.established",
          runtime: "codex",
          targetOrigin: "https://api.example",
          clientId: "runtime-client-secret",
          connectionAttemptId: "connection-secret",
          firstByteMs: 140,
          prompt: "must-not-be-exported",
          proxyUrl: "http://user:secret@proxy.example",
        },
      ],
      [
        {
          phase: "model_first_event",
          monotonicMs: 200,
          observedAt: 300,
          clientId: "runtime-client-secret",
          turnAttemptId: "turn-secret",
        },
      ],
    );

    const serialized = JSON.stringify(exported);
    expect(serialized).not.toContain("must-not-be-exported");
    expect(serialized).not.toContain("runtime-client-secret");
    expect(serialized).not.toContain("connection-secret");
    expect(serialized).not.toContain("turn-secret");
    expect(serialized).not.toContain("proxyUrl");
    expect(exported.snapshots[0]).toMatchObject({
      runtime: "codex",
      targetOrigin: "https://api.example",
      runtimeRef: "runtime-1",
      firstByteMs: 140,
    });
    expect(exported.runtimePhases[0]).toEqual({
      phase: "model_first_event",
      monotonicMs: 200,
      observedAt: 300,
      runtimeRef: "runtime-1",
      turnRef: "turn-1",
    });
    expect(exported.diagnostics[0]).toMatchObject({
      runtimeRef: "runtime-1",
      attemptRef: "attempt-1",
    });
  });
});
