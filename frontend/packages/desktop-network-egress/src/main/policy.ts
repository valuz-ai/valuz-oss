import type { EgressMode, NetworkEgressPolicy, PublicEgressMode } from "../contracts";
import { DEFAULT_NETWORK_EGRESS_POLICY } from "../contracts";

export interface NetworkEgressPolicyValidation {
  valid: boolean;
  policy: NetworkEgressPolicy;
  reason?: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isPublicMode = (value: unknown): value is PublicEgressMode =>
  value === "off" || value === "auto";

const invalid = (reason: string): NetworkEgressPolicyValidation => ({
  valid: false,
  policy: DEFAULT_NETWORK_EGRESS_POLICY,
  reason,
});

export const validateNetworkEgressPolicy = (
  value: unknown,
): NetworkEgressPolicyValidation => {
  if (!isRecord(value)) return invalid("policy_not_object");
  if (!isPublicMode(value.defaultMode)) return invalid("invalid_default_mode");
  if (!Array.isArray(value.allowedModes) || value.allowedModes.length === 0) {
    return invalid("invalid_allowed_modes");
  }
  if (!value.allowedModes.every(isPublicMode)) {
    return invalid("invalid_allowed_modes");
  }
  const allowedModes = [...new Set(value.allowedModes)] as PublicEgressMode[];
  if (!allowedModes.includes(value.defaultMode)) {
    return invalid("default_mode_not_allowed");
  }
  if (typeof value.userConfigurable !== "boolean") {
    return invalid("invalid_user_configurable");
  }
  if (value.lockedMode !== undefined) {
    if (!isPublicMode(value.lockedMode)) return invalid("invalid_locked_mode");
    if (!allowedModes.includes(value.lockedMode)) {
      return invalid("locked_mode_not_allowed");
    }
  }
  return {
    valid: true,
    policy: {
      defaultMode: value.defaultMode,
      allowedModes,
      userConfigurable: value.userConfigurable,
      ...(value.lockedMode === undefined
        ? {}
        : { lockedMode: value.lockedMode }),
    },
  };
};

export const resolveInitialEgressMode = (options: {
  env: NodeJS.ProcessEnv;
  persistedMode?: EgressMode | null;
  policy?: NetworkEgressPolicy;
}): EgressMode => {
  const policy = options.policy ?? DEFAULT_NETWORK_EGRESS_POLICY;
  if (options.env.VALUZ_EGRESS_MODE?.trim().toLowerCase() === "off") {
    return "off";
  }
  if (policy.lockedMode) return policy.lockedMode;
  const persistedMode =
    options.persistedMode === "direct" ? "auto" : options.persistedMode;
  if (persistedMode && policy.allowedModes.includes(persistedMode)) {
    return persistedMode;
  }
  if (policy.allowedModes.includes(policy.defaultMode)) {
    return policy.defaultMode;
  }
  return "off";
};
