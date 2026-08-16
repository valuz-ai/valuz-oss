import { randomBytes, timingSafeEqual } from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { Socket } from "node:net";
import type {
  ForwardProxyDescriptor,
  ForwardProxyRegistration,
} from "./forward-proxy";
import type {
  ModelIngressDescriptor,
  ModelIngressRegistration,
} from "./model-ingress";

const MAX_CONTROL_BODY_BYTES = 32 * 1024;
export const DEFAULT_BOOTSTRAP_TTL_MS = 12 * 60 * 60 * 1000;

export interface EgressBootstrap {
  mode: "auto" | "direct";
  controlEndpoint: string;
  bootstrapToken: string;
  expiresAt: number;
}

export interface RuntimePhasePayload {
  turnAttemptId: string;
  clientId: string;
  phase:
    | "runtime_init_started"
    | "runtime_init"
    | "thread_init_started"
    | "thread_init"
    | "dispatch_started"
    | "dispatch"
    | "model_first_event"
    | "runtime_ready"
    | "runtime_prepare_failed"
    | "turn_complete"
    | "interrupted";
  monotonicMs: number;
}

/**
 * Electron stamps control-plane receipt time because Python's monotonic clock
 * and Node's performance clock do not share an origin. `observedAt` is the
 * cross-process timeline value; `monotonicMs` remains useful only for ordering
 * phases emitted by the same backend process.
 */
export interface RuntimePhaseRecord extends RuntimePhasePayload {
  observedAt: number;
  runtime?: string;
  targetOrigin?: string;
}

export interface EgressControlServerOptions {
  mode: "auto" | "direct";
  registerModelIngress: (
    registration: ModelIngressRegistration,
  ) => ModelIngressDescriptor;
  registerForwardProxy: (
    registration: ForwardProxyRegistration,
  ) => ForwardProxyDescriptor;
  revokeClient: (clientId: string) => void;
  renewClients: (clientIds: string[], expiresAt: number) => void;
  recordRuntimePhase?: (payload: RuntimePhasePayload) => void;
  now?: () => number;
  ttlMs?: number;
}

const safeEqual = (actual: string, expected: string): boolean => {
  const left = Buffer.from(actual);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
};

const sendJson = (
  response: ServerResponse,
  status: number,
  payload: unknown,
): void => {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": body.length,
    "cache-control": "no-store",
  });
  response.end(body);
};

