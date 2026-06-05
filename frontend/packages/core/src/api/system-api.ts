/**
 * Client for ``GET /v1/system/status``.
 *
 * Drives the desktop ``服务`` panel — backend pid, uptime, version,
 * kernel pin, db / log file paths, plus a non-fatal warnings buffer.
 * Cheap on every call; safe to poll at a few-second cadence.
 *
 * The response mirrors ``components.schemas.SystemStatusResponse``
 * in ``api/openapi.yaml``. Keep both in lock-step.
 */

export type {
  SystemStatusResponse,
  SystemHealthStatus,
  RuntimeId,
} from "@valuz/shared";

import { createFetchJson } from "./fetch-json";
import type { SystemStatusResponse } from "@valuz/shared";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setSystemApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

export const systemApi = {
  status(): Promise<SystemStatusResponse> {
    return fetchJson("/v1/system/status");
  },
};
