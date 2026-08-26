import { createServer, get, request, type Server } from "node:http";
import { connect, type Socket } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForwardProxy } from "./forward-proxy";
import { OutboundResolver } from "./outbound-resolver";
import {
  UpstreamConnector,
  type UpstreamConnection,
} from "./upstream-connector";

const servers: Server[] = [];
const proxies: ForwardProxy[] = [];
const testSockets = new Set<Socket>();

afterEach(async () => {
  await Promise.all(proxies.splice(0).map((proxy) => proxy.stop()));
  for (const socket of testSockets) socket.destroy();
  testSockets.clear();
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise<void>((resolve) => server.close(() => resolve())),
    ),
  );
});

const listen = async (server: Server): Promise<number> => {
  servers.push(server);
  server.on("connection", (socket) => {
    testSockets.add(socket);
    socket.once("close", () => testSockets.delete(socket));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing address");
  return address.port;
};

const makeProxy = async (): Promise<ForwardProxy> => {
  const proxy = new ForwardProxy({
    resolver: new OutboundResolver({
      env: {},
      resolveSystemProxy: async () => "DIRECT",
    }),
  });
  proxies.push(proxy);
  await proxy.start();
  return proxy;
};

const proxyAuth = (proxyUrl: string): string => {
  const parsed = new URL(proxyUrl);
  return `Basic ${Buffer.from(`${parsed.username}:${parsed.password}`).toString("base64")}`;
};

describe("ForwardProxy", () => {
  it("requires its short-lived capability", async () => {
    const proxy = await makeProxy();
    const descriptor = proxy.register({
      clientId: "deepagents-1",
      runtime: "deepagents",
    });
    const local = new URL(descriptor.proxyUrl);

    const status = await new Promise<number>((resolve, reject) => {
      const req = get(
        {
          host: local.hostname,
          port: local.port,
          path: "http://api.example/v1",
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode ?? 0));
        },
      );
      req.once("error", reject);
    });
    expect(status).toBe(407);
  });

  it("relays an absolute-form HTTP request through the shared connector", async () => {
    let observedPath = "";
    let leakedProxyAuth = false;
    const upstreamPort = await listen(
      createServer((incoming, response) => {
        observedPath = incoming.url ?? "";
        leakedProxyAuth = "proxy-authorization" in incoming.headers;
        response.end("provider-response");
      }),
    );
    const proxy = await makeProxy();
    const descriptor = proxy.register({
      clientId: "provider-test-1",
      runtime: "provider_test",
    });
    const local = new URL(descriptor.proxyUrl);
    const result = await new Promise<{ status: number; body: string }>((resolve, reject) => {
      const req = request(
        {
          host: local.hostname,
          port: local.port,
          method: "GET",
          path: `http://127.0.0.1:${upstreamPort}/v1/models?q=opaque`,
          headers: { "proxy-authorization": proxyAuth(descriptor.proxyUrl) },
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => (body += chunk));
          response.on("end", () => resolve({ status: response.statusCode ?? 0, body }));
        },
      );
      req.once("error", reject);
      req.end();
    });

    expect(result).toEqual({ status: 200, body: "provider-response" });
    expect(observedPath).toBe("/v1/models?q=opaque");
    expect(leakedProxyAuth).toBe(false);
  });

  it("uses the connector-owned socket instead of opening a direct HTTP connection", async () => {
    let observedHost = "";
    const upstreamPort = await listen(
      createServer((incoming, response) => {
        observedHost = String(incoming.headers.host ?? "");
        response.end("via-egress-socket");
      }),
    );
    class PreconnectedConnector extends UpstreamConnector {
      override async connect(): Promise<UpstreamConnection> {
        const socket = await new Promise<Socket>((resolve, reject) => {
          const candidate = connect(upstreamPort, "127.0.0.1", () =>
            resolve(candidate),
          );
          candidate.once("error", reject);
        });
        return {
          socket,
          route: {
            kind: "http_proxy",
            url: "http://127.0.0.1:7890",
            source: "system",
          },
          candidateIndex: 0,
          fallbackCount: 0,
          connectMs: 1,
        };
      }
    }
    const proxy = new ForwardProxy({
      resolver: {
        resolve: async () => ({
          targetOrigin: "https://unreachable.invalid",
          candidates: [
            {
              kind: "http_proxy" as const,
              url: "http://127.0.0.1:7890",
              source: "system" as const,
            },
          ],
          resolvedAt: Date.now(),
          ttlMs: 30_000,
          status: "resolved" as const,
        }),
      },
      connector: new PreconnectedConnector(),
    });
    proxies.push(proxy);
    await proxy.start();
    const descriptor = proxy.register({
      clientId: "provider-test-preconnected",
      runtime: "provider_test",
    });
    const local = new URL(descriptor.proxyUrl);

    const result = await new Promise<{ status: number; body: string }>(
      (resolve, reject) => {
        const req = request(
          {
            host: local.hostname,
            port: local.port,
            method: "GET",
            path: "https://unreachable.invalid/v1/models",
            headers: { "proxy-authorization": proxyAuth(descriptor.proxyUrl) },
          },
          (response) => {
            let body = "";
            response.setEncoding("utf8");
            response.on("data", (chunk) => (body += chunk));
            response.on("end", () =>
              resolve({ status: response.statusCode ?? 0, body }),
            );
          },
        );
        req.once("error", reject);
        req.end();
      },
    );

    expect(result).toEqual({ status: 200, body: "via-egress-socket" });
    expect(observedHost).toBe("unreachable.invalid");
  });

  it("establishes CONNECT only after authentication and upstream connect", async () => {
    const targetPort = await listen(
      createServer((_request, response) => response.end("unused")),
    );
    const proxy = await makeProxy();
    const descriptor = proxy.register({
      clientId: "deepagents-1",
      runtime: "deepagents",
    });
    const local = new URL(descriptor.proxyUrl);
    const socket = await new Promise<Socket>((resolve, reject) => {
      const candidate = connect(Number(local.port), local.hostname, () => resolve(candidate));
      candidate.once("error", reject);
    });
    const response = new Promise<string>((resolve, reject) => {
      let buffered = "";
      socket.on("data", (chunk) => {
        buffered += chunk.toString("latin1");
        if (buffered.includes("\r\n\r\n")) resolve(buffered);
      });
      socket.once("error", reject);
    });
    socket.write(
      `CONNECT 127.0.0.1:${targetPort} HTTP/1.1\r\nHost: 127.0.0.1:${targetPort}\r\nProxy-Authorization: ${proxyAuth(descriptor.proxyUrl)}\r\n\r\n`,
    );

    await expect(response).resolves.toContain("200 Connection Established");
    socket.destroy();
  });

  it("revocation invalidates future requests", async () => {
    const proxy = await makeProxy();
    const descriptor = proxy.register({
      clientId: "provider-test-1",
      runtime: "provider_test",
    });
    proxy.revoke("provider-test-1");
    const local = new URL(descriptor.proxyUrl);
    const status = await new Promise<number>((resolve, reject) => {
      const req = get(
        {
          host: local.hostname,
          port: local.port,
          path: "http://api.example/v1",
          headers: { "proxy-authorization": proxyAuth(descriptor.proxyUrl) },
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode ?? 0));
        },
      );
      req.once("error", reject);
    });
    expect(status).toBe(407);
  });

  it("expires capabilities and invalidates a failed cached route", async () => {
    let now = 100;
    const invalidate = vi.fn();
    const proxy = new ForwardProxy({
      resolver: {
        resolve: async () => ({
          targetOrigin: "http://127.0.0.1:1",
          candidates: [{ kind: "direct", source: "system" }],
          resolvedAt: now,
          ttlMs: 30_000,
          status: "resolved",
        }),
        invalidate,
      },
      now: () => now,
    });
    proxies.push(proxy);
    await proxy.start();
    const descriptor = proxy.register({
      clientId: "provider-test-expiring",
      runtime: "provider_test",
      ttlMs: 1_000,
    });
    const local = new URL(descriptor.proxyUrl);

    const status = await new Promise<number>((resolve, reject) => {
      const req = get(
        {
          host: local.hostname,
          port: local.port,
          path: "http://127.0.0.1:1/v1/models",
          headers: { "proxy-authorization": proxyAuth(descriptor.proxyUrl) },
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode ?? 0));
        },
      );
      req.once("error", reject);
    });
    expect(status).toBe(502);
    expect(invalidate).toHaveBeenCalledWith("http://127.0.0.1:1");

    now = 1_101;
    const expiredStatus = await new Promise<number>((resolve, reject) => {
      const req = get(
        {
          host: local.hostname,
          port: local.port,
          path: "http://127.0.0.1:1/v1/models",
          headers: { "proxy-authorization": proxyAuth(descriptor.proxyUrl) },
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode ?? 0));
        },
      );
      req.once("error", reject);
    });
    expect(expiredStatus).toBe(407);
  });
});
