import { randomBytes, randomUUID } from "node:crypto";
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

export const DEFAULT_INGRESS_REGISTRATION_TTL_MS = 12 * 60 * 60 * 1000;
const CLIENT_PREFIX = "/_valuz/egress/";

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

export interface ModelIngressRegistration {
  clientId: string;
  runtime: Extract<EgressRuntime, "codex" | "claude">;
  upstreamBaseUrl: string;
  supportsWebSocket: boolean;
  ttlMs?: number;
}

export interface ModelIngressDescriptor {
  kind: "model_ingress";
  baseUrl: string;
  clientId: string;
  expiresAt: number;
  supportsWebSocket: boolean;
}

interface StoredRegistration {
  token: string;
  clientId: string;
  runtime: Extract<EgressRuntime, "codex" | "claude">;
  upstream: URL;
  supportsWebSocket: boolean;
  expiresAt: number;
}

export interface ModelIngressConnectionEvent {
  connectionAttemptId: string;
  startedAt: number;
  registration: Pick<StoredRegistration, "clientId" | "runtime">;
  targetOrigin: string;
  resolutionMs: number;
  connection?: Pick<
    UpstreamConnection,
    "route" | "candidateIndex" | "fallbackCount" | "connectMs"
  >;
  errorCode?: string;
}

export interface ModelIngressOptions {
  resolver: Pick<OutboundResolver, "resolve"> &
    Partial<Pick<OutboundResolver, "invalidate">>;
  connector?: UpstreamConnector;
  mode?: "auto" | "direct";
  now?: () => number;
  onConnection?: (event: ModelIngressConnectionEvent) => void;
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
  upgrade = false,
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
  if (upgrade) {
    output.connection = "Upgrade";
    output.upgrade = headers.upgrade;
  }
  return output;
};

const stableErrorCode = (error: unknown): string => {
  if (error instanceof EgressConnectError) return error.code;
  if (typeof error === "object" && error !== null && "code" in error) {
    return String(error.code).toLowerCase();
  }
  return "model_ingress_failed";
};

const pathWithinBase = (pathname: string, basePathname: string): boolean => {
  const base = basePathname.replace(/\/+$/, "") || "/";
  return base === "/" || pathname === base || pathname.startsWith(`${base}/`);
};

/**
 * Loopback-only, body-opaque model relay. A random path capability selects a
 * control-channel registration; requests cannot provide or override the real
 * upstream origin. The relay does not follow redirects or retry requests.
 */
export class ModelIngress {
  private readonly resolver: Pick<OutboundResolver, "resolve"> &
    Partial<Pick<OutboundResolver, "invalidate">>;
  private readonly connector: UpstreamConnector;
  private readonly now: () => number;
  private readonly onConnection?: ModelIngressOptions["onConnection"];
  private readonly onRequest?: ModelIngressOptions["onRequest"];
  private mode: "auto" | "direct";
  private readonly registrations = new Map<string, StoredRegistration>();
  private readonly acceptedSockets = new Set<Socket>();
  private readonly upstreamSockets = new Set<Socket>();
  private server: Server | null = null;
  private port: number | null = null;

