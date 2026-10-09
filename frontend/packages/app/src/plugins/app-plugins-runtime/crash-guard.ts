/**
 * Crash protection for app plugins (doc 04 §6).
 *
 * A marker is written to localStorage before the plugins load and cleared once
 * they have run for a while (or the window closes normally). A marker that is
 * still there at the next start means the renderer died while plugins were
 * loaded; two starts in a row like that put the app in safe mode.
 */
export const LOADING_MARKER_KEY = "valuz.appPlugins.loading";
export const CRASH_COUNT_KEY = "valuz.appPlugins.crashes";
export const CRASHES_BEFORE_SAFE_MODE = 2;
export const HEALTHY_AFTER_MS = 10_000;

export interface StartupVerdict {
  /** Consecutive starts that found the marker left behind. */
  crashes: number;
  /** Enter safe mode and load nothing. */
  safeMode: boolean;
}

export interface CrashGuard {
  /** Read what the previous start left behind. Call once per start. */
  evaluateStartup(): StartupVerdict;
  /** About to load plugins. */
  markLoading(): void;
  /** Clear the marker ~``HEALTHY_AFTER_MS`` from now. */
  scheduleHealthy(): void;
  /** Clear the marker and the counter now. */
  markHealthy(): void;
  dispose(): void;
}

export function createCrashGuard(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
  options: { healthyAfterMs?: number; target?: Pick<Window, "addEventListener" | "removeEventListener"> | null } = {},
): CrashGuard {
  const healthyAfter = options.healthyAfterMs ?? HEALTHY_AFTER_MS;
  const target = options.target ?? null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const read = (key: string): string | null => {
    try {
      return storage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  };
  const write = (key: string, value: string): void => {
    try {
      storage?.setItem(key, value);
    } catch {
      // storage unavailable: no crash protection, no crash either
    }
  };
  const remove = (key: string): void => {
    try {
      storage?.removeItem(key);
    } catch {
      // see above
    }
  };

  // A normal window close is not a crash: only a renderer that died leaves
  // the marker behind.
  const onPageHide = (): void => remove(LOADING_MARKER_KEY);
  target?.addEventListener("pagehide", onPageHide);

  const cancelTimer = (): void => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  return {
    evaluateStartup() {
      const left = read(LOADING_MARKER_KEY) !== null;
      const crashes = left ? Number(read(CRASH_COUNT_KEY) ?? "0") + 1 : 0;
      if (!left) {
        remove(CRASH_COUNT_KEY);
        return { crashes: 0, safeMode: false };
      }
      if (crashes >= CRASHES_BEFORE_SAFE_MODE) {
        remove(LOADING_MARKER_KEY);
        remove(CRASH_COUNT_KEY);
        return { crashes, safeMode: true };
      }
      write(CRASH_COUNT_KEY, String(crashes));
      return { crashes, safeMode: false };
    },
    markLoading() {
      write(LOADING_MARKER_KEY, String(Date.now()));
    },
    scheduleHealthy() {
      cancelTimer();
      timer = setTimeout(() => {
        timer = null;
        remove(LOADING_MARKER_KEY);
        remove(CRASH_COUNT_KEY);
      }, healthyAfter);
    },
    markHealthy() {
      cancelTimer();
      remove(LOADING_MARKER_KEY);
      remove(CRASH_COUNT_KEY);
    },
    dispose() {
      cancelTimer();
      target?.removeEventListener("pagehide", onPageHide);
    },
  };
}
