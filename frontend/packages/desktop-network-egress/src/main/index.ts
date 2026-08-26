export {
  EgressManager,
  captureProxyEnvironment,
  resolveEgressFrontendsEnabled,
} from "./egress-manager";
export type {
  EgressManagerOptions,
  ShadowResolveRequest,
} from "./egress-manager";
export {
  readPersistedEgressMode,
  writePersistedEgressMode,
} from "./mode-store";
export {
  resolveInitialEgressMode,
  validateNetworkEgressPolicy,
} from "./policy";
export type { NetworkEgressPolicyValidation } from "./policy";
export {
  createNetworkEgressIpcHandlers,
  desktopCapabilities,
} from "./ipc-handlers";
export type { NetworkEgressRuntime } from "./ipc-handlers";
export {
  interruptActiveModelRuns,
  probeActiveModelRuns,
  reconfigureRuntimeEgress,
} from "./desktop-control-client";
