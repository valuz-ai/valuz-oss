import type { HostRequest } from "./services";
import type { LogLevel, PluginLog } from "./types";

const MAX_PENDING = 100;
const FLUSH_DELAY_MS = 400;
const MAX_MESSAGE_CHARS = 4000;

type Sender = (request: HostRequest) => Promise<unknown>;

function format(message: string, details: unknown[]): string {
  const parts = [message];
  for (const detail of details) {
    if (detail instanceof Error) parts.push(detail.stack ?? detail.message);
    else if (typeof detail === "string") parts.push(detail);
    else {
      try {
        parts.push(JSON.stringify(detail));
      } catch {
        parts.push(String(detail));
      }
    }
  }
  const text = parts.join(" ");
  return text.length > MAX_MESSAGE_CHARS
    ? `${text.slice(0, MAX_MESSAGE_CHARS)}…`
    : text;
}

/**
 * ``ctx.log``: to the console, and — best effort, batched — to the plugin's
 * log in Settings → Plugins → App Plugins (``POST /v1/extensions/third-party/{id}/logs``).
 * A failed write is dropped: logging must never break a plugin.
 */
export function createPluginLog(pluginId: string, send: Sender): PluginLog & {
  /** Resolves when everything queued so far has been written (or dropped). */
  flush(): Promise<void>;
} {
  const pending: Array<{ level: LogLevel; message: string }> = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running: Promise<void> | null = null;

  const drain = async (): Promise<void> => {
    while (pending.length > 0) {
      const entry = pending.shift()!;
      try {
        await send({
          method: "POST",
          path: `/v1/extensions/third-party/${encodeURIComponent(pluginId)}/logs`,
          json: entry,
          headers: { "X-Valuz-Plugin-Id": pluginId },
        });
      } catch {
        // best effort
      }
    }
  };

  const flush = (): Promise<void> => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    running ??= drain().finally(() => {
      running = null;
    });
    return running;
  };

  const write = (level: LogLevel, message: string, details: unknown[]) => {
    const text = format(message, details);
    const sink = level === "debug" ? "debug" : level;
    console[sink](`[plugin:${pluginId}] ${text}`);
    if (pending.length >= MAX_PENDING) pending.shift();
    pending.push({ level, message: text });
    timer ??= setTimeout(() => void flush(), FLUSH_DELAY_MS);
  };

  return {
    debug: (message, ...details) => write("debug", message, details),
    info: (message, ...details) => write("info", message, details),
    warn: (message, ...details) => write("warn", message, details),
    error: (message, ...details) => write("error", message, details),
    flush,
  };
}
