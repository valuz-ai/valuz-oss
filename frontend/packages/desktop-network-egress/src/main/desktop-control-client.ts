import type { EgressBootstrap } from "../contracts";

type FetchLike = typeof fetch;

const withTimeout = async <T>(
  timeoutMs: number,
  action: (signal: AbortSignal) => Promise<T>,
): Promise<T> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await action(controller.signal);
  } finally {
    clearTimeout(timeout);
  }
};

const desktopHeaders = (token: string): Record<string, string> => ({
  "x-valuz-desktop-token": token,
});

export const probeActiveModelRuns = async (
  port: number,
  token: string,
  fetchImpl: FetchLike = fetch,
): Promise<string[]> =>
  withTimeout(1_500, async (signal) => {
    const response = await fetchImpl(
      `http://127.0.0.1:${port}/v1/system/network-egress/activity`,
      { headers: desktopHeaders(token), signal },
    );
    if (!response.ok) {
      throw new Error(`egress_activity_probe_failed_${response.status}`);
    }
    const payload = (await response.json()) as {
      active_session_ids?: unknown;
    };
    if (
      !Array.isArray(payload.active_session_ids) ||
      !payload.active_session_ids.every((value) => typeof value === "string")
    ) {
      throw new Error("egress_activity_probe_invalid_response");
    }
    return [...new Set(payload.active_session_ids)];
  });

export const interruptActiveModelRuns = async (
  port: number,
  token: string,
  sessionIds: string[],
  fetchImpl: FetchLike = fetch,
): Promise<void> => {
  await withTimeout(70_000, async (signal) => {
    const response = await fetchImpl(
      `http://127.0.0.1:${port}/v1/system/network-egress/interrupt`,
      {
        method: "POST",
        headers: {
          ...desktopHeaders(token),
          "content-type": "application/json",
        },
        body: JSON.stringify({ session_ids: [...new Set(sessionIds)] }),
        signal,
      },
    );
    if (!response.ok) {
      throw new Error(`egress_activity_interrupt_failed_${response.status}`);
    }
  });
};

export const reconfigureRuntimeEgress = async (
  port: number,
  token: string,
  bootstrap: EgressBootstrap | null,
  requiredUnavailable: boolean,
  fetchImpl: FetchLike = fetch,
): Promise<void> => {
  await withTimeout(120_000, async (signal) => {
    const body = JSON.stringify({
      bootstrap,
      required_unavailable: requiredUnavailable,
      prewarm_limit: 1,
    });
    const activeDrainDeadline = Date.now() + 5_000;
    while (true) {
      const response = await fetchImpl(
        `http://127.0.0.1:${port}/v1/system/network-egress`,
        {
          method: "POST",
          headers: {
            ...desktopHeaders(token),
            "content-type": "application/json",
          },
          body,
          signal,
        },
      );
      if (response.ok) return;
      if (response.status === 409 && Date.now() < activeDrainDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        continue;
      }
      if (response.status === 409) {
        throw new Error("egress_runtime_reconfigure_busy");
      }
      throw new Error(`egress_runtime_reconfigure_failed_${response.status}`);
    }
  });
};
