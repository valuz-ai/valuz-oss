import { describe, expect, it, vi } from "vitest";
import {
  interruptActiveModelRuns,
  probeActiveModelRuns,
  reconfigureRuntimeEgress,
} from "./desktop-control-client";

describe("desktop control client", () => {
  it("uses the memory-only token to inspect process-local activity", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({ active_session_ids: ["b", "a", "a"] }),
        { status: 200 },
      ),
    );

    await expect(
      probeActiveModelRuns(19100, "desktop-token", fetchImpl),
    ).resolves.toEqual(["b", "a"]);
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:19100/v1/system/network-egress/activity",
      expect.objectContaining({
        headers: { "x-valuz-desktop-token": "desktop-token" },
      }),
    );
  });

  it("fails closed when activity cannot be authorized or decoded", async () => {
    await expect(
      probeActiveModelRuns(
        19100,
        "bad-token",
        vi.fn(async () => new Response(null, { status: 401 })),
      ),
    ).rejects.toThrow("egress_activity_probe_failed_401");
    await expect(
      probeActiveModelRuns(
        19100,
        "desktop-token",
        vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
      ),
    ).rejects.toThrow("egress_activity_probe_invalid_response");
  });

  it("interrupts only the session ids confirmed by the caller", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    await interruptActiveModelRuns(
      19100,
      "desktop-token",
      ["session-a", "session-a", "session-b"],
      fetchImpl,
    );

    const call = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(call[1].body as string)).toEqual({
      session_ids: ["session-a", "session-b"],
    });
  });

  it("distinguishes a still-busy backend from an old or failed backend", async () => {
    const busy = vi.fn(async () => new Response(null, { status: 409 }));
    vi.useFakeTimers();
    const pending = reconfigureRuntimeEgress(
      19100,
      "desktop-token",
      null,
      false,
      busy,
    );
    const assertion = expect(pending).rejects.toThrow(
      "egress_runtime_reconfigure_busy",
    );
    await vi.advanceTimersByTimeAsync(5_100);
    await assertion;
    vi.useRealTimers();
  });
});
