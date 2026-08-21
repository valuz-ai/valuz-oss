import {
  DESKTOP_CAPABILITIES_CHANNEL,
  NETWORK_EGRESS_CHANNELS,
  NETWORK_EGRESS_CONTRACT_VERSION,
  type DesktopCapabilities,
  type EgressDiagnosticEvent,
  type EgressManagerStatus,
  type EgressMode,
  type EgressSnapshot,
  type NetworkEgressPolicy,
  type RuntimePhaseRecord,
} from "../contracts";

export interface NetworkEgressRuntime {
  getEgressDiagnostics(): EgressDiagnosticEvent[];
  getEgressSnapshots(): EgressSnapshot[];
  getEgressMode(): EgressMode;
  getEgressStatus(): EgressManagerStatus;
  getEgressRuntimePhases(): RuntimePhaseRecord[];
  setEgressMode(
    mode: EgressMode,
    options?: { interruptActiveRuns?: boolean },
  ): Promise<EgressManagerStatus>;
}

export const desktopCapabilities = (
  policy: NetworkEgressPolicy,
  available = true,
): DesktopCapabilities => ({
  schemaVersion: 1,
  networkEgress: {
    available,
    contractVersion: NETWORK_EGRESS_CONTRACT_VERSION,
    policy,
  },
});

export const createNetworkEgressIpcHandlers = (
  runtime: NetworkEgressRuntime,
  policy: NetworkEgressPolicy,
) => ({
  [DESKTOP_CAPABILITIES_CHANNEL]: () => desktopCapabilities(policy),
  [NETWORK_EGRESS_CHANNELS.getDiagnostics]: () =>
    runtime.getEgressDiagnostics(),
  [NETWORK_EGRESS_CHANNELS.getSnapshots]: () => runtime.getEgressSnapshots(),
  [NETWORK_EGRESS_CHANNELS.getMode]: () => runtime.getEgressMode(),
  [NETWORK_EGRESS_CHANNELS.getStatus]: () => runtime.getEgressStatus(),
  [NETWORK_EGRESS_CHANNELS.getRuntimePhases]: () =>
    runtime.getEgressRuntimePhases(),
  [NETWORK_EGRESS_CHANNELS.setMode]: (
    _: unknown,
    payload?: { mode?: EgressMode; interruptActiveRuns?: boolean },
  ) => {
    const mode = payload?.mode;
    if (mode !== "auto" && mode !== "direct" && mode !== "off") {
      throw new Error("invalid_egress_mode");
    }
    if (mode !== "direct") {
      if (policy.lockedMode && mode !== policy.lockedMode) {
        throw new Error("egress_mode_locked");
      }
      if (!policy.userConfigurable || !policy.allowedModes.includes(mode)) {
        throw new Error("egress_mode_not_allowed");
      }
    }
    return runtime.setEgressMode(mode, {
      interruptActiveRuns: payload?.interruptActiveRuns === true,
    });
  },
});
