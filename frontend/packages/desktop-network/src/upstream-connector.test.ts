import { createServer, type Socket } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { UpstreamConnector } from "./upstream-connector";
import type { EgressResolution } from "./types";

const servers: ReturnType<typeof createServer>[] = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise<void>((resolve) => server.close(() => resolve())),
    ),
  );
});

const listen = async (onConnection: (socket: Socket) => void): Promise<number> => {
  const server = createServer(onConnection);
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing address");
  return address.port;
};

const resolved = (
  candidates: EgressResolution["candidates"],
): EgressResolution => ({
  targetOrigin: "http://127.0.0.1",
  candidates,
  resolvedAt: 0,
  ttlMs: 30_000,
  status: "resolved",
});

const roundTrip = (socket: Socket, payload: string): Promise<string> =>
  new Promise((resolve, reject) => {
    socket.once("data", (chunk) => resolve(chunk.toString()));
    socket.once("error", reject);
    socket.write(payload);
  });

describe("UpstreamConnector", () => {
  it("opens a direct socket", async () => {
    const port = await listen((socket) => socket.on("data", (data) => socket.write(data)));
    const connection = await new UpstreamConnector().connect(
      new URL(`http://127.0.0.1:${port}`),
      resolved([{ kind: "direct", source: "system" }]),
    );

    await expect(roundTrip(connection.socket, "direct")).resolves.toBe("direct");
    expect(connection).toMatchObject({ candidateIndex: 0, fallbackCount: 0 });
    connection.socket.destroy();
  });

  it("performs an authenticated HTTP CONNECT handshake", async () => {
    let request = "";
    const proxyPort = await listen((socket) => {
      socket.once("data", (data) => {
        request = data.toString("latin1");
        socket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        socket.on("data", (payload) => socket.write(payload));
      });
    });
    const connection = await new UpstreamConnector().connect(
      new URL("http://api.example:8080"),
      resolved([
        {
          kind: "http_proxy",
          url: `http://user:secret@127.0.0.1:${proxyPort}`,
          source: "env",
        },
      ]),
    );

    await expect(roundTrip(connection.socket, "proxy")).resolves.toBe("proxy");
    expect(request).toContain("CONNECT api.example:8080 HTTP/1.1");
    expect(request).toContain(
      `Proxy-Authorization: Basic ${Buffer.from("user:secret").toString("base64")}`,
    );
    connection.socket.destroy();
  });

  it("falls through only when an explicit next candidate exists", async () => {
    const targetPort = await listen((socket) =>
      socket.on("data", (data) => socket.write(data)),
    );
    const rejectingProxy = await listen((socket) => {
      socket.once("data", () => {
        socket.end("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n");
      });
    });
    const connection = await new UpstreamConnector().connect(
      new URL(`http://127.0.0.1:${targetPort}`),
      resolved([
        {
          kind: "http_proxy",
          url: `http://127.0.0.1:${rejectingProxy}`,
          source: "system",
        },
        { kind: "direct", source: "system" },
      ]),
    );

    expect(connection).toMatchObject({ candidateIndex: 1, fallbackCount: 1 });
    await expect(roundTrip(connection.socket, "fallback")).resolves.toBe(
      "fallback",
    );
    connection.socket.destroy();
  });

  it("measures connect latency across the complete fallback chain", async () => {
    let now = 100;
    const targetPort = await listen((socket) => {
      socket.on("error", () => undefined);
      now = 200;
      socket.on("data", (data) => socket.write(data));
    });
    const rejectingProxy = await listen((socket) => {
      socket.on("error", () => undefined);
      socket.once("data", () => {
        now = 180;
        socket.end("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n");
      });
    });
    const connection = await new UpstreamConnector({ now: () => now }).connect(
      new URL(`http://127.0.0.1:${targetPort}`),
      resolved([
        {
          kind: "http_proxy",
          url: `http://127.0.0.1:${rejectingProxy}`,
          source: "system",
        },
        { kind: "direct", source: "system" },
      ]),
    );

    const { candidateIndex, fallbackCount, connectMs } = connection;
    connection.socket.destroy();
    expect({ candidateIndex, fallbackCount }).toEqual({
      candidateIndex: 1,
      fallbackCount: 1,
    });
    expect(connectMs).toBeGreaterThanOrEqual(80);
  });

  it("bounds a proxy that accepts TCP but never completes its handshake", async () => {
    let accepted: Socket | undefined;
    const proxyPort = await listen((socket) => {
      accepted = socket;
    });
    const connector = new UpstreamConnector({ connectTimeoutMs: 20 });

    await expect(
      connector.connect(
        new URL("https://api.example"),
        resolved([
          {
            kind: "http_proxy",
            url: `http://127.0.0.1:${proxyPort}`,
            source: "system",
          },
        ]),
      ),
    ).rejects.toMatchObject({ code: "proxy_handshake_timeout" });
    accepted?.destroy();
  });

  it("speaks SOCKS5 without exposing the target as an HTTP request", async () => {
    let targetHost = "";
    const proxyPort = await listen((socket) => {
      socket.once("data", () => {
        socket.write(Buffer.from([0x05, 0x00]));
        socket.once("data", (request) => {
          const length = request[4];
          targetHost = request.subarray(5, 5 + length).toString();
          socket.write(Buffer.from([0x05, 0x00, 0x00, 0x01, 127, 0, 0, 1, 0, 80]));
          socket.on("data", (payload) => socket.write(payload));
        });
      });
    });
    const connection = await new UpstreamConnector().connect(
      new URL("http://api.example:80"),
      resolved([
        {
          kind: "socks5_proxy",
          url: `socks5://127.0.0.1:${proxyPort}`,
          source: "system",
        },
      ]),
    );

    expect(targetHost).toBe("api.example");
    await expect(roundTrip(connection.socket, "socks")).resolves.toBe("socks");
    connection.socket.destroy();
  });

  it("fails loud when resolution has no usable candidate", async () => {
    await expect(
      new UpstreamConnector().connect(new URL("https://api.example"), {
        targetOrigin: "https://api.example",
        candidates: [],
        resolvedAt: 0,
        ttlMs: 0,
        status: "unknown",
        reason: "system_proxy_resolution_failed",
      }),
    ).rejects.toEqual(
      expect.objectContaining({
        code: "system_proxy_resolution_failed",
      }),
    );
  });

  it("rejects a target or proxy route that points back to a protected listener", async () => {
    const connector = new UpstreamConnector();
    connector.setProtectedLoopbackPorts([43123]);

    await expect(
      connector.connect(
        new URL("http://127.0.0.1:43123/v1"),
        resolved([{ kind: "direct", source: "system" }]),
      ),
    ).rejects.toMatchObject({ code: "egress_proxy_loop_detected" });
    await expect(
      connector.connect(
        new URL("http://api.example"),
        resolved([
          {
            kind: "http_proxy",
            url: "http://localhost:43123",
            source: "system",
          },
        ]),
      ),
    ).rejects.toMatchObject({ code: "egress_proxy_loop_detected" });
  });

  it("temporarily opens a failing candidate circuit and still uses an explicit fallback", async () => {
    let now = 100;
    let rejectedConnections = 0;
    const rejectingProxy = await listen((socket) => {
      rejectedConnections += 1;
      socket.once("data", () =>
        socket.end("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"),
      );
    });
    const targetPort = await listen((socket) =>
      socket.on("data", (data) => socket.write(data)),
    );
    const connector = new UpstreamConnector({ now: () => now });
    const resolution = resolved([
      {
        kind: "http_proxy",
        url: `http://127.0.0.1:${rejectingProxy}`,
        source: "system",
      },
      { kind: "direct", source: "system" },
    ]);
    const target = new URL(`http://127.0.0.1:${targetPort}`);

    (await connector.connect(target, resolution)).socket.destroy();
    (await connector.connect(target, resolution)).socket.destroy();
    const third = await connector.connect(target, resolution);
    third.socket.destroy();
    expect(rejectedConnections).toBe(2);

    now += 5_001;
    (await connector.connect(target, resolution)).socket.destroy();
    expect(rejectedConnections).toBe(3);
  });

  it("does not retain proxy credentials in circuit-breaker state", async () => {
    const rejectingProxy = await listen((socket) => {
      socket.once("data", () =>
        socket.end("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"),
      );
    });
    const connector = new UpstreamConnector();

    await expect(
      connector.connect(
        new URL("http://api.example"),
        resolved([
          {
            kind: "http_proxy",
            url: `http://user:do-not-retain@127.0.0.1:${rejectingProxy}`,
            source: "env",
          },
        ]),
      ),
    ).rejects.toBeDefined();

    expect(JSON.stringify(connector)).not.toContain("do-not-retain");
  });
});
