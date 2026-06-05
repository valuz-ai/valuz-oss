/**
 * Zustand store for the desktop ``服务`` panel.
 *
 * Splits state into three concerns:
 *
 * 1. ``status``: latest snapshot from ``GET /v1/system/status``,
 *    refreshed every 5s + on demand.
 * 2. ``logs``: rolling buffer of parsed ``LogLine`` objects pulled from
 *    Electron main via IPC. Capped at ``LOG_BUFFER_SIZE`` so a chatty
 *    backend can't bloat memory.
 * 3. ``view``: per-user filter state (search query, level toggles,
 *    follow-tail flag, repeat-collapse toggle). Lives in the same
 *    store so the toolbar + viewport components both observe it.
 *
 * Subscribed by ``DesktopSystemPage`` and the components it renders.
 */

import { create } from "zustand";
import type { LogLine, LogLevel } from "@valuz/shared";
import { systemApi, type SystemStatusResponse } from "../api/system-api";

/** Max in-renderer log buffer. New lines push out the oldest. */
export const LOG_BUFFER_SIZE = 2000;

const DEFAULT_LEVELS: LogLevel[] = ["INFO", "WARNING", "ERROR", "CRITICAL"];

export interface SystemViewState {
  searchQuery: string;
  enabledLevels: Set<LogLevel>;
  followTail: boolean;
  collapseRepeats: boolean;
}

export interface SystemStoreState {
  // Status
  status: SystemStatusResponse | null;
  statusError: string | null;
  statusLoading: boolean;

  // Logs
  logs: LogLine[];

  // View / filter
  view: SystemViewState;

  // Actions
  refreshStatus: () => Promise<void>;
  setLogs: (lines: LogLine[]) => void;
  appendLog: (line: LogLine) => void;
  clearLogs: () => void;
  setSearchQuery: (q: string) => void;
  toggleLevel: (level: LogLevel) => void;
  setFollowTail: (v: boolean) => void;
  setCollapseRepeats: (v: boolean) => void;
}

export const useSystemStore = create<SystemStoreState>((set) => ({
  status: null,
  statusError: null,
  statusLoading: false,
  logs: [],
  view: {
    searchQuery: "",
    enabledLevels: new Set<LogLevel>(DEFAULT_LEVELS),
    followTail: true,
    collapseRepeats: true,
  },

  async refreshStatus() {
    set({ statusLoading: true });
    try {
      const data = await systemApi.status();
      set({ status: data, statusError: null, statusLoading: false });
    } catch (err) {
      set({
        statusError: err instanceof Error ? err.message : String(err),
        statusLoading: false,
      });
    }
  },

  setLogs(lines) {
    // Snapshot replace — used on initial mount when backfilling from
    // Electron main's ring buffer. Cap defensively.
    const clipped =
      lines.length > LOG_BUFFER_SIZE
        ? lines.slice(lines.length - LOG_BUFFER_SIZE)
        : lines;
    set({ logs: clipped });
  },

  appendLog(line) {
    set((state) => {
      const next = state.logs.concat(line);
      if (next.length > LOG_BUFFER_SIZE) {
        next.splice(0, next.length - LOG_BUFFER_SIZE);
      }
      return { logs: next };
    });
  },

  clearLogs() {
    set({ logs: [] });
  },

  setSearchQuery(q) {
    set((state) => ({ view: { ...state.view, searchQuery: q } }));
  },

  toggleLevel(level) {
    set((state) => {
      const next = new Set(state.view.enabledLevels);
      if (next.has(level)) {
        next.delete(level);
      } else {
        next.add(level);
      }
      return { view: { ...state.view, enabledLevels: next } };
    });
  },

  setFollowTail(v) {
    set((state) => ({ view: { ...state.view, followTail: v } }));
  },

  setCollapseRepeats(v) {
    set((state) => ({ view: { ...state.view, collapseRepeats: v } }));
  },
}));
