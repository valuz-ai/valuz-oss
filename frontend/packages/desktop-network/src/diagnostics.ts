import type { EgressDiagnosticEvent } from "./types";

export const DEFAULT_DIAGNOSTIC_MAX_ENTRIES = 500;
export const DEFAULT_DIAGNOSTIC_MAX_AGE_MS = 30 * 60 * 1000;

export interface EgressDiagnosticsOptions {
  maxEntries?: number;
  maxAgeMs?: number;
  now?: () => number;
}

/** Return only scheme/host/port; credentials, path, query and hash are dropped. */
export const redactProxyUrl = (raw: string): string | undefined => {
  try {
    const parsed = new URL(raw);
    if (!parsed.hostname) return undefined;
    const port = parsed.port ? `:${parsed.port}` : "";
    return `${parsed.protocol}//${parsed.hostname}${port}`;
  } catch {
    return undefined;
  }
};

/**
 * Bounded, in-memory-only diagnostics. The event type is an allowlist schema;
 * callers cannot attach request headers, body, prompt or complete target URL.
 */
export class EgressDiagnostics {
  private readonly maxEntries: number;
  private readonly maxAgeMs: number;
  private readonly now: () => number;
  private events: EgressDiagnosticEvent[] = [];

  constructor(options: EgressDiagnosticsOptions = {}) {
    this.maxEntries = Math.max(1, options.maxEntries ?? DEFAULT_DIAGNOSTIC_MAX_ENTRIES);
    this.maxAgeMs = Math.max(1, options.maxAgeMs ?? DEFAULT_DIAGNOSTIC_MAX_AGE_MS);
    this.now = options.now ?? Date.now;
  }

  record(event: EgressDiagnosticEvent): void {
    this.events.push({ ...event });
    this.prune();
  }

  snapshot(): EgressDiagnosticEvent[] {
    this.prune();
    return this.events.map((event) => ({ ...event }));
  }

  clear(): void {
    this.events = [];
  }

  private prune(): void {
    const cutoff = this.now() - this.maxAgeMs;
    this.events = this.events
      .filter((event) => event.timestamp >= cutoff)
      .slice(-this.maxEntries);
  }
}
