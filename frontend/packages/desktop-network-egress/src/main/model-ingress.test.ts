import { createServer, request, type Server } from "node:http";
import { connect, type Socket } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelIngress } from "./model-ingress";
import { OutboundResolver } from "./outbound-resolver";
import {
  UpstreamConnector,
  type UpstreamConnection,
} from "./upstream-connector";

const servers: Server[] = [];
const ingresses: ModelIngress[] = [];
const testSockets = new Set<Socket>();

afterEach(async () => {
  await Promise.all(ingresses.splice(0).map((ingress) => ingress.stop()));
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

const makeIngress = async (): Promise<ModelIngress> => {
  const ingress = new ModelIngress({
    resolver: new OutboundResolver({
      env: {},
      resolveSystemProxy: async () => "DIRECT",
    }),
  });
  ingresses.push(ingress);
  await ingress.start();
  return ingress;
};

const post = (
  rawUrl: string,
  body: Buffer,
): Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: Buffer }> =>
  new Promise((resolve, reject) => {
    const target = new URL(rawUrl);
    const req = request(
      {
        host: target.hostname,
        port: target.port,
        path: `${target.pathname}${target.search}`,
        method: "POST",
        headers: {
          authorization: "Bearer provider-secret",
          "chatgpt-account-id": "account-opaque",
          "anthropic-beta": "oauth-opaque",
          "content-type": "application/octet-stream",
          "content-length": body.length,
        },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () =>
          resolve({
            status: response.statusCode ?? 0,
            headers: response.headers,
            body: Buffer.concat(chunks),
          }),
        );
      },
    );
    req.once("error", reject);
    req.end(body);
  });

