/**
 * Shared types for the desktop ``服务`` panel — backend status & log
 * surface. Renderer (``@valuz/core`` store + UI) and Electron main
 * (``services/system-logs.ts``) agree on these shapes.
 */

export type LogLevel =
  | "DEBUG"
  | "INFO"
  | "WARNING"
  | "ERROR"
  | "CRITICAL"
  | "RAW";

/** One parsed log entry. Produced by Electron main's tail of the
 *  backend's structured JSON log file (with raw-line fallback). */
export interface LogLine {
  /** Monotonic id assigned at receive time — stable React key. */
  id: number;
  /** ISO-8601 timestamp from the JSON payload, or receive time for raw. */
  ts: string;
  level: LogLevel;
  /** Python logger name, e.g. ``valuz_agent.domains.execution.sessions``. */
  logger: string;
  /** Primary message text. */
  msg: string;
  /** Any structured KV fields beyond the standard ones (request_id,
   *  session_id, duration_ms, …). */
  fields: Record<string, unknown>;
  /** Original line (truncated for very long blobs). */
  raw: string;
}

export type SystemHealthStatus = "running" | "starting" | "degraded";

export type RuntimeId = "claude_agent" | "codex" | "deepagents";

export interface SystemStatusResponse {
  status: SystemHealthStatus;
  pid: number;
  started_at: number;
  uptime_seconds: number;
  version: string;
  kernel_pin: string;
  port: number;
  active_session_count: number;
  db_path: string;
  log_path: string;
  log_dir: string;
  data_dir: string;
  runtimes_available: RuntimeId[];
  warnings: string[];
}
