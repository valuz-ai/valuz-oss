/**
 * Backend log ring + IPC bridge for the desktop ``服务`` panel.
 *
 * Why we tail a file (rather than capture sidecar stdout):
 *
 * Both dev and prod modes need to work. In **dev** the user runs the
 * backend externally via ``valuz start``; Electron's
 * ``services/mod.ts`` skips spawning a sidecar so ``onLog`` never fires.
 * In **prod** the bundled binary is spawned by Electron and we *do*
 * capture stdout/stderr — but we still want the structured JSON lines
 * the backend writes to its rolling file (those have KV fields the
 * console stream doesn't).
 *
 * Solution: tail the file at the canonical path
 * (``~/.valuz/app/logs/backend.log``) using ``fs.watchFile``. Same code
 * path for both modes. The tail handles file rotation (size shrink) by
 * resetting the read offset to 0.
 *
 * IPC channels:
 *
 *   - ``system:get-log-snapshot``  → returns the current ring as
 *     ``LogLine[]``. Renderer calls on mount to backfill.
 *   - ``system:subscribe-logs``    → no-op handler; renderer listens to
 *     ``system:log-line`` events that we ``webContents.send`` for each
 *     new line.
 *   - ``system:unsubscribe-logs``  → drops the renderer from the
 *     subscriber set so we stop pushing to a closed window.
 *   - ``system:get-status``        → proxies to
 *     ``GET /v1/system/status`` (renderer could fetch directly, but
 *     going through main lets us cache + react to bg state changes).
 *   - ``system:open-log-dir``      → ``shell.openPath(log_dir)``.
 *   - ``system:open-log-file``     → ``shell.openPath(log_file)``.
 */

import { ipcMain, shell, type WebContents } from "electron";
import {
  watch as watchFile,
  FSWatcher,
  statSync,
  openSync,
  readSync,
  closeSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, dirname } from "node:path";

const RING_SIZE = 5000;

/** One parsed log entry. */
export interface LogLine {
  /** Monotonic id assigned at receive time — stable React key. */
  id: number;
  /** ISO-8601 timestamp from the JSON payload, or ``receivedAt`` for raw. */
  ts: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL" | "RAW";
  logger: string;
  msg: string;
  /** Any structured KV fields beyond the standard ones. */
  fields: Record<string, unknown>;
  /** Original line (truncated). Lets the UI fall back to raw rendering. */
  raw: string;
}

const ring: LogLine[] = [];
const subscribers = new Set<WebContents>();
let nextId = 1;

const LOG_FILE_PATH = join(homedir(), ".valuz", "app", "logs", "backend.log");
const LOG_DIR_PATH = dirname(LOG_FILE_PATH);

/** Append a parsed entry to the ring + broadcast to subscribers. */
const record = (line: LogLine) => {
  ring.push(line);
  if (ring.length > RING_SIZE) {
    ring.splice(0, ring.length - RING_SIZE);
  }
  for (const w of subscribers) {
    if (!w.isDestroyed()) {
      try {
        w.send("system:log-line", line);
      } catch {
        // Renderer may be tearing down — fall through, removal happens
        // in the ``destroyed`` listener registered on subscribe.
      }
    }
  }
};

/** Best-effort parse: JSON line first, raw fallback. */
const parse = (raw: string): LogLine => {
  const id = nextId++;
  const trimmed = raw.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const obj = JSON.parse(trimmed) as Record<string, unknown>;
      const ts = typeof obj.ts === "string" ? obj.ts : new Date().toISOString();
      const level = String(obj.level ?? "INFO").toUpperCase();
      const logger = String(obj.logger ?? "");
      const msg = String(obj.msg ?? "");
      const fields: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj)) {
        if (k === "ts" || k === "level" || k === "logger" || k === "msg")
          continue;
        fields[k] = v;
      }
      const allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
      const safeLevel = (
        allowed.includes(level) ? level : "INFO"
      ) as LogLine["level"];
      return { id, ts, level: safeLevel, logger, msg, fields, raw };
    } catch {
      // Fall through to raw.
    }
  }
  return {
    id,
    ts: new Date().toISOString(),
    level: "RAW",
    logger: "",
    msg: trimmed,
    fields: {},
    raw,
  };
};

