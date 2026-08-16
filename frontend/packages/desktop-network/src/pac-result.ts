import type { EgressRoute, PacParseResult } from "./types";

const proxyEndpointUrl = (
  scheme: "http" | "socks5",
  endpoint: string,
): string | null => {
  const hasExplicitPort =
    /^\[[0-9a-f:.]+\]:\d+$/i.test(endpoint) ||
    /^[^\s/:]+:\d+$/.test(endpoint);
  if (!hasExplicitPort) return null;
  try {
    const parsed = new URL(`${scheme}://${endpoint}`);
    if (
      !parsed.hostname ||
      parsed.username ||
      parsed.password ||
      (parsed.pathname !== "" && parsed.pathname !== "/") ||
      parsed.search ||
      parsed.hash
    ) {
      return null;
    }
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
};

/**
 * Parse Chromium's ``session.resolveProxy()`` result without silently
 * weakening it. Candidate order is preserved exactly. Unsupported PAC types
 * fail the whole resolution so the caller never invents a DIRECT fallback.
 */
export const parsePacResult = (raw: string): PacParseResult => {
  const entries = raw
    .split(";")
    .map((entry) => entry.trim())
    .filter(Boolean);

  if (entries.length === 0) {
    return { status: "unknown", reason: "empty_pac_result" };
  }

  const candidates: EgressRoute[] = [];
  for (const entry of entries) {
    if (entry.toUpperCase() === "DIRECT") {
      candidates.push({ kind: "direct", source: "system" });
      continue;
    }

    const match = /^(\S+)\s+(.+)$/.exec(entry);
    if (!match) {
      return { status: "unknown", reason: "invalid_pac_entry" };
    }

    const kind = match[1].toUpperCase();
    const endpoint = match[2].trim();
    if (kind !== "PROXY" && kind !== "SOCKS5") {
      return {
        status: "unknown",
        reason: "unsupported_pac_type",
      };
    }

    const url = proxyEndpointUrl(kind === "PROXY" ? "http" : "socks5", endpoint);
    if (!url) {
      return { status: "unknown", reason: "invalid_pac_proxy_endpoint" };
    }
    candidates.push({
      kind: kind === "PROXY" ? "http_proxy" : "socks5_proxy",
      url,
      source: "system",
    });
  }

  return candidates.length > 0
    ? { status: "resolved", candidates }
    : { status: "unknown", reason: "empty_pac_candidates" };
};
