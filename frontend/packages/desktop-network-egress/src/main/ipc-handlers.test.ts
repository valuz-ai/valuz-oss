import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_NETWORK_EGRESS_POLICY,
  DESKTOP_CAPABILITIES_CHANNEL,
  NETWORK_EGRESS_CHANNELS,
} from "../contracts";
import { createNetworkEgressIpcHandlers } from "./ipc-handlers";

const status = {
  mode: "off" as const,
  enabled: true,
  started: false,
  emergencyOverride: false,
  snapshotCount: 0,
  diagnosticEventCount: 0,
};

describe("network egress IPC contract", () => {
  it("publishes versioned capabilities and every network handler", () => {
    const handlers = createNetworkEgressIpcHandlers(
      {
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => "off",
        getEgressStatus: () => status,
        getEgressRuntimePhases: () => [],
        setEgressMode: vi.fn(async () => status),
      },
      DEFAULT_NETWORK_EGRESS_POLICY,
    );

    expect(Object.keys(handlers)).toEqual(
      expect.arrayContaining([
        DESKTOP_CAPABILITIES_CHANNEL,
        ...Object.values(NETWORK_EGRESS_CHANNELS),
      ]),
    );
    expect(handlers[DESKTOP_CAPABILITIES_CHANNEL]()).toEqual({
      schemaVersion: 1,
      networkEgress: {
        available: true,
        contractVersion: 1,
        policy: DEFAULT_NETWORK_EGRESS_POLICY,
      },
    });
  });

  it("rejects a user mode outside the host policy", async () => {
    const setEgressMode = vi.fn(async () => status);
    const handlers = createNetworkEgressIpcHandlers(
      {
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => "off",
        getEgressStatus: () => status,
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      {
        defaultMode: "auto",
        allowedModes: ["auto"],
        userConfigurable: false,
      },
    );

    expect(() =>
      handlers[NETWORK_EGRESS_CHANNELS.setMode]({}, { mode: "off" }),
    ).toThrow("egress_mode_not_allowed");
    expect(setEgressMode).not.toHaveBeenCalled();
  });
});
