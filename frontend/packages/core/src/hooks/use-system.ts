/**
 * Hooks for the desktop ``服务`` panel: backend status polling + log
 * stream subscription.
 *
 * Status side: ``useSystemStatus`` polls ``GET /v1/system/status`` at a
 * configurable cadence (default 5s) and stores the response in the
 * Zustand ``useSystemStore``. Components read from the store; the
 * hook only orchestrates fetches.
 *
 * Logs side: ``useSystemLogs`` first asks Electron main for the ring
 * snapshot (history), then subscribes to the ``system:log-line``
 * event for new lines. Both flow through ``useSystemStore``. When
 * running outside Electron (e.g. webui), the IPC bridge is absent and
 * the hooks degrade to empty + a recorded warning rather than
 * crashing — surfacing a non-fatal "logs unavailable" state in the UI.
 */

import { useEffect } from "react";
import type { LogLine } from "@valuz/shared";
import { useSystemStore } from "../store/system-store";

const STATUS_POLL_MS = 5_000;

interface DesktopBridge {
  invoke: <T>(channel: string, payload?: Record<string, unknown>) => Promise<T>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
}

const getDesktopBridge = (): DesktopBridge | null => {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { valuzDesktop?: DesktopBridge };
  return w.valuzDesktop ?? null;
};

/** Poll ``GET /v1/system/status`` while the component is mounted. */
export const useSystemStatus = (
  options: { intervalMs?: number; enabled?: boolean } = {},
): void => {
  const { intervalMs = STATUS_POLL_MS, enabled = true } = options;
  const refreshStatus = useSystemStore((s) => s.refreshStatus);

  useEffect(() => {
    if (!enabled) return;
    void refreshStatus();
    const t = window.setInterval(() => {
      void refreshStatus();
    }, intervalMs);
    return () => window.clearInterval(t);
  }, [enabled, intervalMs, refreshStatus]);
};

/** Subscribe to backend logs via Electron IPC. No-op when running
 *  outside the desktop shell (the hook still runs, just empty). */
export const useSystemLogs = (
  options: { enabled?: boolean } = {},
): { available: boolean } => {
  const { enabled = true } = options;
  const setLogs = useSystemStore((s) => s.setLogs);
  const appendLog = useSystemStore((s) => s.appendLog);

  const bridge = getDesktopBridge();
  const available = bridge !== null;

  useEffect(() => {
    if (!enabled || !bridge) return;
    let cancelled = false;

    const handler = (payload: unknown) => {
      if (cancelled) return;
      // Electron main sends a single LogLine per event.
      appendLog(payload as LogLine);
    };

    // Initial backfill.
    void bridge
      .invoke<LogLine[]>("system:get-log-snapshot")
      .then((snapshot) => {
        if (!cancelled && Array.isArray(snapshot)) {
          setLogs(snapshot);
        }
      })
      .catch(() => {
        // Ignore — empty state is fine.
      });

    // Live subscription.
    void bridge.invoke("system:subscribe-logs").catch(() => {});
    bridge.on("system:log-line", handler);

    return () => {
      cancelled = true;
      bridge.off("system:log-line", handler);
      void bridge.invoke("system:unsubscribe-logs").catch(() => {});
    };
  }, [enabled, bridge, appendLog, setLogs]);

  return { available };
};

/** Imperative helpers for action buttons. */
export const useSystemActions = () => {
  const bridge = getDesktopBridge();
  return {
    available: bridge !== null,
    openLogDir: () =>
      bridge?.invoke("system:open-log-dir") ?? Promise.resolve(),
    openLogFile: () =>
      bridge?.invoke("system:open-log-file") ?? Promise.resolve(),
  };
};