describe("ModelIngress", () => {
  it("relays request and SSE bytes without changing provider auth or body", async () => {
    let observedPath = "";
    let observedAuth = "";
    let observedAccount = "";
    let observedBeta = "";
    let observedBody = Buffer.alloc(0);
    const upstreamPort = await listen(
      createServer((request, response) => {
        observedPath = request.url ?? "";
        observedAuth = String(request.headers.authorization ?? "");
        observedAccount = String(request.headers["chatgpt-account-id"] ?? "");
        observedBeta = String(request.headers["anthropic-beta"] ?? "");
        const chunks: Buffer[] = [];
        request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        request.on("end", () => {
          observedBody = Buffer.concat(chunks);
          response.writeHead(200, { "content-type": "text/event-stream" });
          response.write(Buffer.from("data: first\n\n"));
          response.end(Buffer.from("data: second\n\n"));
        });
      }),
    );
    const ingress = await makeIngress();
    const descriptor = ingress.register({
      clientId: "runtime-1",
      runtime: "claude",
      upstreamBaseUrl: `http://127.0.0.1:${upstreamPort}/v1`,
      supportsWebSocket: true,
    });
    const payload = Buffer.from([0, 1, 2, 3, 255]);

    const result = await post(`${descriptor.baseUrl}/messages?q=opaque`, payload);

    expect(result.status).toBe(200);
    expect(result.headers["content-type"]).toContain("text/event-stream");
    expect(result.body).toEqual(Buffer.from("data: first\n\ndata: second\n\n"));
    expect(observedPath).toBe("/v1/messages?q=opaque");
    expect(observedAuth).toBe("Bearer provider-secret");
    expect(observedAccount).toBe("account-opaque");
    expect(observedBeta).toBe("oauth-opaque");
    expect(observedBody).toEqual(payload);
  });

  it("uses the connector-owned socket instead of opening a direct HTTP connection", async () => {
    let observedHost = "";
    const upstreamPort = await listen(
      createServer((request, response) => {
        observedHost = String(request.headers.host ?? "");
        request.resume();
        request.on("end", () => response.end("via-egress-socket"));
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
    const ingress = new ModelIngress({
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
    ingresses.push(ingress);
    await ingress.start();
    const descriptor = ingress.register({
      clientId: "runtime-preconnected",
      runtime: "codex",
      upstreamBaseUrl: "https://unreachable.invalid/v1",
      supportsWebSocket: false,
    });

    const result = await post(
      `${descriptor.baseUrl}/responses`,
      Buffer.from("opaque-request"),
    );

    expect(result.status).toBe(200);
    expect(result.body.toString()).toBe("via-egress-socket");
    expect(observedHost).toBe("unreachable.invalid");
  });

  it("terminates the downstream response when an upstream stream aborts", async () => {
    const upstreamPort = await listen(
      createServer((_request, response) => {
        response.writeHead(200, { "content-type": "text/event-stream" });
        response.write("data: partial\n\n");
        setImmediate(() => response.socket?.destroy());
      }),
    );
    const phases: string[] = [];
    let resolveAborted: (() => void) | undefined;
    const aborted = new Promise<void>((resolve) => {
      resolveAborted = resolve;
    });
    const ingress = new ModelIngress({
      resolver: new OutboundResolver({
        env: {},
        resolveSystemProxy: async () => "DIRECT",
      }),
      onRequest: (event) => {
        phases.push(event.phase);
        if (event.phase === "aborted") resolveAborted?.();
      },
    });
    ingresses.push(ingress);
    await ingress.start();
    const descriptor = ingress.register({
      clientId: "runtime-aborted",
      runtime: "codex",
      upstreamBaseUrl: `http://127.0.0.1:${upstreamPort}/v1`,
      supportsWebSocket: false,
    });

    const settled = fetch(`${descriptor.baseUrl}/responses`, {
      method: "POST",
      body: "opaque",
    })
      .then((response) => response.text())
      .catch(() => undefined);
    await Promise.race([
      Promise.all([aborted, settled]),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("relay_did_not_settle")), 2_000),
      ),
    ]);

    expect(phases).toContain("headers_received");
    expect(phases).toContain("first_byte");
    expect(phases.at(-1)).toBe("aborted");
  });

  it("revokes the random path capability and never accepts absolute targets", async () => {
    let upstreamRequests = 0;
    const upstreamPort = await listen(
      createServer((_request, response) => {
        upstreamRequests += 1;
        response.end("unexpected");
      }),
    );
    const ingress = await makeIngress();
    const descriptor = ingress.register({
      clientId: "runtime-1",
      runtime: "codex",
      upstreamBaseUrl: `http://127.0.0.1:${upstreamPort}/v1`,
      supportsWebSocket: true,
    });
    ingress.revoke("runtime-1");

    await expect(post(`${descriptor.baseUrl}/responses`, Buffer.alloc(0))).resolves.toMatchObject({
      status: 404,
    });

    const local = new URL(descriptor.baseUrl);
    const absoluteStatus = await new Promise<number>((resolve, reject) => {
      const req = request(
        {
          host: local.hostname,
          port: local.port,
          method: "GET",
          path: "http://attacker.example/private",
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode ?? 0));
        },
      );
      req.once("error", reject);
      req.end();
    });
    expect(absoluteStatus).toBe(404);
    expect(upstreamRequests).toBe(0);
  });

  it("relays WebSocket upgrades while stripping the local capability path", async () => {
    let observedPath = "";
    let observedAuth = "";
    let observedAccount = "";
    const upstream = createServer();
    upstream.on("upgrade", (request, socket) => {
      observedPath = request.url ?? "";
      observedAuth = String(request.headers.authorization ?? "");
      observedAccount = String(request.headers["chatgpt-account-id"] ?? "");
      socket.write(
        "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n",
      );
      socket.on("data", (chunk) => socket.write(chunk));
    });
    const upstreamPort = await listen(upstream);
    const ingress = await makeIngress();
    const descriptor = ingress.register({
      clientId: "runtime-1",
      runtime: "codex",
      upstreamBaseUrl: `http://127.0.0.1:${upstreamPort}/v1`,
      supportsWebSocket: true,
    });
    const local = new URL(descriptor.baseUrl);
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
      `GET ${local.pathname}/responses/ws?opaque=1 HTTP/1.1\r\nHost: ${local.host}\r\nAuthorization: Bearer provider-secret\r\nChatGPT-Account-Id: account-opaque\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGVzdA==\r\nSec-WebSocket-Version: 13\r\n\r\n`,
    );

    await expect(response).resolves.toContain("101 Switching Protocols");
    expect(observedPath).toBe("/v1/responses/ws?opaque=1");
    expect(observedAuth).toBe("Bearer provider-secret");
    expect(observedAccount).toBe("account-opaque");
    socket.destroy();
  });

  it("rewrites same-origin redirects and rejects redirects outside the registered upstream", async () => {
    let mode: "same" | "cross" = "same";
    const upstreamPort = await listen(
      createServer((_request, response) => {
        response.writeHead(307, {
          location:
            mode === "same"
              ? "/v1/responses/next?cursor=1"
              : "https://attacker.example/collect",
        });
        response.end();
      }),
    );
    const ingress = await makeIngress();
    const descriptor = ingress.register({
      clientId: "runtime-redirect",
      runtime: "codex",
      upstreamBaseUrl: `http://127.0.0.1:${upstreamPort}/v1`,
      supportsWebSocket: true,
    });

    const sameOrigin = await post(`${descriptor.baseUrl}/responses`, Buffer.alloc(0));
    expect(sameOrigin.status).toBe(307);
    expect(sameOrigin.headers.location).toBe(
      `${new URL(descriptor.baseUrl).pathname}/responses/next?cursor=1`,
    );

    mode = "cross";
    await expect(
      post(`${descriptor.baseUrl}/responses`, Buffer.alloc(0)),
    ).resolves.toMatchObject({ status: 502 });
  });

  it("expires capabilities and invalidates a failed cached route", async () => {
    let now = 100;
    const invalidate = vi.fn();
    const ingress = new ModelIngress({
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
    ingresses.push(ingress);
    await ingress.start();
    const failing = ingress.register({
      clientId: "runtime-failing",
      runtime: "codex",
      upstreamBaseUrl: "http://127.0.0.1:1/v1",
      supportsWebSocket: true,
      ttlMs: 1_000,
    });

    await expect(post(`${failing.baseUrl}/responses`, Buffer.alloc(0))).resolves.toMatchObject({
      status: 502,
    });
    expect(invalidate).toHaveBeenCalledWith("http://127.0.0.1:1");

    now = 1_101;
    await expect(post(`${failing.baseUrl}/responses`, Buffer.alloc(0))).resolves.toMatchObject({
      status: 404,
    });
  });
});
