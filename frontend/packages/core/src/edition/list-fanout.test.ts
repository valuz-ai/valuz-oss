import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  DEGRADED_REPROBE_MS,
  fanOutTargets,
  getListFanOutTargets,
  LIST_TARGET_TIMEOUT_MS,
  useDegradedListTargets,
} from "./list-fanout";
import { setExecutionTargets } from "./execution-targets";

const LOCAL = {
  id: "local",
  labelKey: "l",
  baseUrl: "http://localhost:8000",
  isDefault: true,
};
const CLOUD = { id: "cloud", labelKey: "c", baseUrl: "http://cloud:8010" };

afterEach(async () => {
  setExecutionTargets([]);
  // A zero-target fan-out publishes an empty failure set — resets the
  // module-level degraded store between tests.
  await fanOutTargets(() => Promise.resolve(null));
});

describe("getListFanOutTargets", () => {
  it("is empty with zero or one registered target", () => {
    expect(getListFanOutTargets()).toEqual([]);
    setExecutionTargets([LOCAL]);
    expect(getListFanOutTargets()).toEqual([]);
  });

  it("returns all targets when two or more are registered", () => {
    setExecutionTargets([LOCAL, CLOUD]);
    expect(getListFanOutTargets().map((t) => t.id)).toEqual(["local", "cloud"]);
  });

  it("skips narrow-grant targets — they cannot be enumerated", () => {
    // Fanning out to one only yields a refusal, which would pin the
    // "list may be incomplete" banner on forever.
    setExecutionTargets([
      { id: "local", labelKey: "l", baseUrl: "http://local" },
      { id: "cloud", labelKey: "c", baseUrl: "http://cloud" },
      {
        id: "device:owner-mac",
        labelKey: "d",
        baseUrl: "http://relay/owner-mac",
        selectable: false,
      },
    ]);
    expect(getListFanOutTargets().map((t) => t.id)).toEqual(["local", "cloud"]);
  });
});

describe("fanOutTargets", () => {
  it("collects fulfilled values in registration order", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const outcome = await fanOutTargets((target) =>
      Promise.resolve(`from-${target.id}`),
    );
    expect(outcome.values.map((v) => v.value)).toEqual([
      "from-local",
      "from-cloud",
    ]);
    expect(outcome.failedTargets).toEqual([]);
  });

  it("keeps the healthy side when one target fails (degraded)", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const outcome = await fanOutTargets((target) =>
      target.id === "cloud"
        ? Promise.reject(new Error("down"))
        : Promise.resolve("ok"),
    );
    expect(outcome.values).toHaveLength(1);
    expect(outcome.values[0]!.target.id).toBe("local");
    expect(outcome.failedTargets).toEqual(["cloud"]);
  });

  it("throws only when every target fails", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    await expect(
      fanOutTargets(() => Promise.reject(new Error("all down"))),
    ).rejects.toThrow("all down");
  });

  it("degrades and aborts a target that never settles instead of pinning the list", async () => {
    // A black-holed backend accepts the connection and never responds —
    // browser fetch has no default timeout, so without the per-target race
    // this fan-out would await forever and every list surface would sit on
    // "loading" despite the healthy target having answered. The timeout must
    // also fire the target's AbortSignal, otherwise every poll tick leaks one
    // hung connection until the browser's per-origin limit starves the rest.
    vi.useFakeTimers();
    try {
      setExecutionTargets([LOCAL, CLOUD]);
      let cloudSignal: AbortSignal | undefined;
      const outcome = fanOutTargets((target, signal) => {
        if (target.id === "cloud") {
          cloudSignal = signal;
          return new Promise<string>(() => {}); // never settles
        }
        return Promise.resolve("ok");
      });
      await vi.advanceTimersByTimeAsync(LIST_TARGET_TIMEOUT_MS + 1);
      const { values, failedTargets } = await outcome;
      expect(values).toHaveLength(1);
      expect(values[0]!.target.id).toBe("local");
      expect(failedTargets).toEqual(["cloud"]);
      expect(cloudSignal?.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-probes degraded targets and clears the banner when they recover", async () => {
    // The hint is set by failed requests and cleared by successful ones — on
    // a quiet page no further list fetch may ever run, so recovery must be
    // active: replay the last fan-out against the failed target on a timer.
    vi.useFakeTimers();
    try {
      setExecutionTargets([LOCAL, CLOUD]);
      let cloudHealthy = false;
      const fetchOne = (target: { id: string }) =>
        target.id === "cloud" && !cloudHealthy
          ? Promise.reject(new Error("down"))
          : Promise.resolve("ok");
      const { result } = renderHook(() => useDegradedListTargets());
      await act(async () => {
        await fanOutTargets(fetchOne);
      });
      expect(result.current).toEqual(["cloud"]);

      // First probe while still down: banner stays, probe reschedules.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DEGRADED_REPROBE_MS + 1);
      });
      expect(result.current).toEqual(["cloud"]);

      // Backend recovers: the next probe clears the banner without any list
      // surface having refreshed.
      cloudHealthy = true;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DEGRADED_REPROBE_MS + 1);
      });
      expect(result.current).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("publishes and clears the degraded-targets store", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const { result } = renderHook(() => useDegradedListTargets());
    expect(result.current).toEqual([]);
    await act(async () => {
      await fanOutTargets((target) =>
        target.id === "cloud"
          ? Promise.reject(new Error("down"))
          : Promise.resolve("ok"),
      );
    });
    expect(result.current).toEqual(["cloud"]);
    await act(async () => {
      await fanOutTargets(() => Promise.resolve("ok"));
    });
    expect(result.current).toEqual([]);
  });
});

describe("sessions list fan-out (api integration)", () => {
  it("merges, tags exec_origin, and hits both bases", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const fetchSpy = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const sessions = url.startsWith(CLOUD.baseUrl)
        ? [{ id: "s-cloud", project_id: "p2" }]
        : [{ id: "s-local", project_id: "p1" }];
      return Promise.resolve(
        new Response(JSON.stringify({ sessions }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchSpy);
    try {
      const { sessionsApi } = await import("../api/sessions-api");
      const { sessions } = await sessionsApi.list();
      expect(sessions.map((s) => s.id).sort()).toEqual(["s-cloud", "s-local"]);
      expect(sessions.find((s) => s.id === "s-cloud")?.exec_origin).toBe(
        "cloud",
      );
      expect(sessions.find((s) => s.id === "s-local")?.exec_origin).toBe(
        "local",
      );
      const urls = fetchSpy.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.startsWith(LOCAL.baseUrl))).toBe(true);
      expect(urls.some((u) => u.startsWith(CLOUD.baseUrl))).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
