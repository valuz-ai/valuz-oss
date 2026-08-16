import { isIP } from "node:net";
import { parsePacResult } from "./pac-result";
import type { EgressMode, EgressResolution, EgressRoute } from "./types";

export const DEFAULT_RESOLUTION_TTL_MS = 30_000;

type EnvironmentSnapshot = Readonly<Record<string, string | undefined>>;

export interface OutboundResolverOptions {
  env: EnvironmentSnapshot;
  resolveSystemProxy: (targetUrl: string) => Promise<string>;
  ttlMs?: number;
  now?: () => number;
}

const normalizedHostname = (url: URL): string =>
  url.hostname.replace(/^\[|\]$/g, "").toLowerCase();

const effectivePort = (url: URL): string => {
  if (url.port) return url.port;
  if (url.protocol === "https:" || url.protocol === "wss:") return "443";
  if (url.protocol === "http:" || url.protocol === "ws:") return "80";
  return "";
};

const isLoopback = (url: URL): boolean => {
  const host = normalizedHostname(url);
  if (host === "localhost" || host.endsWith(".localhost") || host === "::1") {
    return true;
  }
  return isIP(host) === 4 && host.startsWith("127.");
};

const envValue = (env: EnvironmentSnapshot, name: string): string | null => {
  // Match Python requests/curl convention: a lowercase spelling, when set,
  // wins over its uppercase twin. GUI launches usually only carry uppercase.
  for (const key of [name.toLowerCase(), name.toUpperCase()]) {
    const value = env[key]?.trim();
    if (value) return value;
  }
  return null;
};

const noProxyValues = (env: EnvironmentSnapshot): string[] => {
  const values = [env.no_proxy, env.NO_PROXY]
    .filter((value): value is string => typeof value === "string")
    .flatMap((value) => value.split(","))
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  return [...new Set(values)];
};

const ipv4Number = (host: string): number | null => {
  if (isIP(host) !== 4) return null;
  return host
    .split(".")
    .map(Number)
    .reduce((value, octet) => (value << 8) | octet, 0) >>> 0;
};

const matchesIpv4Cidr = (host: string, token: string): boolean => {
  const match = /^([^/]+)\/(\d{1,2})$/.exec(token);
  if (!match) return false;
  const target = ipv4Number(host);
  const network = ipv4Number(match[1]);
  const prefix = Number(match[2]);
  if (target === null || network === null || prefix < 0 || prefix > 32) {
    return false;
  }
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (target & mask) === (network & mask);
};

const splitNoProxyToken = (
  token: string,
): { host: string; port: string | null } => {
  const bracketed = /^\[([^\]]+)](?::(\d+))?$/.exec(token);
  if (bracketed) {
    return { host: bracketed[1], port: bracketed[2] ?? null };
  }

  const lastColon = token.lastIndexOf(":");
  if (lastColon > 0 && token.indexOf(":") === lastColon) {
    const possiblePort = token.slice(lastColon + 1);
    if (/^\d+$/.test(possiblePort)) {
      return { host: token.slice(0, lastColon), port: possiblePort };
    }
  }
  return { host: token, port: null };
};

export const matchesNoProxy = (
  target: URL,
  env: EnvironmentSnapshot,
): boolean => {
  const hostname = normalizedHostname(target);
  const port = effectivePort(target);

  return noProxyValues(env).some((rawToken) => {
    if (rawToken === "*") return true;
    if (matchesIpv4Cidr(hostname, rawToken)) return true;

    const { host: rawHost, port: tokenPort } = splitNoProxyToken(rawToken);
    if (tokenPort !== null && tokenPort !== port) return false;

    const host = rawHost.replace(/^\*?\./, "").replace(/\.$/, "");
    if (!host) return false;
    return hostname === host || hostname.endsWith(`.${host}`);
  });
};

type EnvProxyResult =
  | { status: "absent" }
  | { status: "resolved"; route: EgressRoute }
  | { status: "unknown"; reason: string };

