import { describe, expect, it, vi } from "vitest";
import { OutboundResolver, matchesNoProxy } from "./outbound-resolver";
import { parsePacResult } from "./pac-result";

describe("parsePacResult", () => {
  it("preserves ordered PROXY, SOCKS5 and DIRECT candidates", () => {
    expect(
      parsePacResult(
        "PROXY 127.0.0.1:7890; SOCKS5 proxy.example:7891; DIRECT",
      ),
    ).toEqual({
      status: "resolved",
      candidates: [
        {
          kind: "http_proxy",
          url: "http://127.0.0.1:7890",
          source: "system",
        },
        {
          kind: "socks5_proxy",
          url: "socks5://proxy.example:7891",
          source: "system",
        },
        { kind: "direct", source: "system" },
      ],
    });
  });

  it("fails loud for unsupported PAC types", () => {
    expect(parsePacResult("HTTPS proxy.example:443; DIRECT")).toEqual({
      status: "unknown",
      reason: "unsupported_pac_type",
    });
  });

  it("rejects malformed proxy endpoints", () => {
    expect(parsePacResult("PROXY missing-port")).toEqual({
      status: "unknown",
      reason: "invalid_pac_proxy_endpoint",
    });
  });
});

describe("matchesNoProxy", () => {
  it("merges upper/lower values and matches suffix, port and IPv4 CIDR", () => {
    const env = {
      no_proxy: ".example.com,api.internal:8443",
      NO_PROXY: "10.0.0.0/8",
    };
    expect(matchesNoProxy(new URL("https://sub.example.com/v1"), env)).toBe(
      true,
    );
    expect(matchesNoProxy(new URL("https://api.internal:8443/v1"), env)).toBe(
      true,
    );
    expect(matchesNoProxy(new URL("https://api.internal/v1"), env)).toBe(false);
    expect(matchesNoProxy(new URL("http://10.2.3.4/v1"), env)).toBe(true);
  });
});

describe("OutboundResolver", () => {
  it("hard-routes loopback direct without consulting env or PAC", async () => {
    const system = vi.fn(async () => "PROXY 127.0.0.1:7890");
    const resolver = new OutboundResolver({
      env: { HTTPS_PROXY: "http://proxy.example:8080" },
      resolveSystemProxy: system,
    });

    await expect(resolver.resolve("http://127.0.0.2:9000/v1")).resolves.toMatchObject(
      {
        status: "resolved",
        candidates: [{ kind: "direct", source: "local" }],
      },
    );
    expect(system).not.toHaveBeenCalled();
  });

  it("uses explicit direct policy before env/PAC", async () => {
    const system = vi.fn(async () => "PROXY system.example:8080");
    const resolver = new OutboundResolver({
      env: { HTTPS_PROXY: "http://env.example:8080" },
      resolveSystemProxy: system,
    });

    await expect(
      resolver.resolve("https://api.example/v1", "direct"),
    ).resolves.toMatchObject({
      candidates: [{ kind: "direct", source: "policy" }],
    });
    expect(system).not.toHaveBeenCalled();
  });

  it("honors NO_PROXY before explicit proxy env", async () => {
    const system = vi.fn(async () => "PROXY system.example:8080");
    const resolver = new OutboundResolver({
      env: {
        HTTPS_PROXY: "http://env.example:8080",
        NO_PROXY: "api.example",
      },
      resolveSystemProxy: system,
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject(
      {
        candidates: [{ kind: "direct", source: "no_proxy" }],
      },
    );
    expect(system).not.toHaveBeenCalled();
  });

  it("prefers lowercase proxy env and uses ALL_PROXY as fallback", async () => {
    const system = vi.fn(async () => "DIRECT");
    const resolver = new OutboundResolver({
      env: {
        HTTPS_PROXY: "http://upper.example:8080",
        https_proxy: "http://lower.example:8081",
        ALL_PROXY: "socks5://fallback.example:1080",
      },
      resolveSystemProxy: system,
    });
    const fallback = new OutboundResolver({
      env: { ALL_PROXY: "socks5h://fallback.example:1080" },
      resolveSystemProxy: system,
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject(
      {
        candidates: [
          {
            kind: "http_proxy",
            url: "http://lower.example:8081",
            source: "env",
          },
        ],
      },
    );
    await expect(fallback.resolve("http://api.example/v1")).resolves.toMatchObject(
      {
        candidates: [
          {
            kind: "socks5_proxy",
            url: "socks5://fallback.example:1080",
            source: "env",
          },
        ],
      },
    );
  });

  it("fails loud for an unsupported env proxy scheme", async () => {
    const system = vi.fn(async () => "DIRECT");
    const resolver = new OutboundResolver({
      env: { HTTPS_PROXY: "https://secure-proxy.example:443" },
      resolveSystemProxy: system,
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject(
      {
        status: "unknown",
        candidates: [],
        reason: "unsupported_env_proxy_scheme",
      },
    );
    expect(system).not.toHaveBeenCalled();
  });

  it("falls through to Chromium PAC and preserves candidates", async () => {
    const resolver = new OutboundResolver({
      env: {},
      resolveSystemProxy: async () =>
        "PROXY system.example:8080; SOCKS5 system.example:1080; DIRECT",
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject(
      {
        status: "resolved",
        candidates: [
          { kind: "http_proxy", source: "system" },
          { kind: "socks5_proxy", source: "system" },
          { kind: "direct", source: "system" },
        ],
      },
    );
  });

  it("reports system resolution failures as unknown", async () => {
    const resolver = new OutboundResolver({
      env: {},
      resolveSystemProxy: async () => {
        throw new Error("PAC unavailable");
      },
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject(
      {
        status: "unknown",
        reason: "system_proxy_resolution_failed",
      },
    );
  });

  it("retries one transient system resolver failure before caching the route", async () => {
    let attempts = 0;
    const resolver = new OutboundResolver({
      env: {},
      resolveSystemProxy: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("transient");
        return "DIRECT";
      },
    });

    await expect(resolver.resolve("https://api.example/v1")).resolves.toMatchObject({
      status: "resolved",
      candidates: [{ kind: "direct", source: "system" }],
    });
    expect(attempts).toBe(2);
  });

  it("deduplicates concurrent resolutions, caches by TTL and invalidates by origin", async () => {
    let now = 1000;
    const system = vi.fn(async () => "DIRECT");
    const resolver = new OutboundResolver({
      env: {},
      resolveSystemProxy: system,
      ttlMs: 100,
      now: () => now,
    });

    await Promise.all([
      resolver.resolve("https://api.example/v1"),
      resolver.resolve("https://api.example/v1"),
    ]);
    expect(system).toHaveBeenCalledTimes(1);

    await resolver.resolve("https://api.example/v1");
    expect(system).toHaveBeenCalledTimes(1);

    resolver.invalidate("https://api.example");
    await resolver.resolve("https://api.example/v1");
    expect(system).toHaveBeenCalledTimes(2);

    now += 101;
    await resolver.resolve("https://api.example/v1");
    expect(system).toHaveBeenCalledTimes(3);
  });
});
