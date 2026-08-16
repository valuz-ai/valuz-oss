import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import {
  createServer,
  request as httpRequest,
  type IncomingHttpHeaders,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { Socket } from "node:net";
import type { Duplex } from "node:stream";
import type { OutboundResolver } from "./outbound-resolver";
import { createPreconnectedHttpAgent } from "./preconnected-http";
import type { EgressRequestEvent, EgressRuntime } from "./types";
import {
  EgressConnectError,
  UpstreamConnector,
  type UpstreamConnection,
} from "./upstream-connector";

export const DEFAULT_FORWARD_PROXY_TTL_MS = 12 * 60 * 60 * 1000;

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

export interface ForwardProxyRegistration {
  clientId: string;
  runtime: Extract<EgressRuntime, "deepagents" | "provider_test">;
  ttlMs?: number;
}

export interface ForwardProxyDescriptor {
  kind: "forward_proxy";
  proxyUrl: string;
  clientId: string;
  expiresAt: number;
}

interface StoredCapability {
  username: string;
  secret: string;
  clientId: string;
  runtime: Extract<EgressRuntime, "deepagents" | "provider_test">;
  expiresAt: number;
}

export interface ForwardProxyConnectionEvent {
  connectionAttemptId: string;
  startedAt: number;
  registration: Pick<StoredCapability, "clientId" | "runtime">;
  targetOrigin: string;
  resolutionMs: number;
  connection?: Pick<
    UpstreamConnection,
    "route" | "candidateIndex" | "fallbackCount" | "connectMs"
  >;
  errorCode?: string;
}

export interface ForwardProxyOptions {
  resolver: Pick<OutboundResolver, "resolve"> &
    Partial<Pick<OutboundResolver, "invalidate">>;
  connector?: UpstreamConnector;
  mode?: "auto" | "direct";
  now?: () => number;
  onConnection?: (event: ForwardProxyConnectionEvent) => void;
  onRequest?: (event: EgressRequestEvent) => void;
}

interface ConnectedAttempt {
  connectionAttemptId: string;
  startedAt: number;
  connection: UpstreamConnection;
}

const filteredHeaders = (
  headers: IncomingHttpHeaders,
  host: string,
): IncomingHttpHeaders => {
  const output: IncomingHttpHeaders = { host };
  const blocked = new Set(HOP_BY_HOP_HEADERS);
  const connectionValues = Array.isArray(headers.connection)
    ? headers.connection
    : [headers.connection];
  for (const value of connectionValues) {
    for (const token of String(value ?? "").split(",")) {
      if (token.trim()) blocked.add(token.trim().toLowerCase());
    }
  }
  for (const [name, value] of Object.entries(headers)) {
    const lower = name.toLowerCase();
    if (lower === "host" || blocked.has(lower)) continue;
    output[lower] = value;
  }
  return output;
};

const safeEqual = (actual: string, expected: string): boolean => {
  const actualBytes = Buffer.from(actual);
  const expectedBytes = Buffer.from(expected);
  return (
    actualBytes.length === expectedBytes.length &&
    timingSafeEqual(actualBytes, expectedBytes)
  );
};

const parseBasic = (header: string | undefined): [string, string] | null => {
  if (!header?.startsWith("Basic ")) return null;
  try {
    const decoded = Buffer.from(header.slice(6), "base64").toString();
    const separator = decoded.indexOf(":");
    return separator < 0
      ? null
      : [decoded.slice(0, separator), decoded.slice(separator + 1)];
  } catch {
    return null;
  }
};

const stableErrorCode = (error: unknown): string => {
  if (error instanceof EgressConnectError) return error.code;
  if (typeof error === "object" && error !== null && "code" in error) {
    return String(error.code).toLowerCase();
  }
  return "forward_proxy_failed";
};

/**
 * Authenticated loopback forward proxy for explicit model transports only.
 * Its URL is a runtime capability, never a process-wide proxy setting.
 */
export class ForwardProxy {
  private readonly resolver: Pick<OutboundResolver, "resolve"> &
    Partial<Pick<OutboundResolver, "invalidate">>;
  private readonly connector: UpstreamConnector;
  private readonly now: () => number;
  private readonly onConnection?: ForwardProxyOptions["onConnection"];
  private readonly onRequest?: ForwardProxyOptions["onRequest"];
  private readonly capabilities = new Map<string, StoredCapability>();
  private readonly acceptedSockets = new Set<Socket>();
  private readonly upstreamSockets = new Set<Socket>();
  private mode: "auto" | "direct";
  private server: Server | null = null;
  private port: number | null = null;

  constructor(options: ForwardProxyOptions) {
    this.resolver = options.resolver;
    this.connector = options.connector ?? new UpstreamConnector();
    this.mode = options.mode ?? "auto";
    this.now = options.now ?? Date.now;
    this.onConnection = options.onConnection;
    this.onRequest = options.onRequest;
  }

  async start(): Promise<void> {
    if (this.server) return;
    const server = createServer((request, response) => {
      void this.handleHttp(request, response);
    });
    server.on("connect", (request, socket, head) => {
      void this.handleConnect(request, socket, head);
    });
    server.on("connection", (socket) => {
      this.acceptedSockets.add(socket);
      socket.once("close", () => this.acceptedSockets.delete(socket));
    });
    await new Promise<void>((resolve, reject) => {
      const onError = (error: Error) => {
        server.off("listening", onListening);
        reject(error);
      };
      const onListening = () => {
        server.off("error", onError);
        resolve();
      };
      server.once("error", onError);
      server.once("listening", onListening);
      server.listen(0, "127.0.0.1");
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      server.close();
      throw new Error("forward_proxy_missing_loopback_address");
    }
    this.server = server;
    this.port = address.port;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    this.port = null;
    this.capabilities.clear();
    for (const socket of this.acceptedSockets) socket.destroy();
    this.acceptedSockets.clear();
    for (const socket of this.upstreamSockets) socket.destroy();
    this.upstreamSockets.clear();
    if (!server) return;
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(resolve, 250);
      timeout.unref();
      server.close(() => {
        clearTimeout(timeout);
        resolve();
      });
      server.closeAllConnections();
    });
  }

  getListeningPort(): number | null {
    return this.port;
  }

  setMode(mode: "auto" | "direct"): void {
    this.mode = mode;
  }

  register(input: ForwardProxyRegistration): ForwardProxyDescriptor {
    if (!this.server || this.port === null) {
      throw new Error("forward_proxy_not_started");
    }
    const username = randomBytes(18).toString("base64url");
    const secret = randomBytes(32).toString("base64url");
    const expiresAt = this.now() + Math.max(1, input.ttlMs ?? DEFAULT_FORWARD_PROXY_TTL_MS);
    this.capabilities.set(username, {
      username,
      secret,
      clientId: input.clientId,
      runtime: input.runtime,
      expiresAt,
    });
    return {
      kind: "forward_proxy",
      proxyUrl: `http://${username}:${secret}@127.0.0.1:${this.port}`,
      clientId: input.clientId,
      expiresAt,
    };
  }

  revoke(clientId: string): void {
    for (const [username, capability] of this.capabilities) {
      if (capability.clientId === clientId) this.capabilities.delete(username);
    }
  }

  revokeAll(): void {
    this.capabilities.clear();
  }

  renew(clientId: string, expiresAt: number): void {
    for (const capability of this.capabilities.values()) {
      if (capability.clientId === clientId) capability.expiresAt = expiresAt;
    }
  }

  private authenticate(request: IncomingMessage): StoredCapability | null {
    const credentials = parseBasic(request.headers["proxy-authorization"]);
    if (!credentials) return null;
    const capability = this.capabilities.get(credentials[0]);
    if (!capability || capability.expiresAt <= this.now()) {
      if (capability) this.capabilities.delete(capability.username);
      return null;
    }
    return safeEqual(credentials[1], capability.secret) ? capability : null;
  }

  private async connect(
    capability: StoredCapability,
    target: URL,
    tlsToTarget?: boolean,
  ): Promise<ConnectedAttempt> {
    const connectionAttemptId = randomUUID();
    const resolveStarted = this.now();
    try {
      const resolution = await this.resolver.resolve(target.href, this.mode);
      const resolutionMs = Math.max(0, this.now() - resolveStarted);
      const connection = await this.connector.connect(target, resolution, {
        tlsToTarget,
      });
      this.upstreamSockets.add(connection.socket);
      connection.socket.once("close", () =>
        this.upstreamSockets.delete(connection.socket),
      );
      this.onConnection?.({
        connectionAttemptId,
        startedAt: resolveStarted,
        registration: capability,
        targetOrigin: target.origin,
        resolutionMs,
        connection,
      });
      return { connectionAttemptId, startedAt: resolveStarted, connection };
    } catch (error) {
      // Re-evaluate PAC/system proxy state on the next attempt instead of
      // retaining a route that has just failed to connect.
      this.resolver.invalidate?.(target.origin);
      this.onConnection?.({
        connectionAttemptId,
        startedAt: resolveStarted,
        registration: capability,
        targetOrigin: target.origin,
        resolutionMs: Math.max(0, this.now() - resolveStarted),
        errorCode: stableErrorCode(error),
      });
      throw error;
    }
  }

  private deny(response: ServerResponse): void {
    response.writeHead(407, { "proxy-authenticate": 'Basic realm="Valuz Egress"' });
    response.end();
  }

  private async handleHttp(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    const capability = this.authenticate(request);
    if (!capability) {
      this.deny(response);
      return;
    }
    let target: URL;
    try {
      target = new URL(request.url ?? "");
      if (!["http:", "https:"].includes(target.protocol)) throw new Error("scheme");
    } catch {
      response.writeHead(400).end();
      return;
    }
    let downstreamCancelled = false;
    let activeRequest: ReturnType<typeof httpRequest> | undefined;
    const cancelDownstream = () => {
      downstreamCancelled = true;
      activeRequest?.destroy();
    };
    request.once("aborted", cancelDownstream);
    response.once("close", () => {
      if (!response.writableEnded) cancelDownstream();
    });
    try {
      const attempt = await this.connect(capability, target);
      const { connection } = attempt;
      let statusCode: number | undefined;
      let terminal = false;
      const emitRequest = (
        phase: EgressRequestEvent["phase"],
        errorCode?: string,
      ) => {
        this.onRequest?.({
          connectionAttemptId: attempt.connectionAttemptId,
          startedAt: attempt.startedAt,
          clientId: capability.clientId,
          runtime: capability.runtime,
          targetOrigin: target.origin,
          phase,
          elapsedMs: Math.max(0, this.now() - attempt.startedAt),
          connectMs: connection.connectMs,
          fallbackCount: connection.fallbackCount,
          statusCode,
          errorCode,
        });
      };
      const finish = (
        phase: Extract<
          EgressRequestEvent["phase"],
          "completed" | "aborted" | "failed" | "cancelled"
        >,
        errorCode?: string,
      ) => {
        if (terminal) return;
        terminal = true;
        emitRequest(phase, errorCode);
      };
      if (downstreamCancelled) {
        finish("cancelled", "downstream_cancelled");
        connection.socket.destroy();
        return;
      }
      const agent = createPreconnectedHttpAgent(connection.socket);
      const upstreamRequest = httpRequest({
        method: request.method,
        host: target.hostname,
        port: target.port || (target.protocol === "https:" ? 443 : 80),
        path: `${target.pathname}${target.search}`,
        headers: filteredHeaders(request.headers, target.host),
        agent,
      });
      activeRequest = upstreamRequest;
      upstreamRequest.once("response", (upstreamResponse) => {
        const headers = filteredHeaders(upstreamResponse.headers, "");
        delete headers.host;
        statusCode = upstreamResponse.statusCode ?? 502;
        emitRequest("headers_received");
        upstreamResponse.once("data", () => emitRequest("first_byte"));
        const abortResponse = (errorCode: string) => {
          finish("aborted", errorCode);
          agent.destroy();
          if (!response.headersSent) response.writeHead(502).end();
          else if (!response.destroyed) response.destroy();
        };
        upstreamResponse.once("aborted", () =>
          abortResponse("upstream_response_aborted"),
        );
        upstreamResponse.once("error", (error) =>
          abortResponse(stableErrorCode(error)),
        );
        response.writeHead(statusCode, headers);
        upstreamResponse.pipe(response);
        upstreamResponse.once("end", () => {
          finish("completed");
          agent.destroy();
        });
      });
      upstreamRequest.once("error", (error) => {
        finish(
          downstreamCancelled ? "cancelled" : "failed",
          downstreamCancelled ? "downstream_cancelled" : stableErrorCode(error),
        );
        agent.destroy();
        if (!downstreamCancelled && !response.destroyed) {
          if (!response.headersSent) response.writeHead(502);
          response.end();
        }
      });
      request.pipe(upstreamRequest);
    } catch {
      if (!downstreamCancelled && !response.destroyed) {
        if (!response.headersSent) response.writeHead(502);
        response.end();
      }
    }
  }

  private async handleConnect(
    request: IncomingMessage,
    clientSocket: Duplex,
    head: Buffer,
  ): Promise<void> {
    const capability = this.authenticate(request);
    if (!capability) {
      clientSocket.end(
        'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Valuz Egress"\r\nConnection: close\r\n\r\n',
      );
      return;
    }
    let target: URL;
    try {
      if (!request.url || !/^[^/\s]+:\d+$/.test(request.url)) throw new Error("authority");
      target = new URL(`https://${request.url}`);
    } catch {
      clientSocket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
      return;
    }
    let clientCancelled = false;
    clientSocket.once("close", () => {
      clientCancelled = true;
    });
    try {
      const attempt = await this.connect(capability, target, false);
      const { connection } = attempt;
      let terminal = false;
      let sawUpstreamData = false;
      const emitRequest = (
        phase: EgressRequestEvent["phase"],
        errorCode?: string,
      ) => {
        this.onRequest?.({
          connectionAttemptId: attempt.connectionAttemptId,
          startedAt: attempt.startedAt,
          clientId: capability.clientId,
          runtime: capability.runtime,
          targetOrigin: target.origin,
          phase,
          elapsedMs: Math.max(0, this.now() - attempt.startedAt),
          connectMs: connection.connectMs,
          fallbackCount: connection.fallbackCount,
          errorCode,
        });
      };
      const finish = (
        phase: Extract<
          EgressRequestEvent["phase"],
          "completed" | "aborted" | "failed" | "cancelled"
        >,
        errorCode?: string,
      ) => {
        if (terminal) return;
        terminal = true;
        emitRequest(phase, errorCode);
      };
      if (clientCancelled || clientSocket.destroyed) {
        finish("cancelled", "downstream_cancelled");
        connection.socket.destroy();
        return;
      }
      connection.socket.once("data", () => {
        sawUpstreamData = true;
        emitRequest("first_byte");
      });
      clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length > 0) connection.socket.write(head);
      clientSocket.pipe(connection.socket).pipe(clientSocket);
      const destroyBoth = () => {
        clientSocket.destroy();
        connection.socket.destroy();
      };
      clientSocket.once("error", (error) => {
        finish("cancelled", stableErrorCode(error));
        destroyBoth();
      });
      connection.socket.once("error", (error) => {
        finish("aborted", stableErrorCode(error));
        destroyBoth();
      });
      clientSocket.once("close", () => {
        finish("cancelled", "downstream_cancelled");
        connection.socket.destroy();
      });
      connection.socket.once("close", () => {
        finish(
          sawUpstreamData ? "completed" : "aborted",
          sawUpstreamData ? undefined : "upstream_closed_before_response",
        );
        clientSocket.destroy();
      });
    } catch {
      if (!clientSocket.destroyed) {
        clientSocket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
      }
    }
  }
}
