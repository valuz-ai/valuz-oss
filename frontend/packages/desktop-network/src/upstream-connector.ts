import { isIP, connect as netConnect, type Socket } from "node:net";
import { performance } from "node:perf_hooks";
import { connect as tlsConnect, type TLSSocket } from "node:tls";
import type { EgressResolution, EgressRoute } from "./types";

export const DEFAULT_CONNECT_TIMEOUT_MS = 10_000;
export const DEFAULT_CIRCUIT_BREAKER_MS = 5_000;
export const DEFAULT_CIRCUIT_BREAKER_FAILURES = 2;
const MAX_PROXY_RESPONSE_BYTES = 32 * 1024;

export class EgressConnectError extends Error {
  constructor(
    readonly code: string,
    options?: { cause?: unknown },
  ) {
    super(code, options);
    this.name = "EgressConnectError";
  }
}

export interface UpstreamConnection {
  socket: Socket | TLSSocket;
  route: EgressRoute;
  candidateIndex: number;
  fallbackCount: number;
  connectMs: number;
}

export interface UpstreamConnectorOptions {
  connectTimeoutMs?: number;
  circuitBreakerMs?: number;
  circuitBreakerFailures?: number;
  now?: () => number;
}

export interface UpstreamConnectOptions {
  /** CONNECT frontends need a raw tunnel so the client performs target TLS. */
  tlsToTarget?: boolean;
}

const targetPort = (target: URL): number => {
  if (target.port) return Number(target.port);
  return target.protocol === "https:" || target.protocol === "wss:" ? 443 : 80;
};

const targetHostname = (target: URL): string =>
  target.hostname.replace(/^\[|\]$/g, "");

const isLoopbackHost = (hostname: string): boolean => {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return (
    normalized === "localhost" ||
    normalized.endsWith(".localhost") ||
    normalized === "::1" ||
    (isIP(normalized) === 4 && normalized.startsWith("127."))
  );
};

const write = (socket: Socket, data: Uint8Array | string): Promise<void> =>
  new Promise((resolve, reject) => {
    socket.write(data, (error) => (error ? reject(error) : resolve()));
  });

const readAtLeast = (socket: Socket, length: number): Promise<Buffer> =>
  new Promise((resolve, reject) => {
    let buffered = Buffer.alloc(0);
    const cleanup = () => {
      socket.off("data", onData);
      socket.off("error", onError);
      socket.off("close", onClose);
      socket.off("timeout", onTimeout);
    };
    const onError = (error: Error) => {
      cleanup();
      reject(error);
    };
    const onClose = () => {
      cleanup();
      reject(new EgressConnectError("proxy_closed_during_handshake"));
    };
    const onTimeout = () => {
      cleanup();
      reject(new EgressConnectError("proxy_handshake_timeout"));
    };
    const onData = (chunk: Buffer) => {
      buffered = Buffer.concat([buffered, chunk]);
      if (buffered.length < length) return;
      cleanup();
      const remainder = buffered.subarray(length);
      socket.pause();
      if (remainder.length > 0) socket.unshift(remainder);
      resolve(buffered.subarray(0, length));
    };
    socket.on("data", onData);
    socket.once("error", onError);
    socket.once("close", onClose);
    socket.once("timeout", onTimeout);
    socket.resume();
  });

const readUntil = (
  socket: Socket,
  marker: Uint8Array,
  maxBytes: number,
): Promise<Buffer> =>
  new Promise((resolve, reject) => {
    let buffered = Buffer.alloc(0);
    const cleanup = () => {
      socket.off("data", onData);
      socket.off("error", onError);
      socket.off("close", onClose);
      socket.off("timeout", onTimeout);
    };
    const onError = (error: Error) => {
      cleanup();
      reject(error);
    };
    const onClose = () => {
      cleanup();
      reject(new EgressConnectError("proxy_closed_during_handshake"));
    };
    const onTimeout = () => {
      cleanup();
      reject(new EgressConnectError("proxy_handshake_timeout"));
    };
    const onData = (chunk: Buffer) => {
      buffered = Buffer.concat([buffered, chunk]);
      if (buffered.length > maxBytes) {
        cleanup();
        reject(new EgressConnectError("proxy_response_too_large"));
        return;
      }
      const index = buffered.indexOf(marker);
      if (index < 0) return;
      cleanup();
      const end = index + marker.length;
      const remainder = buffered.subarray(end);
      socket.pause();
      if (remainder.length > 0) socket.unshift(remainder);
      resolve(buffered.subarray(0, end));
    };
    socket.on("data", onData);
    socket.once("error", onError);
    socket.once("close", onClose);
    socket.once("timeout", onTimeout);
    socket.resume();
  });

