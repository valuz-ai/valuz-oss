/**
 * Local backup API client (docs/design/client-local-backup.md).
 *
 * Wraps ``/v1/backup/*``: config (frequency / destination / scope /
 * retention), run-now + progress, version listing/browsing, per-file export
 * and full-restore staging. Wire shapes mirror
 * ``valuz_agent.modules.backup.schemas`` (snake_case).
 */

import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setBackupApiBase = (url: string): void => {
  _apiBase = url;
};

export type BackupFrequency = "manual" | "every_6h" | "daily" | "weekly";

export interface BackupScope {
  managed_projects: boolean;
  external_projects: boolean;
  user_skills: boolean;
}

export interface BackupRetention {
  keep_recent: number;
  keep_daily_days: number;
  max_total_gb: number;
}

export interface BackupLastRun {
  at: number | null;
  status: "ok" | "failed" | "skipped_no_change" | null;
  error: string | null;
  version_id: string | null;
}

export interface BackupRunProgress {
  running: boolean;
  phase: "preflight" | "db" | "files" | "summary" | "finalize" | null;
  started_at: number | null;
  processed_bytes: number;
  trigger: "manual" | "scheduled" | null;
}

export interface BackupConfig {
  supported: boolean;
  unsupported_reason: string | null;
  enabled: boolean;
  frequency: BackupFrequency;
  destination: string;
  scope: BackupScope;
  retention: BackupRetention;
  last_run: BackupLastRun;
  next_run_at: number | null;
  run: BackupRunProgress;
  versions_count: number;
  total_bytes: number;
  restore_result: Record<string, unknown> | null;
}

export interface BackupConfigPatch {
  enabled?: boolean;
  frequency?: BackupFrequency;
  destination?: string;
  scope?: BackupScope;
  retention?: BackupRetention;
}

export interface BackupSummaryCounts {
  sessions: number;
  messages: number;
  projects: number;
  agents: number;
  skills: number;
  knowledge_bases: number;
  documents: number;
  automations: number;
}

export interface BackupVersionInfo {
  id: string;
  created_at: number;
  kind: "scheduled" | "manual" | "pre_restore";
  total_bytes: number;
  new_bytes: number;
  file_count: number;
  duration_ms: number;
  app_version: string | null;
  counts: BackupSummaryCounts;
}

export interface BackupVersionDetail extends BackupVersionInfo {
  host_alembic: string | null;
  kernel_alembic: string | null;
  scope: BackupScope;
  dedup: "hardlink" | "none";
  kb_source_count: number;
  kb_source_missing: number;
}

export interface BackupFileEntry {
  name: string;
  path: string;
  kind: "dir" | "file" | "link";
  size: number;
}

export interface BackupFileListResponse {
  path: string;
  entries: BackupFileEntry[];
}

export interface BackupRestoreResult {
  staged: boolean;
  requires_restart: boolean;
  plan: { target: string; action: "replace" | "create"; bytes: number }[];
}

const fetchJson = createFetchJson(() => _apiBase);

export const backupApi = {
  getConfig(): Promise<BackupConfig> {
    return fetchJson(`/v1/backup/config`);
  },

  patchConfig(patch: BackupConfigPatch): Promise<BackupConfig> {
    return fetchJson(`/v1/backup/config`, {
      method: "PUT",
      json: patch,
    });
  },

  runNow(): Promise<{ started: boolean; run: BackupRunProgress }> {
    return fetchJson(`/v1/backup/runs`, { method: "POST" });
  },

  currentRun(): Promise<BackupRunProgress> {
    return fetchJson(`/v1/backup/runs/current`);
  },

  listVersions(): Promise<{ versions: BackupVersionInfo[] }> {
    return fetchJson(`/v1/backup/versions`);
  },

  getVersion(versionId: string): Promise<BackupVersionDetail> {
    return fetchJson(`/v1/backup/versions/${encodeURIComponent(versionId)}`);
  },

  deleteVersion(versionId: string): Promise<void> {
    return fetchJson(`/v1/backup/versions/${encodeURIComponent(versionId)}`, {
      method: "DELETE",
    });
  },

  listFiles(versionId: string, path: string): Promise<BackupFileListResponse> {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return fetchJson(
      `/v1/backup/versions/${encodeURIComponent(versionId)}/files${query}`,
    );
  },

  /** Browser-download URL for one file inside a version. */
  fileDownloadUrl(versionId: string, path: string): string {
    return `${_apiBase}/v1/backup/versions/${encodeURIComponent(versionId)}/files/download?path=${encodeURIComponent(path)}`;
  },

  restore(versionId: string, dryRun = false): Promise<BackupRestoreResult> {
    return fetchJson(
      `/v1/backup/versions/${encodeURIComponent(versionId)}/restore`,
      { method: "POST", json: { dry_run: dryRun } },
    );
  },
};
