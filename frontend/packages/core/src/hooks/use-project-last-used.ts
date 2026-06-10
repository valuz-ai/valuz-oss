/**
 * Fetch the most-recent (runtime, provider, model) picked in a project.
 *
 * Powers per-project picker memory: when the user opens a project, the
 * composer pre-fills with whatever they last used **in this project**
 * rather than the global Settings → Default. The hook returns ``null``
 * fields when the project has no prior session — the caller layers
 * this on top of ``useModelDefaults`` so the global default is the
 * fallback.
 *
 * Intentionally not cached: the underlying value changes whenever the
 * user creates a new session in this project, and a stale cached pick
 * would mis-seed the composer next time they revisit. The endpoint
 * does one cheap DB query, so re-fetching on every mount is fine.
 */

import { useEffect, useState } from "react";

import { type LastSessionPick, projectsApi } from "../api/projects-api";

interface UseProjectLastUsedResult {
  pick: LastSessionPick | null;
  loading: boolean;
  error: string | null;
}

export const useProjectLastUsed = (
  projectId: string | null | undefined,
): UseProjectLastUsedResult => {
  const [pick, setPick] = useState<LastSessionPick | null>(null);
  const [loading, setLoading] = useState<boolean>(!!projectId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setPick(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    projectsApi
      .getLastSessionPick(projectId)
      .then((res) => {
        if (cancelled) return;
        setPick(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return { pick, loading, error };
};