const connectTcp = (
  hostname: string,
  port: number,
  timeoutMs: number,
): Promise<Socket> =>
  new Promise((resolve, reject) => {
    const socket = netConnect({ host: hostname, port });
    const cleanup = () => {
      socket.off("connect", onConnect);
      socket.off("error", onError);
      socket.off("timeout", onTimeout);
    };
    const onConnect = () => {
      cleanup();
      socket.setTimeout(0);
      socket.setNoDelay(true);
      resolve(socket);
    };
    const onError = (error: Error) => {
      cleanup();
      socket.destroy();
      reject(error);
    };
    const onTimeout = () => {
      cleanup();
      socket.destroy();
      reject(new EgressConnectError("connect_timeout"));
    };
    socket.once("connect", onConnect);
    socket.once("error", onError);
    socket.once("timeout", onTimeout);
    socket.setTimeout(timeoutMs);
  });

const httpProxyTunnel = async (
  target: URL,
  route: Extract<EgressRoute, { kind: "http_proxy" }>,
  timeoutMs: number,
): Promise<Socket> => {
  const deadline = performance.now() + timeoutMs;
  const proxy = new URL(route.url);
  const socket = await connectTcp(
    targetHostname(proxy),
    Number(proxy.port || 80),
    timeoutMs,
  );
  const handshakeTimeoutMs = Math.max(
    1,
    Math.ceil(deadline - performance.now()),
  );
  const handshakeTimer = setTimeout(
    () => socket.destroy(new EgressConnectError("proxy_handshake_timeout")),
    handshakeTimeoutMs,
  );
  handshakeTimer.unref();
  try {
    socket.setTimeout(handshakeTimeoutMs);
    const hostname = targetHostname(target);
    const authority = `${isIP(hostname) === 6 ? `[${hostname}]` : hostname}:${targetPort(target)}`;
    const authorization =
      proxy.username || proxy.password
        ? `Proxy-Authorization: Basic ${Buffer.from(
            `${decodeURIComponent(proxy.username)}:${decodeURIComponent(proxy.password)}`,
          ).toString("base64")}\r\n`
        : "";
    await write(
      socket,
      `CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n${authorization}Proxy-Connection: Keep-Alive\r\n\r\n`,
    );
    const response = await readUntil(
      socket,
      Buffer.from("\r\n\r\n"),
      MAX_PROXY_RESPONSE_BYTES,
    );
    const statusLine = response.toString("latin1").split("\r\n", 1)[0];
    const match = /^HTTP\/\d(?:\.\d)?\s+(\d{3})\b/.exec(statusLine);
    if (!match) throw new EgressConnectError("invalid_http_proxy_response");
    if (Number(match[1]) !== 200) {
      throw new EgressConnectError(`http_proxy_status_${match[1]}`);
    }
    socket.setTimeout(0);
    socket.resume();
    return socket;
  } catch (error) {
    socket.destroy();
    throw error;
  } finally {
    clearTimeout(handshakeTimer);
  }
};

const socksAddress = (hostname: string): Buffer => {
  const ipVersion = isIP(hostname);
  if (ipVersion === 4) {
    return Buffer.from([0x01, ...hostname.split(".").map(Number)]);
  }
  if (ipVersion === 6) {
    const normalized = hostname.replace(/^\[|\]$/g, "");
    const [leftRaw, rightRaw = ""] = normalized.split("::", 2);
    const left = leftRaw ? leftRaw.split(":") : [];
    const right = rightRaw ? rightRaw.split(":") : [];
    const missing = 8 - left.length - right.length;
    const words = [...left, ...Array(Math.max(0, missing)).fill("0"), ...right];
    const bytes = words.flatMap((word) => {
      const value = Number.parseInt(word || "0", 16);
      return [(value >> 8) & 0xff, value & 0xff];
    });
    return Buffer.from([0x04, ...bytes]);
  }
  const encoded = Buffer.from(hostname);
  if (encoded.length === 0 || encoded.length > 255) {
    throw new EgressConnectError("invalid_socks_target_host");
  }
  return Buffer.concat([Buffer.from([0x03, encoded.length]), encoded]);
};

