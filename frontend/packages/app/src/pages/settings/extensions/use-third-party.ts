import { useCallback, useEffect, useState } from "react";
import { thirdPartyApi } from "@valuz/core";
import type { PluginStatus, ThirdPartyList } from "@valuz/core";
import { useThirdPartyRuntime } from "../../../plugins/third-party-runtime";
import { errorDetail, isUnavailableError } from "./third-party-helpers";

export type ThirdPartyState =
  | { phase: "loading" }
  /** 403 / 404: a cloud deployment, or a build without ``oss-third-party``. */
  | { phase: "unavailable" }
  | { phase: "error"; message: string }
  | { phase: "ready"; data: ThirdPartyList };

/** Pause before asking again after the long poll failed (backend restarting). */
const RETRY_DELAY_MS = 3000;

const sleep = (ms: number, signal: AbortSignal): Promise<void> =>
  new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });

/**
 * The installed third-party plugins, kept live: the list is read once, then a
 * long poll on ``watch`` re-reads it whenever the backend's ``generation``
 * moves — so an install from the CLI or from the agent appears without
 * reopening the page. ``refresh`` re-reads in place (no flicker back to the
 * loading state) and also restarts a loop that stopped on an error.
 */
export function useThirdParty() {
  const [state, setState] = useState<ThirdPartyState>({ phase: "loading" });
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    const run = async () => {
      let generation: number | null = null;
      while (!signal.aborted) {
        try {
          if (generation === null) {
            const data = await thirdPartyApi.list();
            if (signal.aborted) return;
            generation = data.generation;
            setState({ phase: "ready", data });
          } else {
            const next = await thirdPartyApi.watch(generation, 25, signal);
            if (signal.aborted) return;
            if (next.generation !== generation) {
              const data = await thirdPartyApi.list();
              if (signal.aborted) return;
              generation = data.generation;
              setState({ phase: "ready", data });
            }
          }
        } catch (error) {
          if (signal.aborted) return;
          if (isUnavailableError(error)) {
            setState({ phase: "unavailable" });
            return;
          }
          if (generation === null) {
            // The first read failed: say so and stop; "Retry" restarts it.
            setState({ phase: "error", message: errorDetail(error) });
            return;
          }
          // The long poll failed after a good read: keep what is on screen.
          await sleep(RETRY_DELAY_MS, signal);
        }
      }
    };

    setState((prev) => (prev.phase === "ready" ? prev : { phase: "loading" }));
    void run();
    return () => controller.abort();
  }, [epoch]);

  const refresh = useCallback(() => setEpoch((value) => value + 1), []);
  return { state, refresh };
}

/** What the renderer reports for one plugin's frontend. */
export interface ThirdPartyLoadState {
  status: PluginStatus;
  error?: unknown;
}

/**
 * The renderer-side load state of each third-party plugin, by plugin id, from
 * the ``oss-third-party`` runtime (``useThirdPartyRuntime``). A plugin the
 * runtime did not load (disabled, incompatible, safe mode …) has no entry: its
 * backend status already says why.
 */
export function useThirdPartyLoadStates(): ReadonlyMap<
  string,
  ThirdPartyLoadState
> {
  const { plugins } = useThirdPartyRuntime();
  const byId = new Map<string, ThirdPartyLoadState>();
  for (const state of Object.values(plugins)) {
    if (state.phase === "skipped") continue;
    byId.set(state.id, { status: state.phase, error: state.error });
  }
  return byId;
}
