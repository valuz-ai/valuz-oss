/**
 * Fetch + cache the Runtime Agent registry from the backend.
 *
 * The session-creation form uses this to render the Runtime picker
 * (Claude Agent / Codex Agent / Valuz Agent), and to grey out runtimes
 * whose binary isn't installed (only Codex needs one). Per CLAUDE.md
 * the store is Zustand-only — but for a small read-mostly metadata
 * call this lightweight ``useState`` + module-level cache pattern is
 * the same shape used by other API consumers (see how registries flow
 * in ``edition/registry-store.ts``); pulling in a full Zustand slice
 * for "fetched once on mount" data would be over-engineered.
 */

import { useEffect, useState } from "react";

import { runtimesApi, type RuntimeListItem } from "../api/runtimes-api";

interface UseRuntimesResult {
  runtimes: RuntimeListItem[];
  loading: boolean;
  error: string | null;
  /** Force a refetch (e.g. after the user installs the codex binary). */
  refresh: () => void;
}

let _cache: RuntimeListItem[] | null = null;

export const useRuntimes = (): UseRuntimesResult => {
  const [runtimes, setRuntimes] = useState<RuntimeListItem[]>(_cache ?? []);
  const [loading, setLoading] = useState<boolean>(_cache === null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    runtimesApi
      .list()
      .then((res) => {
        if (cancelled) return;
        _cache = res.runtimes;
        setRuntimes(res.runtimes);
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
    runtimes,
    loading,
    error,
    refresh: () => setTick((t) => t + 1),
  };
};

/** Test helper — wipes the module-level cache so each test starts fresh. */
export const __clearRuntimesCacheForTests = (): void => {
  _cache = null;
};