const socks5Tunnel = async (
  target: URL,
  route: Extract<EgressRoute, { kind: "socks5_proxy" }>,
  timeoutMs: number,
): Promise<Socket> => {
  const deadline = performance.now() + timeoutMs;
  const proxy = new URL(route.url);
  const socket = await connectTcp(
    targetHostname(proxy),
    Number(proxy.port || 1080),
    timeoutMs,
  );
  const handshakeTimeoutMs = Math.max(
    1,
    Math.ceil(deadline - performance.now()),
  );
  const handshakeTimer = setTimeout(
    () => socket.destroy(new EgressConnectError("proxy_handshake_timeout")),
    handshakeTimeoutMs,
  );
  handshakeTimer.unref();
  try {
    socket.setTimeout(handshakeTimeoutMs);
    const hasCredentials = Boolean(proxy.username || proxy.password);
    await write(
      socket,
      Buffer.from(hasCredentials ? [0x05, 0x02, 0x00, 0x02] : [0x05, 0x01, 0x00]),
    );
    const greeting = await readAtLeast(socket, 2);
    if (greeting[0] !== 0x05 || greeting[1] === 0xff) {
      throw new EgressConnectError("socks5_auth_method_rejected");
    }
    if (greeting[1] === 0x02) {
      const username = Buffer.from(decodeURIComponent(proxy.username));
      const password = Buffer.from(decodeURIComponent(proxy.password));
      if (username.length > 255 || password.length > 255) {
        throw new EgressConnectError("socks5_credentials_too_long");
      }
      await write(
        socket,
        Buffer.concat([
          Buffer.from([0x01, username.length]),
          username,
          Buffer.from([password.length]),
          password,
        ]),
      );
      const auth = await readAtLeast(socket, 2);
      if (auth[1] !== 0x00) throw new EgressConnectError("socks5_auth_failed");
    } else if (greeting[1] !== 0x00) {
      throw new EgressConnectError("unsupported_socks5_auth_method");
    }

    const port = targetPort(target);
    await write(
      socket,
      Buffer.concat([
        Buffer.from([0x05, 0x01, 0x00]),
        socksAddress(targetHostname(target)),
        Buffer.from([(port >> 8) & 0xff, port & 0xff]),
      ]),
    );
    const responseHead = await readAtLeast(socket, 4);
    if (responseHead[0] !== 0x05 || responseHead[1] !== 0x00) {
      throw new EgressConnectError(`socks5_connect_status_${responseHead[1]}`);
    }
    const addressLength =
      responseHead[3] === 0x01
        ? 4
        : responseHead[3] === 0x04
          ? 16
          : responseHead[3] === 0x03
            ? (await readAtLeast(socket, 1))[0]
            : -1;
    if (addressLength < 0) throw new EgressConnectError("invalid_socks5_response");
    await readAtLeast(socket, addressLength + 2);
    socket.setTimeout(0);
    socket.resume();
    return socket;
  } catch (error) {
    socket.destroy();
    throw error;
  } finally {
    clearTimeout(handshakeTimer);
  }
};

const wrapTls = (
  socket: Socket,
  target: URL,
  timeoutMs: number,
): Promise<TLSSocket> =>
  new Promise((resolve, reject) => {
    const hostname = targetHostname(target);
    const tlsSocket = tlsConnect({
      socket,
      servername: isIP(hostname) === 0 ? hostname : undefined,
      ALPNProtocols: ["http/1.1"],
    });
    const cleanup = () => {
      tlsSocket.off("secureConnect", onConnect);
      tlsSocket.off("error", onError);
      tlsSocket.off("timeout", onTimeout);
    };
    const onConnect = () => {
      cleanup();
      tlsSocket.setTimeout(0);
      resolve(tlsSocket);
    };
    const onError = (error: Error) => {
      cleanup();
      tlsSocket.destroy();
      reject(error);
    };
    const onTimeout = () => {
      cleanup();
      tlsSocket.destroy();
      reject(new EgressConnectError("tls_handshake_timeout"));
    };
    tlsSocket.once("secureConnect", onConnect);
    tlsSocket.once("error", onError);
    tlsSocket.once("timeout", onTimeout);
    tlsSocket.setTimeout(timeoutMs);
  });

const stableConnectError = (error: unknown): EgressConnectError => {
  if (error instanceof EgressConnectError) return error;
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String(error.code).toLowerCase()
      : "connect_failed";
  return new EgressConnectError(code, { cause: error });
};

/**
 * Shared candidate connector for both local frontends. It never sees HTTP
 * bodies and only falls through after a connection/handshake failure, before
 * callers receive a socket and can write business bytes.
 */
export class UpstreamConnector {
  private readonly timeoutMs: number;
  private readonly now: () => number;
  private readonly circuitBreakerMs: number;
  private readonly circuitBreakerFailures: number;
  private protectedLoopbackPorts = new Set<number>();
  private readonly failures = new Map<
    string,
    { count: number; openUntil: number }
  >();