const parseEnvProxy = (raw: string): EnvProxyResult => {
  const candidate = raw.includes("://") ? raw : `http://${raw}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return { status: "unknown", reason: "invalid_env_proxy_url" };
  }

  if (parsed.protocol === "http:") {
    if (!parsed.hostname) {
      return { status: "unknown", reason: "invalid_env_proxy_url" };
    }
    parsed.pathname = "";
    parsed.search = "";
    parsed.hash = "";
    return {
      status: "resolved",
      route: {
        kind: "http_proxy",
        url: parsed.toString().replace(/\/$/, ""),
        source: "env",
      },
    };
  }
  if (parsed.protocol === "socks5:" || parsed.protocol === "socks5h:") {
    if (!parsed.hostname) {
      return { status: "unknown", reason: "invalid_env_proxy_url" };
    }
    parsed.protocol = "socks5:";
    parsed.pathname = "";
    parsed.search = "";
    parsed.hash = "";
    return {
      status: "resolved",
      route: {
        kind: "socks5_proxy",
        url: parsed.toString().replace(/\/$/, ""),
        source: "env",
      },
    };
  }
  return {
    status: "unknown",
    reason: "unsupported_env_proxy_scheme",
  };
};

const proxyForTarget = (target: URL, env: EnvironmentSnapshot): EnvProxyResult => {
  const secure = target.protocol === "https:" || target.protocol === "wss:";
  const primary = envValue(env, secure ? "HTTPS_PROXY" : "HTTP_PROXY");
  const fallback = envValue(env, "ALL_PROXY");
  const raw = primary ?? fallback;
  return raw ? parseEnvProxy(raw) : { status: "absent" };
};

const unknownResolution = (
  targetOrigin: string,
  now: number,
  ttlMs: number,
  reason: string,
): EgressResolution => ({
  targetOrigin,
  candidates: [],
  resolvedAt: now,
  ttlMs,
  status: "unknown",
  reason,
});

/**
 * Runtime-neutral route resolver. This module is deliberately side-effect
 * free: it does not open sockets or mutate process.env, so it can run in
 * Phase 1 shadow mode before any model request path changes.
 */
export class OutboundResolver {
  private readonly env: EnvironmentSnapshot;
  private readonly resolveSystemProxy: (targetUrl: string) => Promise<string>;
  private readonly ttlMs: number;
  private readonly now: () => number;
  private readonly cache = new Map<string, EgressResolution>();
  private readonly inFlight = new Map<string, Promise<EgressResolution>>();

  constructor(options: OutboundResolverOptions) {
    this.env = Object.freeze({ ...options.env });
    this.resolveSystemProxy = options.resolveSystemProxy;
    this.ttlMs = options.ttlMs ?? DEFAULT_RESOLUTION_TTL_MS;
    this.now = options.now ?? Date.now;
  }

  async resolve(
    targetUrl: string,
    mode: Exclude<EgressMode, "off"> = "auto",
  ): Promise<EgressResolution> {
    let target: URL;
    try {
      target = new URL(targetUrl);
    } catch {
      return unknownResolution("invalid", this.now(), this.ttlMs, "invalid_target_url");
    }
    if (!["http:", "https:", "ws:", "wss:"].includes(target.protocol)) {
      return unknownResolution(
        target.origin,
        this.now(),
        this.ttlMs,
        "unsupported_target_scheme",
      );
    }

    const key = `${mode}:${target.href}`;
    const cached = this.cache.get(key);
    const now = this.now();
    if (cached && now - cached.resolvedAt < cached.ttlMs) return cached;

    const pending = this.inFlight.get(key);
    if (pending) return pending;

    const resolution = this.resolveUncached(target, mode, now).finally(() => {
      this.inFlight.delete(key);
    });
    this.inFlight.set(key, resolution);
    const result = await resolution;
    this.cache.set(key, result);
    return result;
  }

  invalidate(targetOrigin?: string) {
    if (!targetOrigin) {
      this.cache.clear();
      return;
    }
    for (const [key, value] of this.cache) {
      if (value.targetOrigin === targetOrigin) this.cache.delete(key);
    }
  }

  private async resolveUncached(
    target: URL,
    mode: Exclude<EgressMode, "off">,
    now: number,
  ): Promise<EgressResolution> {
    if (isLoopback(target)) {
      return {
        targetOrigin: target.origin,
        candidates: [{ kind: "direct", source: "local" }],
        resolvedAt: now,
        ttlMs: this.ttlMs,
        status: "resolved",
      };
    }
    if (mode === "direct") {
      return {
        targetOrigin: target.origin,
        candidates: [{ kind: "direct", source: "policy" }],
        resolvedAt: now,
        ttlMs: this.ttlMs,
        status: "resolved",
      };
    }
    if (matchesNoProxy(target, this.env)) {
      return {
        targetOrigin: target.origin,
        candidates: [{ kind: "direct", source: "no_proxy" }],
        resolvedAt: now,
        ttlMs: this.ttlMs,
        status: "resolved",
      };
    }

    const envProxy = proxyForTarget(target, this.env);
    if (envProxy.status === "resolved") {
      return {
        targetOrigin: target.origin,
        candidates: [envProxy.route],
        resolvedAt: now,
        ttlMs: this.ttlMs,
        status: "resolved",
      };
    }
    if (envProxy.status === "unknown") {
      return unknownResolution(target.origin, now, this.ttlMs, envProxy.reason);
    }

    let rawPac: string | undefined;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        rawPac = await this.resolveSystemProxy(target.href);
        break;
      } catch {
        // Chromium/PAC state can briefly lag a system network transition.
        // Retry resolution once before caching a fail-loud unknown result.
      }
    }
    if (rawPac === undefined) {
      return unknownResolution(
        target.origin,
        now,
        this.ttlMs,
        "system_proxy_resolution_failed",
      );
    }
    const parsed = parsePacResult(rawPac);
    if (parsed.status === "unknown") {
      return unknownResolution(target.origin, now, this.ttlMs, parsed.reason);
    }
    return {
      targetOrigin: target.origin,
      candidates: parsed.candidates,
      resolvedAt: now,
      ttlMs: this.ttlMs,
      status: "resolved",
    };
  }
}
