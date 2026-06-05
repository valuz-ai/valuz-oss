import { useMemo, useSyncExternalStore } from "react";
import { subscribe, getSnapshot, t as _t } from "@valuz/shared/i18n";

export function useI18n() {
  // Subscribing to the external store keeps the component re-rendering
  // on locale switch.
  const state = useSyncExternalStore(subscribe, getSnapshot);

  // Memoize the returned object on the snapshot so consumers that put
  // ``t`` in a ``useCallback`` / ``useEffect`` dep list don't see a
  // fresh reference per render. Without this, any
  // ``useCallback(fn, [t])`` recreates each render and any
  // ``useEffect(() => fn(), [fn])`` re-fires — turning page mounts
  // into polling loops against the backend (e.g. the parser settings
  // panel re-fetching ``/v1/settings/parser`` on every render).
  return useMemo(() => {
    function tt(
      key: string,
      fallback?: string | Record<string, string | number>,
      params?: Record<string, string | number>,
    ): string {
      return _t(key as never, fallback as never, params);
    }
    return { t: tt };
    // ``state`` is the i18n snapshot — only changes on locale flip,
    // which is when we DO want to bust the cached ``t``.
  }, [state]);
}
