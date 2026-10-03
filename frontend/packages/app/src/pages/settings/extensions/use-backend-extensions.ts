import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, extensionsApi } from "@valuz/core";
import type { BackendExtensionList } from "@valuz/core";

export type BackendExtensionsState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; data: BackendExtensionList };

/** A backend with no plugin host — also what a build without the route means. */
const NOT_COMPOSED: BackendExtensionList = {
  composed: false,
  editable: false,
  plugins: [],
  config_schemas: {},
};

export function useBackendExtensions() {
  const [state, setState] = useState<BackendExtensionsState>({
    phase: "loading",
  });
  // A newer load (or unmount) makes every older in-flight response stale.
  const generation = useRef(0);
  const invalidate = useCallback(() => {
    generation.current += 1;
  }, []);

  /** ``silent`` re-reads in place: no flicker back to the loading state, and a
   * failed re-read keeps what is on screen. */
  const load = useCallback(async (options?: { silent?: boolean }) => {
    generation.current += 1;
    const mine = generation.current;
    const silent = options?.silent === true;
    if (!silent) setState({ phase: "loading" });
    try {
      const data = await extensionsApi.listBackendExtensions();
      if (generation.current === mine) setState({ phase: "ready", data });
    } catch (error) {
      if (generation.current !== mine) return;
      if (error instanceof ApiError && error.status === 404) {
        setState({ phase: "ready", data: NOT_COMPOSED });
      } else if (!silent) {
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }, []);

  /** Mirror a toggle the server just accepted, without waiting for a re-read. */
  const markEnabled = useCallback((id: string, enabled: boolean) => {
    setState((prev) =>
      prev.phase === "ready"
        ? {
            ...prev,
            data: {
              ...prev.data,
              plugins: prev.data.plugins.map((plugin) =>
                plugin.id === id
                  ? { ...plugin, desiredEnabled: enabled }
                  : plugin,
              ),
            },
          }
        : prev,
    );
  }, []);

  useEffect(() => {
    void load();
    return invalidate;
  }, [load, invalidate]);

  return { state, reload: load, markEnabled };
}