const readJson = async (request: IncomingMessage): Promise<unknown> => {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.from(chunk);
    size += bytes.length;
    if (size > MAX_CONTROL_BODY_BYTES) throw new Error("control_body_too_large");
    chunks.push(bytes);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const validClientId = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(value);

/** Loopback control plane; secrets are accepted only in Authorization. */
export class EgressControlServer {
  private readonly options: EgressControlServerOptions;
  private readonly now: () => number;
  private token = "";
  private expiresAt = 0;
  private readonly ttlMs: number;
  private mode: "auto" | "direct";
  private readonly sockets = new Set<Socket>();
  private server: Server | null = null;
  private port: number | null = null;

  constructor(options: EgressControlServerOptions) {
    this.options = options;
    this.now = options.now ?? Date.now;
    this.ttlMs = options.ttlMs ?? DEFAULT_BOOTSTRAP_TTL_MS;
    this.mode = options.mode;
  }

  async start(): Promise<void> {
    if (this.server) return;
    this.token = randomBytes(32).toString("base64url");
    this.expiresAt = this.now() + this.ttlMs;
    const server = createServer((request, response) => {
      void this.handle(request, response);
    });
    server.on("connection", (socket) => {
      this.sockets.add(socket);
      socket.once("close", () => this.sockets.delete(socket));
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      server.close();
      throw new Error("egress_control_missing_loopback_address");
    }
    this.server = server;
    this.port = address.port;
  }

  bootstrap(): EgressBootstrap {
    if (!this.server || this.port === null) {
      throw new Error("egress_control_not_started");
    }
    return {
      mode: this.mode,
      controlEndpoint: `http://127.0.0.1:${this.port}`,
      bootstrapToken: this.token,
      expiresAt: this.expiresAt,
    };
  }

  getListeningPort(): number | null {
    return this.port;
  }

  setMode(mode: "auto" | "direct"): void {
    this.mode = mode;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    this.port = null;
    for (const socket of this.sockets) socket.destroy();
    this.sockets.clear();
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

  private authorized(request: IncomingMessage): boolean {
    const header = request.headers.authorization;
    return (
      this.now() < this.expiresAt &&
      typeof header === "string" &&
      header.startsWith("Bearer ") &&
      safeEqual(header.slice(7), this.token)
    );
  }

  private async handle(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    if (!this.authorized(request)) {
      sendJson(response, 401, { error: "unauthorized" });
      return;
    }
    try {
      if (request.method === "POST" && request.url === "/v1/clients/model-ingress") {
        const body = await readJson(request);
        if (
          !isRecord(body) ||
          !validClientId(body.clientId) ||
          (body.runtime !== "codex" && body.runtime !== "claude") ||
          typeof body.upstreamBaseUrl !== "string" ||
          typeof body.supportsWebSocket !== "boolean"
        ) {
          sendJson(response, 400, { error: "invalid_registration" });
          return;
        }
        sendJson(response, 201, this.options.registerModelIngress({
          clientId: body.clientId,
          runtime: body.runtime,
          upstreamBaseUrl: body.upstreamBaseUrl,
          supportsWebSocket: body.supportsWebSocket,
        }));
        return;
      }
      if (request.method === "POST" && request.url === "/v1/clients/forward-proxy") {
        const body = await readJson(request);
        if (
          !isRecord(body) ||
          !validClientId(body.clientId) ||
          (body.runtime !== "deepagents" && body.runtime !== "provider_test")
        ) {
          sendJson(response, 400, { error: "invalid_registration" });
          return;
        }
        sendJson(response, 201, this.options.registerForwardProxy({
          clientId: body.clientId,
          runtime: body.runtime,
        }));
        return;
      }
      if (request.method === "POST" && request.url === "/v1/runtime-phase") {
        const body = await readJson(request);
        const phases = new Set<RuntimePhasePayload["phase"]>([
          "runtime_init_started",
          "runtime_init",
          "thread_init_started",
          "thread_init",
          "dispatch_started",
          "dispatch",
          "model_first_event",
          "runtime_ready",
          "runtime_prepare_failed",
          "turn_complete",
          "interrupted",
        ]);
        if (
          !isRecord(body) ||
          !validClientId(body.clientId) ||
          typeof body.turnAttemptId !== "string" ||
          !/^[A-Za-z0-9_-]{16,128}$/.test(body.turnAttemptId) ||
          typeof body.phase !== "string" ||
          !phases.has(body.phase as RuntimePhasePayload["phase"]) ||
          typeof body.monotonicMs !== "number" ||
          !Number.isFinite(body.monotonicMs)
        ) {
          sendJson(response, 400, { error: "invalid_runtime_phase" });
          return;
        }
        this.options.recordRuntimePhase?.(body as unknown as RuntimePhasePayload);
        sendJson(response, 202, { accepted: true });
        return;
      }
      if (request.method === "POST" && request.url === "/v1/lease/renew") {
        const body = await readJson(request);
        if (
          !isRecord(body) ||
          !Array.isArray(body.clientIds) ||
          body.clientIds.length > 1_000 ||
          !body.clientIds.every(validClientId)
        ) {
          sendJson(response, 400, { error: "invalid_lease_renewal" });
          return;
        }
        this.expiresAt = this.now() + this.ttlMs;
        this.options.renewClients([...new Set(body.clientIds)], this.expiresAt);
        sendJson(response, 200, { expiresAt: this.expiresAt });
        return;
      }
      if (request.method === "DELETE" && request.url?.startsWith("/v1/clients/")) {
        const clientId = decodeURIComponent(request.url.slice("/v1/clients/".length));
        if (!validClientId(clientId)) {
          sendJson(response, 400, { error: "invalid_client_id" });
          return;
        }
        this.options.revokeClient(clientId);
        sendJson(response, 200, { revoked: true });
        return;
      }
      sendJson(response, 404, { error: "not_found" });
    } catch (error) {
      const code = error instanceof Error ? error.message : "control_request_failed";
      const allowed = new Set([
        "control_body_too_large",
        "invalid_model_ingress_upstream",
        "model_ingress_not_started",
        "forward_proxy_not_started",
      ]);
      sendJson(response, 400, { error: allowed.has(code) ? code : "control_request_failed" });
    }
  }
}
