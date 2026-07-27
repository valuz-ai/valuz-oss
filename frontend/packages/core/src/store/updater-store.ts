import { create } from "zustand";

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  // macOS only: after the real network download, electron-updater hands the zip
  // to Squirrel.Mac via a loopback server, which "re-downloads" it fast (0→100
  // again). We surface that second pass as "preparing" (install hand-off) so the
  // bar doesn't look like it downloads twice.
  | "preparing"
  | "downloaded"
  | "error";

/** Which operation an error interrupted. The wire payload carries no phase, so
 *  it is derived in ``setError`` from the status the error arrived in — the UI
 *  uses it to say "download failed" vs "check failed" instead of echoing raw
 *  ``net::ERR_*`` strings. */
export type UpdaterErrorPhase = "download" | "check";

export interface UpdaterState {
  status: UpdaterStatus;
  version: string | null;
  progress: number;
  bytesPerSecond: number;
  errorMessage: string | null;
  /** Only meaningful while ``status === "error"``. */
  errorPhase: UpdaterErrorPhase | null;
  /** Whether the current error should appear in the floating toast. False for
   *  the About-page check (it shows its own inline error); true for menu/tray
   *  checks and downloads, where the toast is the only feedback surface. Only
   *  meaningful while ``status === "error"``. */
  errorInToast: boolean;
  /** User hid the in-app update toast. Re-shown when a new lifecycle event
   *  arrives (available / downloaded) or via show(). */
  dismissed: boolean;

  setChecking: () => void;
  setAvailable: (version: string) => void;
  setNotAvailable: () => void;
  /** Optimistically flip to "downloading" at 0% the instant the user clicks
   *  download, so the progress bar appears immediately instead of waiting for
   *  the first ``download-progress`` event (which can lag a beat). */
  setDownloading: () => void;
  setProgress: (progress: number, bytesPerSecond: number) => void;
  setDownloaded: () => void;
  /** ``toast`` controls whether the error also pops the floating toast. */
  setError: (message: string, toast?: boolean) => void;
  dismiss: () => void;
  show: () => void;
  reset: () => void;
}

const initial = {
  status: "idle" as UpdaterStatus,
  version: null as string | null,
  progress: 0,
  bytesPerSecond: 0,
  errorMessage: null as string | null,
  errorPhase: null as UpdaterErrorPhase | null,
  errorInToast: false,
  dismissed: false,
};

export const useUpdaterStore = create<UpdaterState>((set) => ({
  ...initial,

  setChecking: () =>
    set({ status: "checking", errorMessage: null, errorInToast: false }),
  setAvailable: (version: string) =>
    set({ status: "available", version, errorMessage: null, dismissed: false }),
  setNotAvailable: () => set({ status: "idle" }),
  setDownloading: () =>
    set({ status: "downloading", progress: 0, errorMessage: null }),
  setProgress: (progress: number, bytesPerSecond: number) =>
    set((s) => {
      // Already in the local install hand-off → hold the bar full, ignore the
      // fast second 0→100.
      if (s.status === "preparing") {
        return { progress: 100, bytesPerSecond: 0 };
      }
      // A sharp drop after we'd nearly finished = the network download is done
      // and Squirrel.Mac is re-reading it from loopback. Show "preparing"
      // instead of a second download bar. The >=90 / -10 guards keep mid-
      // download jitter from tripping it; platforms without the hand-off never
      // reset, so they never enter "preparing".
      if (
        s.status === "downloading" &&
        s.progress >= 90 &&
        progress < s.progress - 10
      ) {
        return { status: "preparing", progress: 100, bytesPerSecond: 0 };
      }
      return { status: "downloading", progress, bytesPerSecond };
    }),
  setDownloaded: () =>
    set({ status: "downloaded", progress: 100, dismissed: false }),
  setError: (message: string, toast = false) =>
    set((s) => {
      const errorPhase: UpdaterErrorPhase =
        s.status === "downloading" || s.status === "preparing"
          ? "download"
          : "check";
      return toast
        ? {
            status: "error" as const,
            errorMessage: message,
            errorPhase,
            errorInToast: true,
            dismissed: false,
          }
        : {
            status: "error" as const,
            errorMessage: message,
            errorPhase,
            errorInToast: false,
          };
    }),
  dismiss: () => set({ dismissed: true }),
  show: () => set({ dismissed: false }),
  reset: () => set(initial),
}));
