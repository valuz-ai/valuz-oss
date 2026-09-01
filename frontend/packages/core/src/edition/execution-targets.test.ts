import { afterEach, describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  executionTargetIconKind,
  targetUsesManagedCwd,
  getDefaultExecutionTarget,
  getDefaultRuntimeLocation,
  getExecutionTargets,
  getExecutionTargetsRevision,
  selectableExecutionTargets,
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

  it("keeps the array identity when the same set is re-announced", () => {
    // Editions re-announce on every presence poll. A fresh array identity for
    // an unchanged set re-renders every consumer on that cadence — including
    // the app layout, which then re-renders every page under its outlet.
    setExecutionTargets([LOCAL, CLOUD]);
    const published = getExecutionTargets();
    setExecutionTargets([{ ...LOCAL }, { ...CLOUD }]);
    expect(getExecutionTargets()).toBe(published);
  });

  it("publishes again when a property outside the fan-out set changes", () => {
    // ``labelKey`` does not affect fan-out (the revision stays put), but the
    // pickers render it — so the array still has to be republished.
    setExecutionTargets([LOCAL]);
    const published = getExecutionTargets();
    const revision = getExecutionTargetsRevision();
    setExecutionTargets([{ ...LOCAL, labelKey: "commercial.exec.renamed" }]);
    expect(getExecutionTargets()).not.toBe(published);
    expect(getExecutionTargets()[0]?.labelKey).toBe("commercial.exec.renamed");
    expect(getExecutionTargetsRevision()).toBe(revision);
  });

  it("useExecutionTargets does not re-render on a re-announcement", () => {
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useExecutionTargets();
    });
    act(() => {
      setExecutionTargets([LOCAL, CLOUD]);
    });
    const afterChange = renders;
    act(() => {
      setExecutionTargets([{ ...LOCAL }, { ...CLOUD }]);
    });
    expect(renders).toBe(afterChange);
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
      {
        id: "device:legacy",
        labelKey: "y",
        baseUrl: "http://y",
        icon: "cloud",
      },
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

describe("selectableExecutionTargets", () => {
  it("should keep targets that do not say otherwise", () => {
    expect(selectableExecutionTargets([LOCAL, CLOUD])).toEqual([LOCAL, CLOUD]);
  });

  it("should drop targets an edition marked unselectable", () => {
    // Routable but not offerable: a session hosted on someone else's machine
    // resolves its label and base URL here, yet must never be a *choice* when
    // creating something new.
    const shared = {
      id: "device:owner-mac",
      labelKey: "k",
      baseUrl: "http://relay/owner-mac",
      remote: true,
      selectable: false,
    };
    expect(selectableExecutionTargets([LOCAL, shared, CLOUD])).toEqual([
      LOCAL,
      CLOUD,
    ]);
  });
});

describe("execution target revision", () => {
  it("bumps only when the fan-out set actually changes", () => {
    // Editions re-register on every presence poll. If re-announcing the same
    // set bumped the revision, every list that uses it as an effect dep would
    // refetch on a timer.
    const before = getExecutionTargetsRevision();
    setExecutionTargets([LOCAL, CLOUD]);
    const registered = getExecutionTargetsRevision();
    expect(registered).toBeGreaterThan(before);

    setExecutionTargets([LOCAL, CLOUD]);
    expect(getExecutionTargetsRevision()).toBe(registered);

    // A device coming online is exactly the change lists must react to.
    setExecutionTargets([
      LOCAL,
      CLOUD,
      { id: "device:d1", labelKey: "x", baseUrl: "https://relay/proxy" },
    ]);
    expect(getExecutionTargetsRevision()).toBe(registered + 1);
  });
});
