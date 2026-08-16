import { createServer, type ServerResponse } from "node:http";
import { describe, expect, it } from "vitest";
import { EgressDiagnostics, redactProxyUrl } from "./diagnostics";
import {
  captureProxyEnvironment,
  EgressManager,
  resolveEgressFrontendsEnabled,
  resolveInitialEgressMode,
} from "./egress-manager";

describe("egress diagnostics", () => {
  it("redacts credentials and every URL component after host/port", () => {
    expect(
      redactProxyUrl("http://user:secret@proxy.example:8080/private?q=token#x"),
    ).toBe("http://proxy.example:8080");
    expect(redactProxyUrl("socks5://proxy.example:1080")).toBe(
      "socks5://proxy.example:1080",
    );
  });

  it("bounds diagnostics by age and count and returns defensive copies", () => {
    let now = 100;
    const diagnostics = new EgressDiagnostics({
      maxEntries: 2,
      maxAgeMs: 10,
      now: () => now,
    });
    const event = (id: string, timestamp: number) => ({
      event: "egress.attempt.started" as const,
      connectionAttemptId: id,
      clientId: "client",
      runtime: "codex" as const,
      frontend: "shadow" as const,
      targetOrigin: "https://api.example",
      mode: "auto" as const,
      timestamp,
    });
    diagnostics.record(event("old", 89));
    diagnostics.record(event("one", 99));
    diagnostics.record(event("two", 100));
    diagnostics.record(event("three", 100));

    const snapshot = diagnostics.snapshot();
    expect(snapshot.map((item) => item.connectionAttemptId)).toEqual([
      "two",
      "three",
    ]);
    snapshot[0].clientId = "mutated";
    expect(diagnostics.snapshot()[0].clientId).toBe("client");

    now = 111;
    expect(diagnostics.snapshot()).toEqual([]);
  });
});

