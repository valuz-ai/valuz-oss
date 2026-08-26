import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  EgressBootstrap,
  EgressManagerStatus,
} from "@valuz/desktop-network-egress/contracts";
import { createServiceManager } from "./mod";

const mocks = vi.hoisted(() => ({
  order: [] as string[],
  sidecarOptions: [] as Array<{
    port?: number;
    development?: boolean;
    egressBootstrap?: EgressBootstrap | null;
    egressRequired?: boolean;
    desktopControlToken?: string;
  }>,
}));

vi.mock("./sidecar", () => ({
  reclaimStaleSidecar: vi.fn(async () => undefined),
  resolveSidecarDataDir: vi.fn(() => "/tmp/valuz-managed-dev"),
  startSidecar: vi.fn(async (options: {
    port?: number;
    development?: boolean;
    egressBootstrap?: EgressBootstrap | null;
    egressRequired?: boolean;
    desktopControlToken?: string;
  }) => {
    mocks.sidecarOptions.push(options);
    return {
      name: "agent-server",
      pid: 123,
      port: 19100,
      stop: async () => {
        mocks.order.push("sidecar-stop");
      },
    };
  }),
}));

const status = (): EgressManagerStatus => ({
  mode: "auto",
  enabled: true,
  started: true,
  emergencyOverride: false,
  snapshotCount: 0,
  diagnosticEventCount: 0,
});

describe("DesktopServiceManager egress lifecycle", () => {
  beforeEach(() => {
    mocks.order.splice(0);
    mocks.sidecarOptions.splice(0);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("delivers bootstrap only to the sidecar and quiesces before bounded teardown", async () => {
    const manager = createServiceManager("/tmp/valuz-egress-test", {
      egressManager: {
        start: async () => {
          mocks.order.push("manager-start");
        },
        quiesce: async () => {
          mocks.order.push("manager-quiesce");
        },
        stop: async () => {
          mocks.order.push("manager-stop");
        },
        setMode: async () => undefined,
        getDiagnostics: () => [],
        getSnapshots: () => [],
        getRuntimePhases: () => [],
        getMode: () => "auto",
        getStatus: status,
        getBootstrap: () => ({
          mode: "auto",
          controlEndpoint: "http://127.0.0.1:43123",
          bootstrapToken: "memory-only-token",
          expiresAt: Date.now() + 60_000,
        }),
      },
    });

    await manager.startAllServices();
    expect(mocks.sidecarOptions[0].egressBootstrap?.bootstrapToken).toBe(
      "memory-only-token",
    );
    expect(mocks.sidecarOptions[0].desktopControlToken).toHaveLength(64);
    expect(mocks.sidecarOptions[0].desktopControlToken).not.toBe(
      manager.getAgentServerInfo().token,
    );
    await manager.stopAllServices();

    expect(mocks.order).toEqual([
      "manager-start",
      "manager-quiesce",
      "sidecar-stop",
      "manager-stop",
    ]);
  });

  it("keeps the backend available but marks model networking required when manager startup fails", async () => {
    const manager = createServiceManager("/tmp/valuz-egress-failure-test", {
      egressManager: {
        start: async () => {
          throw new Error("secret internal startup detail");
        },
        quiesce: async () => undefined,
        stop: async () => undefined,
        setMode: async () => undefined,
        getDiagnostics: () => [],
        getSnapshots: () => [],
        getRuntimePhases: () => [],
        getMode: () => "auto",
        getStatus: () => ({
          ...status(),
          started: false,
          lastErrorCode: "egress_frontend_start_failed",
        }),
        getBootstrap: () => null,
      },
    });

    const services = await manager.startAllServices();

    expect(services[0].status).toBe("running");
    expect(mocks.sidecarOptions[0]).toMatchObject({
      egressBootstrap: null,
      egressRequired: true,
    });
    expect(manager.getLogs("agent-server").join("\n")).not.toContain(
      "secret internal startup detail",
    );
  });

  it("keeps a successful recovery switch usable when persistence fails", async () => {
    let mode: "auto" | "direct" | "off" = "auto";
    const manager = createServiceManager("/tmp/valuz-egress-persist-test", {
      egressManager: {
        start: async () => undefined,
        quiesce: async () => undefined,
        stop: async () => undefined,
        setMode: async (nextMode) => {
          mode = nextMode;
        },
        getDiagnostics: () => [],
        getSnapshots: () => [],
        getRuntimePhases: () => [],
        getMode: () => mode,
        getStatus: () => ({ ...status(), mode }),
        getBootstrap: () => null,
      },
      onEgressModeChanged: () => {
        throw new Error("private filesystem detail");
      },
    });

    await expect(manager.setEgressMode("off")).resolves.toMatchObject({
      mode: "off",
    });
    const logs = manager.getLogs("agent-server").join("\n");
    expect(logs).toContain("could not be saved");
    expect(logs).not.toContain("private filesystem detail");
  });

  it("owns and restarts the source backend in managed development mode", async () => {
    const manager = createServiceManager("/tmp/valuz-managed-dev-test", {
      managedDevMode: true,
      devPort: 18080,
    });

    await manager.startAllServices();
    await manager.restartService("agent-server");

    expect(mocks.sidecarOptions).toHaveLength(2);
    expect(mocks.sidecarOptions).toEqual([
      expect.objectContaining({ port: 18080, development: true }),
      expect.objectContaining({ port: 18080, development: true }),
    ]);
    expect(mocks.order).toEqual(["sidecar-stop"]);
    expect(manager.getAgentServerInfo().port).toBe(18080);
  });
});
