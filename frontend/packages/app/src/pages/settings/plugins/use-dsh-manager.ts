import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, describeDshApiError, dshPluginsApi } from "@valuz/core";
import type { DshBundleInfo, DshManagerStatus } from "@valuz/core";

/**
 * Where the DSH block is in its life:
 * - ``checking``    asking ``/status`` (never starts the manager);
 * - ``unavailable`` disabled (cloud) or not installable here — not an error;
 * - ``connecting``  available; ``listBundles`` is starting the resident host
 *                   on first use (up to ~30s);
 * - ``ready``       the bundle list is loaded;
 * - ``error``       an unexpected failure, retryable.
 */
export type DshManagerState =
  | { phase: "checking" }
  | {
      phase: "unavailable";
      status: DshManagerStatus | null;
      /** Backend's own explanation, when it gave one. */
      reason: string | null;
    }
  | { phase: "connecting"; status: DshManagerStatus }
  | {
      phase: "ready";
      status: DshManagerStatus;
      bundles: DshBundleInfo[];
    }
  | { phase: "error"; message: string };

/** The route is absent (a build that does not mount it) or the manager cannot run. */
const isUnsupported = (error: unknown): boolean =>
  error instanceof ApiError &&
  (error.status === 409 || error.status === 404 || error.status === 501);

export function useDshManager() {
  const [state, setState] = useState<DshManagerState>({ phase: "checking" });
  // A newer load (or unmount) makes every older in-flight response stale.
  const generation = useRef(0);
  const invalidate = useCallback(() => {
    generation.current += 1;
  }, []);

  const load = useCallback(async () => {
    generation.current += 1;
    const mine = generation.current;
    const current = () => generation.current === mine;

    setState({ phase: "checking" });
    let status: DshManagerStatus;
    try {
      status = await dshPluginsApi.status();
    } catch (error) {
      if (!current()) return;
      setState(
        isUnsupported(error)
          ? { phase: "unavailable", status: null, reason: null }
          : { phase: "error", message: describeDshApiError(error).message },
      );
      return;
    }
    if (!current()) return;
    if (!status.enabled || !status.available) {
      setState({
        phase: "unavailable",
        status,
        reason: status.unavailable_reason,
      });
      return;
    }

    setState({ phase: "connecting", status });
    try {
      const bundles = await dshPluginsApi.listBundles();
      if (current()) setState({ phase: "ready", status, bundles });
    } catch (error) {
      if (!current()) return;
      setState(
        isUnsupported(error)
          ? {
              phase: "unavailable",
              status,
              reason: describeDshApiError(error).message,
            }
          : { phase: "error", message: describeDshApiError(error).message },
      );
    }
  }, []);

  /** Re-read the bundle list in place (no flicker back to the loading state). */
  const refreshBundles = useCallback(async () => {
    const mine = generation.current;
    try {
      const bundles = await dshPluginsApi.listBundles();
      if (generation.current !== mine) return;
      setState((prev) =>
        prev.phase === "ready" ? { ...prev, bundles } : prev,
      );
    } catch (error) {
      if (generation.current !== mine) return;
      setState({ phase: "error", message: describeDshApiError(error).message });
    }
  }, []);

  useEffect(() => {
    void load();
    return invalidate;
  }, [load, invalidate]);

  return { state, reload: load, refreshBundles };
}
