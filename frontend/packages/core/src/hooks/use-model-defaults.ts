/**
 * Fetch + cache the global "Default model" tuple from Settings.
 *
 * Mirrors ``use-runtimes`` shape: module-level cache so the first
 * subscriber loads from the network and every subsequent mount reads
 * the warm copy. Composer pages call this to seed their (runtime,
 * provider, model) pickers so a fresh chat reflects the user's
 * Settings → Default pick without a manual click.
 *
 * A ``refresh()`` exit lets the Settings page invalidate the cache
 * after PATCH so other open composers re-pick up the new default.
 */

import { useEffect, useState } from "react";

import { type ModelDefaults, settingsApi } from "../api/settings-api";

interface UseModelDefaultsResult {
  defaults: ModelDefaults | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

let _cache: ModelDefaults | null = null;

export const useModelDefaults = (): UseModelDefaultsResult => {
  const [defaults, setDefaults] = useState<ModelDefaults | null>(_cache);
  const [loading, setLoading] = useState<boolean>(_cache === null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    settingsApi
      .getModelDefaults()
      .then((res) => {
        if (cancelled) return;
        _cache = res;
        setDefaults(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  return {
    defaults,
    loading,
    error,
    refresh: () => {
      _cache = null;
      setTick((t) => t + 1);
    },
  };
};

/** Test helper — wipes the module-level cache so each test starts fresh. */
export const __clearModelDefaultsCacheForTests = (): void => {
  _cache = null;
};
