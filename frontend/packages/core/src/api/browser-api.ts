/**
 * Client for /v1/browser/* — the Settings "Browser" panel.
 *
 * Status / login-helper (open) / stop for the host-managed chrome-devtools
 * daemon. Mirrors ``components.schemas.Browser*`` in ``api/openapi.yaml``
 * (no OpenAPI codegen is wired today — keep both in lock-step).
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setBrowserApiBase = (url: string): void => {
  _apiBase = url;
};

/**
 * `managed` / `attach` — the local engine (a visible Chrome on this machine).
 * `sandbox` — a remote engine: the daemon runs headless where the agent's
 * shell runs; there is no window for the user to open.
 */
export type BrowserMode = "managed" | "attach" | "sandbox";

export interface BrowserStatus {
  daemon_running: boolean;
  mode: BrowserMode;
  node_ok: boolean;
  cli_prefix: string;
  pid: number | null;
  /** User-facing guidance for the panel (e.g. "Node not found …"). */
  hints: string[];
}

export interface BrowserStartResult {
  status: string;
  mode: string;
  cli_prefix: string;
}

export interface BrowserStopResult {
  status: string;
}

const fetchJson = createFetchJson(() => _apiBase);

export const browserApi = {
  status(): Promise<BrowserStatus> {
    return fetchJson<BrowserStatus>("/v1/browser/status");
  },
  open(): Promise<BrowserStartResult> {
    return fetchJson<BrowserStartResult>("/v1/browser/open", { method: "POST" });
  },
  stop(): Promise<BrowserStopResult> {
    return fetchJson<BrowserStopResult>("/v1/browser/stop", { method: "POST" });
  },
};