describe("EgressManager shadow mode", () => {
  it("captures only proxy-related environment keys", () => {
    expect(
      captureProxyEnvironment({
        HTTPS_PROXY: "http://proxy.example:8080",
        NO_PROXY: "localhost",
        OPENAI_API_KEY: "must-not-be-copied",
      }),
    ).toEqual({
      HTTPS_PROXY: "http://proxy.example:8080",
      NO_PROXY: "localhost",
    });
  });

  it("only accepts off as the emergency environment override", () => {
    expect(resolveInitialEgressMode({ VALUZ_EGRESS_MODE: " off " })).toBe("off");
    expect(
      resolveInitialEgressMode({ VALUZ_EGRESS_MODE: "direct" }, "auto"),
    ).toBe("auto");
  });

  it("makes desktop frontends available by default with an emergency disable", () => {
    expect(resolveEgressFrontendsEnabled({})).toBe(true);
    expect(resolveEgressFrontendsEnabled({ VALUZ_EGRESS_FRONTENDS: "1" })).toBe(
      true,
    );
    expect(resolveEgressFrontendsEnabled({ VALUZ_EGRESS_FRONTENDS: " 0 " })).toBe(
      false,
    );
    expect(resolveEgressFrontendsEnabled({}, true)).toBe(false);
  });

  it("starts new installations in model-client-managed mode", () => {
    expect(resolveInitialEgressMode({})).toBe("off");
    expect(resolveInitialEgressMode({}, "auto")).toBe("auto");
  });

  it("does not resolve or emit diagnostics while off", async () => {
    const manager = new EgressManager({
      mode: "off",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
    });
    manager.start();

    await expect(
      manager.resolveShadow({
        targetUrl: "https://api.example/v1",
        clientId: "client-1",
        runtime: "codex",
      }),
    ).resolves.toMatchObject({
      status: "unknown",
      reason: "egress_manager_off",
    });
    expect(manager.isStarted()).toBe(false);
    expect(manager.getDiagnostics()).toEqual([]);
  });

  it("records allowlisted shadow resolution events and a runtime snapshot", async () => {
    let now = 100;
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => {
        now = 107;
        return "PROXY user:secret@proxy.example:8080; DIRECT";
      },
      now: () => now,
    });
    manager.start();

    await expect(
      manager.resolveShadow({
        targetUrl: "https://api.example/private?prompt=secret",
        clientId: "client-1",
        runtime: "claude",
      }),
    ).resolves.toMatchObject({
      status: "unknown",
      reason: "invalid_pac_proxy_endpoint",
    });
    expect(manager.getDiagnostics()).toEqual([
      expect.objectContaining({
        event: "egress.attempt.started",
        targetOrigin: "https://api.example",
      }),
      expect.objectContaining({
        event: "egress.resolve.failed",
        targetOrigin: "https://api.example",
        resolveMs: 7,
      }),
    ]);
    expect(JSON.stringify(manager.getDiagnostics())).not.toContain("prompt");
    expect(JSON.stringify(manager.getDiagnostics())).not.toContain("secret");
    expect(manager.getSnapshots()).toEqual([
      expect.objectContaining({
        runtime: "claude",
        route: "unknown",
        health: "unknown",
      }),
    ]);
  });

  it("records a redacted proxy on successful resolution", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: { HTTPS_PROXY: "http://user:secret@proxy.example:8080/private" },
      resolveSystemProxy: async () => "DIRECT",
    });
    manager.start();
    await manager.resolveShadow({
      targetUrl: "https://api.example/v1",
      clientId: "client-1",
      runtime: "deepagents",
    });

    const resolved = manager
      .getDiagnostics()
      .find((event) => event.event === "egress.route.resolved");
    expect(resolved).toMatchObject({
      redactedProxy: "http://proxy.example:8080",
      route: "http_proxy",
      source: "env",
    });
    expect(JSON.stringify(resolved)).not.toContain("secret");
    expect(JSON.stringify(resolved)).not.toContain("private");
  });

  it("feature-gates real frontends and records their connection health", async () => {
    const upstream = createServer((_request, response) => response.end("ok"));
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("missing address");
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const descriptor = manager.registerModelIngress({
        clientId: "claude-runtime-1",
        runtime: "claude",
        upstreamBaseUrl: `http://127.0.0.1:${address.port}/v1`,
        supportsWebSocket: true,
      });
      const bootstrap = manager.getBootstrap();
      if (!bootstrap) throw new Error("missing bootstrap");
      const recordPhase = async (phase: "dispatch" | "turn_complete") => {
        const phaseResponse = await fetch(
          `${bootstrap.controlEndpoint}/v1/runtime-phase`,
          {
            method: "POST",
            headers: {
              authorization: `Bearer ${bootstrap.bootstrapToken}`,
              "content-type": "application/json",
            },
            body: JSON.stringify({
              turnAttemptId: "turn-attempt-1234",
              clientId: "claude-runtime-1",
              phase,
              monotonicMs: 99,
            }),
          },
        );
        expect(phaseResponse.status).toBe(202);
      };

      const response = await fetch(`${descriptor.baseUrl}/messages`);
      expect(response.status).toBe(200);
      await response.text();
      expect(manager.getSnapshots()[0]).toMatchObject({
        activeTurn: false,
        requestActive: false,
      });
      // The runtime marker and network event cross processes independently.
      // A slightly late dispatch marker must still claim the current request.
      await recordPhase("dispatch");
      expect(
        manager.getDiagnostics().map((event) => event.event),
      ).toEqual([
        "egress.attempt.started",
        "egress.route.resolved",
        "egress.connect.succeeded",
        "egress.response.headers",
        "egress.stream.established",
        "egress.request.completed",
      ]);
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({
          runtime: "claude",
          frontend: "model_ingress",
          route: "direct",
          health: "healthy",
          activeTurn: true,
          requestActive: false,
        }),
      ]);
      await recordPhase("turn_complete");
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({
          runtime: "claude",
          health: "healthy",
          activeTurn: false,
          requestActive: false,
        }),
      ]);
      manager.revokeClient("claude-runtime-1");
      expect(manager.getSnapshots()).toEqual([]);
    } finally {
      await manager.stop();
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
    }
  });

  it("does not report a completed upstream 502 as healthy", async () => {
    const upstream = createServer((_request, response) => {
      response.writeHead(502).end("bad gateway");
    });
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("missing address");
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const descriptor = manager.registerModelIngress({
        clientId: "codex-runtime-502",
        runtime: "codex",
        upstreamBaseUrl: `http://127.0.0.1:${address.port}/v1`,
        supportsWebSocket: false,
      });

      const response = await fetch(`${descriptor.baseUrl}/responses`);
      expect(response.status).toBe(502);
      await response.text();
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({
          health: "failed",
          responseStatus: 502,
          lastErrorCode: "upstream_http_502",
        }),
      ]);
    } finally {
      await manager.stop();
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
    }
  });

  it("keeps health unknown until the upstream actually responds", async () => {
    let releaseResponse: (() => void) | undefined;
    let markRequestSeen: (() => void) | undefined;
    const requestSeen = new Promise<void>((resolve) => {
      markRequestSeen = resolve;
    });
    const upstream = createServer((_request, response) => {
      releaseResponse = () => response.end("ok");
      markRequestSeen?.();
    });
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("missing address");
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const descriptor = manager.registerModelIngress({
        clientId: "codex-runtime-slow-response",
        runtime: "codex",
        upstreamBaseUrl: `http://127.0.0.1:${address.port}/v1`,
        supportsWebSocket: false,
      });

      const pendingResponse = fetch(`${descriptor.baseUrl}/responses`);
      await requestSeen;
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({ health: "unknown" }),
      ]);
      expect(manager.getDiagnostics().at(-1)?.event).toBe(
        "egress.connect.succeeded",
      );

      releaseResponse?.();
      const response = await pendingResponse;
      await response.text();
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({ health: "healthy" }),
      ]);
    } finally {
      releaseResponse?.();
      await manager.stop();
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
    }
  });

  it("does not mix timing fields from concurrent requests", async () => {
    let slowResponse: ServerResponse | undefined;
    let markSlowRequestSeen: (() => void) | undefined;
    const slowRequestSeen = new Promise<void>((resolve) => {
      markSlowRequestSeen = resolve;
    });
    const upstream = createServer((request, response) => {
      if (request.url === "/slow") {
        slowResponse = response;
        markSlowRequestSeen?.();
        return;
      }
      response.end("fast");
    });
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("missing address");
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const descriptor = manager.registerModelIngress({
        clientId: "claude-runtime-concurrent",
        runtime: "claude",
        upstreamBaseUrl: `http://127.0.0.1:${address.port}`,
        supportsWebSocket: false,
      });

      const pendingSlow = fetch(`${descriptor.baseUrl}/slow`);
      await slowRequestSeen;
      const fast = await fetch(`${descriptor.baseUrl}/fast`);
      await fast.text();
      const fastSnapshot = manager.getSnapshots()[0];
      expect(fastSnapshot.totalMs).toBeDefined();

      slowResponse?.write("slow chunk");
      const slow = await pendingSlow;
      const slowReader = slow.body?.getReader();
      await slowReader?.read();

      expect(manager.getSnapshots()[0]).toMatchObject({
        connectionAttemptId: fastSnapshot.connectionAttemptId,
        responseMs: fastSnapshot.responseMs,
        firstByteMs: fastSnapshot.firstByteMs,
        totalMs: fastSnapshot.totalMs,
      });
      await slowReader?.cancel();
    } finally {
      slowResponse?.end();
      await manager.stop();
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
    }
  });

  it("stamps runtime phases on receipt for a cross-process timeline", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
      now: () => 1_234,
    });
    try {
      await manager.start();
      const bootstrap = manager.getBootstrap();
      expect(bootstrap).not.toBeNull();
      const response = await fetch(`${bootstrap!.controlEndpoint}/v1/runtime-phase`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${bootstrap!.bootstrapToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          turnAttemptId: "turn-attempt-1234",
          clientId: "runtime-client-12",
          phase: "dispatch",
          monotonicMs: 99,
        }),
      });

      expect(response.status).toBe(202);
      expect(manager.getRuntimePhases()).toEqual([
        expect.objectContaining({
          phase: "dispatch",
          monotonicMs: 99,
          observedAt: 1_234,
        }),
      ]);
      manager.revokeClient("runtime-client-12");
      expect(manager.getRuntimePhases()).toEqual([]);
    } finally {
      await manager.stop();
    }
  });

  it("rejects registrations that would route a model ingress back into a manager listener", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const forward = manager.registerForwardProxy({
        clientId: "provider-test-loop",
        runtime: "provider_test",
      });
      const listener = new URL(forward.proxyUrl);
      listener.username = "";
      listener.password = "";

      expect(() =>
        manager.registerModelIngress({
          clientId: "codex-loop",
          runtime: "codex",
          upstreamBaseUrl: listener.href,
          supportsWebSocket: true,
        }),
      ).toThrow("model_ingress_proxy_loop_detected");
    } finally {
      await manager.stop();
    }
  });
});