/** Push a stdout/stderr line captured from a sidecar child process. */
export const recordSidecarLine = (line: string): void => {
  if (!line.trim()) return;
  record(parse(line));
};

// ── File tailer ───────────────────────────────────────────────────────

let tailHandle: { offset: number; watcher: FSWatcher | null } | null = null;
let pollTimer: NodeJS.Timeout | null = null;

const readNew = (): void => {
  if (!tailHandle) return;
  let stat: ReturnType<typeof statSync>;
  try {
    stat = statSync(LOG_FILE_PATH);
  } catch {
    return; // File doesn't exist yet — wait for the next poll tick.
  }
  if (stat.size < tailHandle.offset) {
    // Rotated / truncated — restart from 0.
    tailHandle.offset = 0;
  }
  if (stat.size === tailHandle.offset) return;

  const length = stat.size - tailHandle.offset;
  const buffer = Buffer.alloc(length);
  let fd: number;
  try {
    fd = openSync(LOG_FILE_PATH, "r");
  } catch {
    return;
  }
  try {
    readSync(fd, buffer, 0, length, tailHandle.offset);
  } finally {
    closeSync(fd);
  }
  tailHandle.offset = stat.size;

  const text = buffer.toString("utf-8");
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    record(parse(line));
  }
};

/** Start tailing the backend log file. Idempotent. */
export const startLogTail = (): void => {
  if (tailHandle) return;
  tailHandle = { offset: 0, watcher: null };

  // If the file already exists, seed the offset to a tail window so
  // we don't replay the entire file (the snapshot-on-mount path will
  // cover history). Read the last 64 KB or 200 lines max.
  try {
    const stat = statSync(LOG_FILE_PATH);
    const seedOffset = Math.max(0, stat.size - 64 * 1024);
    tailHandle.offset = seedOffset;
    readNew();
  } catch {
    // Doesn't exist yet — fine, the poll loop will pick it up.
  }

  // ``fs.watch`` is event-driven on macOS but flaky on rotated files;
  // pair it with a 500ms poll loop for reliability across platforms.
  // Same defensive pattern multica's daemon-manager uses.
  try {
    tailHandle.watcher = watchFile(LOG_FILE_PATH, { persistent: false }, () => {
      readNew();
    });
  } catch {
    // Directory might not exist yet — poll-only is fine.
  }

  pollTimer = setInterval(readNew, 500);
};

/** Stop tailing (and clear subscribers). For app shutdown. */
export const stopLogTail = (): void => {
  if (tailHandle?.watcher) {
    tailHandle.watcher.close();
  }
  tailHandle = null;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  subscribers.clear();
};

// ── IPC registration ─────────────────────────────────────────────────

/** Register all ``system:*`` IPC handlers. Call once after app ready. */
export const registerSystemLogIpc = (): void => {
  ipcMain.handle("system:get-log-snapshot", async () => {
    return ring.slice();
  });

  ipcMain.handle("system:subscribe-logs", async (event) => {
    const wc = event.sender;
    if (subscribers.has(wc)) return { ok: true };
    subscribers.add(wc);
    // Auto-cleanup on renderer teardown.
    wc.once("destroyed", () => {
      subscribers.delete(wc);
    });
    return { ok: true };
  });

  ipcMain.handle("system:unsubscribe-logs", async (event) => {
    subscribers.delete(event.sender);
    return { ok: true };
  });

  ipcMain.handle(
    "system:get-status",
    async (_event, args?: { backendBaseUrl?: string }) => {
      const base = args?.backendBaseUrl ?? "http://127.0.0.1:8000";
      try {
        const res = await fetch(`${base}/v1/system/status`);
        if (!res.ok) {
          return { ok: false, error: `HTTP ${res.status}` };
        }
        return { ok: true, data: await res.json() };
      } catch (err) {
        return {
          ok: false,
          error: err instanceof Error ? err.message : String(err),
        };
      }
    },
  );

  ipcMain.handle("system:open-log-dir", async () => {
    await shell.openPath(LOG_DIR_PATH);
    return { ok: true };
  });

  ipcMain.handle("system:open-log-file", async () => {
    await shell.openPath(LOG_FILE_PATH);
    return { ok: true };
  });
};

export const SYSTEM_LOG_PATHS = {
  file: LOG_FILE_PATH,
  dir: LOG_DIR_PATH,
};
