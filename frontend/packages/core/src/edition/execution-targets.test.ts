import { afterEach, describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  executionTargetIconKind,
  targetUsesManagedCwd,
  getDefaultExecutionTarget,
  getDefaultRuntimeLocation,
  getExecutionTargets,
  setDefaultRuntimeLocation,
  setExecutionTargets,
  useDefaultRuntimeLocation,
  useExecutionTargets,
  type ExecutionTarget,
} from "./execution-targets";

const LOCAL: ExecutionTarget = {
  id: "local",
  labelKey: "commercial.exec.local",
  baseUrl: "http://localhost:8000",
  isDefault: true,
};
const CLOUD: ExecutionTarget = {
  id: "cloud",
  labelKey: "commercial.exec.cloud",
  baseUrl: "http://cloud:8010",
};

afterEach(() => {
  setExecutionTargets([]);
  setDefaultRuntimeLocation("local");
});

describe("execution targets registry", () => {
  it("is empty by default (OSS single-backend)", () => {
    expect(getExecutionTargets()).toEqual([]);
    expect(getDefaultExecutionTarget()).toBeUndefined();
  });

  it("returns registered targets and the flagged default", () => {
    setExecutionTargets([CLOUD, LOCAL]);
    expect(getExecutionTargets()).toHaveLength(2);
    expect(getDefaultExecutionTarget()?.id).toBe("local");
  });

  it("falls back to the first target when none is flagged default", () => {
    setExecutionTargets([CLOUD, { ...LOCAL, isDefault: false }]);
    expect(getDefaultExecutionTarget()?.id).toBe("cloud");
  });

  it("copies the input array so later caller mutation is invisible", () => {
    const input = [LOCAL];
    setExecutionTargets(input);
    input.push(CLOUD);
    expect(getExecutionTargets()).toHaveLength(1);
  });

  it("useExecutionTargets re-renders on registry change", () => {
    const { result } = renderHook(() => useExecutionTargets());
    expect(result.current).toEqual([]);
    act(() => {
      setExecutionTargets([LOCAL, CLOUD]);
    });
    expect(result.current.map((t) => t.id)).toEqual(["local", "cloud"]);
  });
});

describe("default runtime location", () => {
  it("should be local by default (OSS backend is a sidecar on this machine)", () => {
    expect(getDefaultRuntimeLocation()).toBe("local");
  });

  it("should report cloud when a browser-only edition declares it", () => {
    setDefaultRuntimeLocation("cloud");
    expect(getDefaultRuntimeLocation()).toBe("cloud");
  });

  it("useDefaultRuntimeLocation re-renders when the declaration lands", () => {
    const { result } = renderHook(() => useDefaultRuntimeLocation());
    expect(result.current).toBe("local");
    act(() => {
      setDefaultRuntimeLocation("cloud");
    });
    expect(result.current).toBe("cloud");
  });
});

describe("executionTargetIconKind", () => {
  it("should infer local / cloud / device from the id when no target is registered", () => {
    expect(executionTargetIconKind("local")).toBe("local");
    expect(executionTargetIconKind("cloud")).toBe("cloud");
    expect(executionTargetIconKind("device:abc123")).toBe("device");
    expect(executionTargetIconKind("something-else")).toBe("local");
  });

  it("should prefer an explicit icon on the registered target", () => {
    setExecutionTargets([
      LOCAL,
      { id: "edge-1", labelKey: "x", baseUrl: "http://edge", icon: "device" },
      { id: "device:legacy", labelKey: "y", baseUrl: "http://y", icon: "cloud" },
    ]);
    expect(executionTargetIconKind("edge-1")).toBe("device");
    expect(executionTargetIconKind("device:legacy")).toBe("cloud");
  });

  it("should use the passed target without consulting the registry", () => {
    expect(
      executionTargetIconKind("whatever", {
        id: "whatever",
        labelKey: "k",
        baseUrl: "http://w",
        icon: "cloud",
      }),
    ).toBe("cloud");
  });
});

describe("targetUsesManagedCwd", () => {
  it("should be false for local / undefined targets", () => {
    expect(targetUsesManagedCwd(undefined)).toBe(false);
    expect(targetUsesManagedCwd(LOCAL)).toBe(false);
  });

  it("should be true for a remote target without its own directory chooser", () => {
    expect(targetUsesManagedCwd({ ...CLOUD, remote: true })).toBe(true);
  });

  it("should be false for a remote target that can browse its own filesystem", () => {
    expect(
      targetUsesManagedCwd({
        id: "device:x",
        labelKey: "k",
        baseUrl: "http://relay/x",
        remote: true,
        selectDirectory: async () => ({ path: "/Users/me/proj" }),
      }),
    ).toBe(false);
  });
});
