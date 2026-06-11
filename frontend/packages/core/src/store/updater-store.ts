import { create } from "zustand";

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "error";

export interface UpdaterState {
  status: UpdaterStatus;
  version: string | null;
  progress: number;
  bytesPerSecond: number;
  errorMessage: string | null;
  /** User hid the in-app update toast. Re-shown when a new lifecycle event
   *  arrives (available / downloaded) or via show(). */
  dismissed: boolean;

  setChecking: () => void;
  setAvailable: (version: string) => void;
  setNotAvailable: () => void;
  setProgress: (progress: number, bytesPerSecond: number) => void;
  setDownloaded: () => void;
  setError: (message: string) => void;
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
  dismissed: false,
};

export const useUpdaterStore = create<UpdaterState>((set) => ({
  ...initial,

  setChecking: () => set({ status: "checking", errorMessage: null }),
  setAvailable: (version: string) =>
    set({ status: "available", version, errorMessage: null, dismissed: false }),
  setNotAvailable: () => set({ status: "idle" }),
  setProgress: (progress: number, bytesPerSecond: number) =>
    set({ status: "downloading", progress, bytesPerSecond }),
  setDownloaded: () =>
    set({ status: "downloaded", progress: 100, dismissed: false }),
  setError: (message: string) =>
    set({ status: "error", errorMessage: message }),
  dismiss: () => set({ dismissed: true }),
  show: () => set({ dismissed: false }),
  reset: () => set(initial),
}));