  constructor(options: UpstreamConnectorOptions = {}) {
    this.timeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS;
    this.circuitBreakerMs =
      options.circuitBreakerMs ?? DEFAULT_CIRCUIT_BREAKER_MS;
    this.circuitBreakerFailures =
      options.circuitBreakerFailures ?? DEFAULT_CIRCUIT_BREAKER_FAILURES;
    this.now = options.now ?? Date.now;
  }

  /** Replace the manager-owned listener set used to reject local proxy loops. */
  setProtectedLoopbackPorts(ports: Iterable<number>): void {
    this.protectedLoopbackPorts = new Set(
      [...ports].filter((port) => Number.isInteger(port) && port > 0 && port <= 65_535),
    );
  }

  isProtectedTarget(target: URL): boolean {
    return (
      isLoopbackHost(targetHostname(target)) &&
      this.protectedLoopbackPorts.has(targetPort(target))
    );
  }

  private routeKey(target: URL, route: EgressRoute): string {
    if (!("url" in route)) {
      return `${target.origin}:${route.kind}:${route.source}`;
    }
    const proxy = new URL(route.url);
    // Circuit-breaker bookkeeping only needs the endpoint identity. Keeping
    // userinfo here would create another in-memory copy of proxy credentials.
    return `${target.origin}:${route.kind}:${proxy.protocol}//${proxy.hostname}:${proxy.port}`;
  }

  private circuitOpen(key: string): boolean {
    const failure = this.failures.get(key);
    if (!failure || failure.openUntil <= this.now()) {
      if (failure?.openUntil) this.failures.delete(key);
      return false;
    }
    return true;
  }

  private recordFailure(key: string): void {
    const previous = this.failures.get(key);
    const count = (previous?.count ?? 0) + 1;
    this.failures.set(key, {
      count,
      openUntil:
        count >= this.circuitBreakerFailures
          ? this.now() + this.circuitBreakerMs
          : 0,
    });
  }

  async connect(
    target: URL,
    resolution: EgressResolution,
    options: UpstreamConnectOptions = {},
  ): Promise<UpstreamConnection> {
    if (this.isProtectedTarget(target)) {
      throw new EgressConnectError("egress_proxy_loop_detected");
    }
    if (resolution.status !== "resolved" || resolution.candidates.length === 0) {
      throw new EgressConnectError(resolution.reason ?? "egress_route_unresolved");
    }

    // One wall-clock budget covers the complete candidate chain, proxy
    // handshakes and target TLS. A long PAC list must not multiply the user
    // visible timeout by one full budget per candidate.
    const deadline = performance.now() + this.timeoutMs;
    const remainingTimeout = (): number =>
      Math.max(1, Math.ceil(deadline - performance.now()));
    const connectStartedAt = this.now();
    let lastError: EgressConnectError | undefined;
    for (const [candidateIndex, route] of resolution.candidates.entries()) {
      const routeKey = this.routeKey(target, route);
      try {
        if (performance.now() >= deadline) {
          throw new EgressConnectError("connect_timeout");
        }
        if (this.circuitOpen(routeKey)) {
          throw new EgressConnectError("egress_candidate_circuit_open");
        }
        if (
          route.kind !== "direct" &&
          this.isProtectedTarget(new URL(route.url))
        ) {
          throw new EgressConnectError("egress_proxy_loop_detected");
        }
        const raw =
          route.kind === "direct"
            ? await connectTcp(
                targetHostname(target),
                targetPort(target),
                remainingTimeout(),
              )
            : route.kind === "http_proxy"
              ? await httpProxyTunnel(target, route, remainingTimeout())
              : await socks5Tunnel(target, route, remainingTimeout());
        const secure =
          options.tlsToTarget ??
          (target.protocol === "https:" || target.protocol === "wss:");
        if (secure && performance.now() >= deadline) {
          raw.destroy();
          throw new EgressConnectError("connect_timeout");
        }
        const socket = secure
          ? await wrapTls(raw, target, remainingTimeout())
          : raw;
        this.failures.delete(routeKey);
        return {
          socket,
          route,
          candidateIndex,
          fallbackCount: candidateIndex,
          connectMs: Math.max(0, this.now() - connectStartedAt),
        };
      } catch (error) {
        lastError = stableConnectError(error);
        if (lastError.code !== "egress_candidate_circuit_open") {
          this.recordFailure(routeKey);
        }
      }
    }
    throw new EgressConnectError(lastError?.code ?? "egress_candidates_exhausted", {
      cause: lastError,
    });
  }
}