  constructor(options: ModelIngressOptions) {
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
    server.on("upgrade", (request, socket, head) => {
      void this.handleUpgrade(request, socket, head);
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
      throw new Error("model_ingress_missing_loopback_address");
    }
    this.server = server;
    this.port = address.port;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    this.port = null;
    this.registrations.clear();
    if (!server) return;
    for (const socket of this.acceptedSockets) socket.destroy();
    this.acceptedSockets.clear();
    for (const socket of this.upstreamSockets) socket.destroy();
    this.upstreamSockets.clear();
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

  register(input: ModelIngressRegistration): ModelIngressDescriptor {
    if (!this.server || this.port === null) {
      throw new Error("model_ingress_not_started");
    }
    const upstream = new URL(input.upstreamBaseUrl);
    if (
      !["http:", "https:"].includes(upstream.protocol) ||
      upstream.username ||
      upstream.password ||
      upstream.search ||
      upstream.hash
    ) {
      throw new Error("invalid_model_ingress_upstream");
    }
    if (this.connector.isProtectedTarget(upstream)) {
      throw new Error("model_ingress_proxy_loop_detected");
    }
    const token = randomBytes(32).toString("base64url");
    const expiresAt = this.now() + Math.max(1, input.ttlMs ?? DEFAULT_INGRESS_REGISTRATION_TTL_MS);
    const registration: StoredRegistration = {
      token,
      clientId: input.clientId,
      runtime: input.runtime,
      upstream,
      supportsWebSocket: input.supportsWebSocket,
      expiresAt,
    };
    this.registrations.set(token, registration);
    const basePath = upstream.pathname === "/" ? "" : upstream.pathname.replace(/\/+$/, "");
    return {
      kind: "model_ingress",
      baseUrl: `http://127.0.0.1:${this.port}${CLIENT_PREFIX}${token}${basePath}`,
      clientId: input.clientId,
      expiresAt,
      supportsWebSocket: input.supportsWebSocket,
    };
  }

  revoke(clientId: string): void {
    for (const [token, registration] of this.registrations) {
      if (registration.clientId === clientId) this.registrations.delete(token);
    }
  }

  revokeAll(): void {
    this.registrations.clear();
  }

  renew(clientId: string, expiresAt: number): void {
    for (const registration of this.registrations.values()) {
      if (registration.clientId === clientId) registration.expiresAt = expiresAt;
    }
  }

  private registrationForRequest(rawUrl: string | undefined): {
    registration: StoredRegistration;
    target: URL;
  } | null {
    if (!rawUrl || /^https?:\/\//i.test(rawUrl)) return null;
    const match = new RegExp(`^${CLIENT_PREFIX}([A-Za-z0-9_-]{43})(/.*)?$`).exec(rawUrl);
    if (!match) return null;
    const registration = this.registrations.get(match[1]);
    if (!registration || registration.expiresAt <= this.now()) {
      if (registration) this.registrations.delete(registration.token);
      return null;
    }
    const suffix = match[2] || "/";
    const target = new URL(suffix, registration.upstream.origin);
    if (
      target.origin !== registration.upstream.origin ||
      !pathWithinBase(target.pathname, registration.upstream.pathname)
    ) {
      return null;
    }
    return { registration, target };
  }

  private async connect(
    registration: StoredRegistration,
    target: URL,
  ): Promise<ConnectedAttempt> {
    const connectionAttemptId = randomUUID();
    const resolveStarted = this.now();
    try {
      const resolution = await this.resolver.resolve(target.href, this.mode);
      const resolutionMs = Math.max(0, this.now() - resolveStarted);
      const connection = await this.connector.connect(target, resolution);
      this.upstreamSockets.add(connection.socket);
      connection.socket.once("close", () =>
        this.upstreamSockets.delete(connection.socket),
      );
      this.onConnection?.({
        connectionAttemptId,
        startedAt: resolveStarted,
        registration,
        targetOrigin: target.origin,
        resolutionMs,
        connection,
      });
      return { connectionAttemptId, startedAt: resolveStarted, connection };
    } catch (error) {
      // A failed proxy candidate often means the system proxy/PAC selection
      // just changed. Do not pin that stale resolution for the full cache TTL.
      this.resolver.invalidate?.(target.origin);
      this.onConnection?.({
        connectionAttemptId,
        startedAt: resolveStarted,
        registration,
        targetOrigin: target.origin,
        resolutionMs: Math.max(0, this.now() - resolveStarted),
        errorCode: stableErrorCode(error),
      });
      throw error;
    }
  }

  private async handleHttp(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    const resolved = this.registrationForRequest(request.url);
    if (!resolved) {
      response.writeHead(404).end();
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
      const attempt = await this.connect(resolved.registration, resolved.target);
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
          clientId: resolved.registration.clientId,
          runtime: resolved.registration.runtime,
          targetOrigin: resolved.target.origin,
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
      const headers = filteredHeaders(request.headers, resolved.target.host);
      const agent = createPreconnectedHttpAgent(connection.socket);
      const upstreamRequest = httpRequest({
        method: request.method,
        host: resolved.target.hostname,
        port:
          resolved.target.port ||
          (resolved.target.protocol === "https:" ? 443 : 80),
        path: `${resolved.target.pathname}${resolved.target.search}`,
        headers,
        agent,
      });
      activeRequest = upstreamRequest;
      upstreamRequest.once("response", (upstreamResponse) => {
        const responseHeaders = filteredHeaders(upstreamResponse.headers, "");
        delete responseHeaders.host;
        statusCode = upstreamResponse.statusCode ?? 502;
        const location = upstreamResponse.headers.location;
        if (
          location &&
          (upstreamResponse.statusCode ?? 0) >= 300 &&
          (upstreamResponse.statusCode ?? 0) < 400
        ) {
          let redirect: URL;
          try {
            redirect = new URL(location, resolved.target);
          } catch {
            finish("failed", "model_ingress_invalid_redirect");
            upstreamResponse.destroy();
            response.writeHead(502).end();
            agent.destroy();
            return;
          }
          if (
            redirect.origin !== resolved.registration.upstream.origin ||
            !pathWithinBase(
              redirect.pathname,
              resolved.registration.upstream.pathname,
            )
          ) {
            finish("failed", "model_ingress_cross_origin_redirect");
            upstreamResponse.destroy();
            response.writeHead(502).end();
            agent.destroy();
            return;
          }
          responseHeaders.location = `${CLIENT_PREFIX}${resolved.registration.token}${redirect.pathname}${redirect.search}${redirect.hash}`;
        }
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
        response.writeHead(statusCode, responseHeaders);
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

  private async handleUpgrade(
    request: IncomingMessage,
    clientSocket: Duplex,
    head: Buffer,
  ): Promise<void> {
    const resolved = this.registrationForRequest(request.url);
    if (!resolved || !resolved.registration.supportsWebSocket) {
      clientSocket.end("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
      return;
    }
    let clientCancelled = false;
    clientSocket.once("close", () => {
      clientCancelled = true;
    });
    try {
      const attempt = await this.connect(resolved.registration, resolved.target);
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
          clientId: resolved.registration.clientId,
          runtime: resolved.registration.runtime,
          targetOrigin: resolved.target.origin,
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
      const headers = filteredHeaders(request.headers, resolved.target.host, true);
      const lines = [
        `${request.method ?? "GET"} ${resolved.target.pathname}${resolved.target.search} HTTP/${request.httpVersion}`,
        ...Object.entries(headers).flatMap(([name, value]) => {
          if (value === undefined) return [];
          return Array.isArray(value)
            ? value.map((item) => `${name}: ${item}`)
            : [`${name}: ${value}`];
        }),
        "",
        "",
      ];
      connection.socket.once("data", () => {
        sawUpstreamData = true;
        emitRequest("first_byte");
      });
      connection.socket.write(lines.join("\r\n"));
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
