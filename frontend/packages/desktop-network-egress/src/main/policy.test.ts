import { describe, expect, it } from "vitest";
import {
  DEFAULT_NETWORK_EGRESS_POLICY,
  type NetworkEgressPolicy,
} from "../contracts";
import {
  resolveInitialEgressMode,
  validateNetworkEgressPolicy,
} from "./policy";

const teamPolicy: NetworkEgressPolicy = {
  defaultMode: "auto",
  allowedModes: ["off", "auto"],
  userConfigurable: true,
};

describe("network egress policy", () => {
  it("uses the edition default only when the user has never selected a mode", () => {
    expect(
      resolveInitialEgressMode({ env: {}, persistedMode: null, policy: teamPolicy }),
    ).toBe("auto");
    expect(
      resolveInitialEgressMode({
        env: {},
        persistedMode: "off",
        policy: teamPolicy,
      }),
    ).toBe("off");
  });

  it("applies emergency and locked policy before a persisted preference", () => {
    expect(
      resolveInitialEgressMode({
        env: { VALUZ_EGRESS_MODE: "off" },
        persistedMode: "auto",
        policy: { ...teamPolicy, lockedMode: "auto" },
      }),
    ).toBe("off");
    expect(
      resolveInitialEgressMode({
        env: {},
        persistedMode: "off",
        policy: { ...teamPolicy, lockedMode: "auto" },
      }),
    ).toBe("auto");
  });

  it("falls back to the OSS off policy when edition policy is invalid", () => {
    expect(validateNetworkEgressPolicy({ defaultMode: "direct" })).toEqual({
      valid: false,
      policy: DEFAULT_NETWORK_EGRESS_POLICY,
      reason: "invalid_default_mode",
    });
    expect(validateNetworkEgressPolicy(undefined)).toEqual({
      valid: false,
      policy: DEFAULT_NETWORK_EGRESS_POLICY,
      reason: "policy_not_object",
    });
  });

  it("rejects a saved mode that the current edition no longer permits", () => {
    expect(
      resolveInitialEgressMode({
        env: {},
        persistedMode: "off",
        policy: {
          defaultMode: "auto",
          allowedModes: ["auto"],
          userConfigurable: false,
        },
      }),
    ).toBe("auto");
  });
});
