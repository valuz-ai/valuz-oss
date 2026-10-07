import type { HostRequest, HostServices } from "./services";
import type {
  ArtifactContent,
  AutomationRun,
  ConnectorInfo,
  ConnectorTool,
  ConversationEvent,
  KnowledgeDocument,
  KnowledgeHit,
  ProjectDetail,
  ProjectFileNode,
  PublicArtifact,
  PublicProject,
  StorageItem,
  ToolCallResult,
  ValuzApiErrorShape,
  ValuzClient,
} from "./types";

const TERMINAL_RUN_STATUSES = new Set([
  "success",
  "failed",
  "timeout",
  "cancelled",
  "skipped",
  "interrupted_by_shutdown",
]);

/** Every failure of a ``ctx.valuz`` call: HTTP, network, or declined by the user. */
export class ValuzApiError extends Error implements ValuzApiErrorShape {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ValuzApiError";
    this.status = status;
    this.code = code;
  }
}

type Json = Record<string, unknown>;

const asRecord = (value: unknown): Json =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Json)
    : {};
const asString = (value: unknown): string | null =>
  typeof value === "string" ? value : null;
const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const asArray = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];

/**
 * Normalise whatever the host's request layer threw into a ``ValuzApiError``.
 * Reads ``status`` / ``body`` of the host's ``ApiError`` without importing it.
 */
export function toValuzApiError(error: unknown): ValuzApiError {
  if (error instanceof ValuzApiError) return error;
  const source = asRecord(error);
  const status = typeof source.status === "number" ? source.status : 0;
  let code = status === 0 ? "network_error" : `http_${status}`;
  let message =
    error instanceof Error ? error.message : String(error ?? "request failed");

  const body = asString(source.body);
  if (body) {
    try {
      const parsed = asRecord(JSON.parse(body));
      const detail = parsed.detail ?? parsed;
      if (typeof detail === "string") {
        message = detail;
      } else {
        const d = asRecord(detail);
        const named = d.code ?? d.error ?? d.error_code ?? d.message_key;
        if (typeof named === "string") code = named;
        const text = d.message ?? d.reason;
        if (typeof text === "string") message = text;
      }
    } catch {
      // not JSON: keep the host's message
    }
  }
  return new ValuzApiError(status, code, message);
}

function toProject(raw: unknown): PublicProject {
  const p = asRecord(raw);
  return {
    id: String(p.id ?? ""),
    name: String(p.name ?? ""),
    kind: asString(p.kind),
    rootPath: asString(p.root_path ?? p.rootPath),
    icon: asString(p.icon),
  };
}

function toRun(raw: unknown): AutomationRun {
  const r = asRecord(raw);
  const status = String(r.status ?? "");
  return {
    runId: String(r.run_id ?? r.id ?? ""),
    automationId: asString(r.automation_id),
    status,
    done: TERMINAL_RUN_STATUSES.has(status),
    startedAt: asNumber(r.started_at),
    completedAt: asNumber(r.completed_at),
    summary: asString(r.result_summary),
    errorMessage: asString(r.error_message),
    sessionId: asString(r.session_id),
    output: r.artifact ?? null,
    files: asArray(r.files),
    raw: r,
  };
}

function toArtifact(raw: unknown, sessionId: string | null): PublicArtifact {
  const a = asRecord(raw);
  const current = asRecord(a.current);
  // ``GET /v1/artifacts`` rows are deliverables (``current`` is the version);
  // ``GET /v1/sessions/{id}/artifacts`` rows are versions (``artifact_id``).
  const isVersionRow = typeof a.artifact_id === "string";
  return {
    id: String(isVersionRow ? a.artifact_id : (a.id ?? "")),
    revisionId: String(isVersionRow ? a.id : (current.id ?? "")),
    name: String(a.display_name ?? a.file_name ?? current.file_name ?? ""),
    kind: asString(a.kind),
    versionNo: asNumber(a.version_no),
    fileName: asString(a.file_name ?? current.file_name),
    mimeType: asString(a.mime_type ?? current.mime_type),
    updatedAt: asNumber(a.updated_at ?? a.created_at ?? current.created_at),
    sessionId: asString(a.session_id) ?? sessionId,
  };
}

export interface ValuzClientOptions {
  pluginId: string;
  services: Pick<
    HostServices,
    "request" | "confirm" | "draftConversation"
  >;
}

/** ``valuz.service.fetch``: B-level plugin services are not part of plugin API 1.x. */
const serviceNotSupported = (): Promise<never> =>
  Promise.reject(
    new ValuzApiError(
      501,
      "not_supported",
      "valuz.service.fetch is not supported in plugin API 1.x",
    ),
  );

const invalid = (message: string): ValuzApiError =>
  new ValuzApiError(400, "invalid_argument", message);

/**
 * ``ctx.valuz`` (doc 12 §5). Every call goes through the host's authenticated
 * request layer with ``X-Valuz-App-Plugin-Id`` set, so the backend can check the
 * plugin's declared permissions; failures are ``ValuzApiError``.
 */
export function createValuzClient(options: ValuzClientOptions): ValuzClient {
  const { pluginId, services } = options;
  const appPluginBase = `/v1/app-plugins/${encodeURIComponent(pluginId)}`;

  const call = async (request: HostRequest): Promise<unknown> => {
    try {
      return await services.request({
        ...request,
        headers: { ...request.headers, "X-Valuz-App-Plugin-Id": pluginId },
      });
    } catch (error) {
      throw toValuzApiError(error);
    }
  };
  const seg = encodeURIComponent;

  const getRun = async (runId: string): Promise<AutomationRun> =>
    toRun(
      await call({
        method: "GET",
        path: `${appPluginBase}/automation-runs/${seg(runId)}`,
      }),
    );

  return {
    projects: {
      async list() {
        const body = asRecord(await call({ method: "GET", path: "/v1/projects" }));
        return asArray(body.projects).map(toProject);
      },
      async get(id) {
        const body = await call({ method: "GET", path: `/v1/projects/${seg(id)}` });
        return {
          ...toProject(body),
          instructions: asString(asRecord(body).instructions_md),
        } satisfies ProjectDetail;
      },
      async files(id, opts) {
        const body = asRecord(
          await call({
            method: "GET",
            path: `/v1/projects/${seg(id)}/files`,
            query: { path: opts?.path, depth: opts?.depth },
          }),
        );
        return asArray(body.files) as ProjectFileNode[];
      },
    },

    artifacts: {
      async list(scope) {
        if (scope.sessionId) {
          const body = asRecord(
            await call({
              method: "GET",
              path: `/v1/sessions/${seg(scope.sessionId)}/artifacts`,
            }),
          );
          return asArray(body.items).map((item) =>
            toArtifact(item, scope.sessionId ?? null),
          );
        }
        if (scope.projectId) {
          const body = asRecord(
            await call({
              method: "GET",
              path: "/v1/artifacts",
              query: { project_id: scope.projectId },
            }),
          );
          return asArray(body.items).map((item) => toArtifact(item, null));
        }
        throw invalid("artifacts.list needs a projectId or a sessionId");
      },
      async content(revisionId) {
        const body = asRecord(
          await call({
            method: "GET",
            path: `/v1/artifacts/revisions/${seg(revisionId)}/content`,
          }),
        );
        return {
          revisionId: String(body.revision_id ?? revisionId),
          versionNo: asNumber(body.version_no),
          content: asString(body.content),
        } satisfies ArtifactContent;
      },
    },

    knowledge: {
      async search(query, opts) {
        const body = asRecord(
          await call({
            method: "POST",
            path: "/v1/docs/search",
            json: {
              query,
              project_id: opts?.projectId ?? "",
              top_k: opts?.topK ?? 5,
              ...(opts?.kbIds ? { folder_ids: opts.kbIds } : {}),
            },
          }),
        );
        return asArray(body.hits).map((raw): KnowledgeHit => {
          const h = asRecord(raw);
          return {
            documentId: String(h.document_id ?? ""),
            filename: String(h.filename ?? ""),
            score: asNumber(h.score) ?? 0,
            snippet: String(h.snippet ?? ""),
            pageRef: asString(h.page_ref),
          };
        });
      },
      async get(docId) {
        const body = asRecord(
          await call({ method: "GET", path: `/v1/docs/${seg(docId)}` }),
        );
        return {
          ...body,
          id: String(body.id ?? docId),
          filename: String(body.filename ?? ""),
        } as KnowledgeDocument;
      },
    },

    conversations: {
      async draft(opts) {
        try {
          await services.draftConversation(opts);
        } catch (error) {
          throw toValuzApiError(error);
        }
      },
      async send(opts) {
        let sessionId = opts.sessionId;
        if (!sessionId) {
          if (!opts.projectId) {
            throw invalid("conversations.send needs a sessionId or a projectId");
          }
          const created = asRecord(
            await call({
              method: "POST",
              path: "/v1/sessions",
              json: {
                project_id: opts.projectId,
                ...(opts.agent ? { agent_slug: opts.agent } : {}),
              },
            }),
          );
          sessionId = String(created.id ?? "");
        }
        await call({
          method: "POST",
          path: `/v1/sessions/${seg(sessionId)}/messages`,
          json: { prompt: opts.text },
        });
        return { sessionId };
      },
      async events(sessionId, opts) {
        const body = asRecord(
          await call({
            method: "GET",
            path: `/v1/sessions/${seg(sessionId)}/events`,
            query: { after_seq: opts?.afterSeq },
          }),
        );
        return asArray(body.items).map((raw): ConversationEvent => {
          const e = asRecord(raw);
          return {
            seq: asNumber(e.seq) ?? 0,
            timestamp: asNumber(e.timestamp),
            event: asRecord(e.event),
          };
        });
      },
    },

    connectors: {
      async list() {
        const body = asRecord(await call({ method: "GET", path: "/v1/connectors" }));
        return asArray(body.connectors).map((raw): ConnectorInfo => {
          const c = asRecord(raw);
          return {
            id: String(c.id ?? ""),
            slug: String(c.slug ?? ""),
            name: String(c.display_name ?? c.slug ?? ""),
            description: asString(c.description),
            enabled: c.enabled === true,
            status: String(c.status ?? ""),
            toolCount: asNumber(c.tool_count),
          };
        });
      },
      async listTools(connector) {
        const body = asRecord(
          await call({
            method: "GET",
            path: `/v1/connectors/${seg(connector)}/tools`,
          }),
        );
        return asArray(body.tools).map((raw): ConnectorTool => {
          const t = asRecord(raw);
          return {
            name: String(t.name ?? ""),
            description: asString(t.description),
            inputSchema:
              t.input_schema && typeof t.input_schema === "object"
                ? (t.input_schema as Json)
                : null,
            readOnly: t.read_only === true,
          };
        });
      },
      async callTool(connector, tool, args, opts) {
        const write = opts?.write === true;
        if (write) {
          const approved = await services.confirm({
            title: `${pluginId} → ${connector} / ${tool}`,
            body: JSON.stringify(args ?? {}).slice(0, 600),
          });
          if (!approved) {
            throw new ValuzApiError(
              403,
              "user_declined",
              "The user declined the write operation",
            );
          }
        }
        const body = asRecord(
          await call({
            method: "POST",
            path: `/v1/connectors/${seg(connector)}/tools/${seg(tool)}/call`,
            json: { arguments: args ?? {}, allow_write: write },
          }),
        );
        return {
          content: asArray(body.content),
          structuredContent: body.structured_content ?? null,
          isError: body.is_error === true,
        } satisfies ToolCallResult;
      },
    },

    automations: {
      async run(name, input) {
        const body = asRecord(
          await call({
            method: "POST",
            path: `${appPluginBase}/automations/${seg(name)}/run`,
            json: input === undefined ? {} : { input },
          }),
        );
        return {
          runId: String(body.run_id ?? ""),
          automationId: asString(body.automation_id),
        };
      },
      getRun,
      async waitRun(runId, opts) {
        const deadline = Date.now() + (opts?.timeoutMs ?? 60_000);
        const interval = opts?.intervalMs ?? 1000;
        for (;;) {
          const run = await getRun(runId);
          if (run.done) return run;
          if (Date.now() + interval > deadline) {
            throw new ValuzApiError(
              408,
              "run_timeout",
              `Run ${runId} did not finish in time`,
            );
          }
          await new Promise<void>((resolve) => setTimeout(resolve, interval));
        }
      },
      async latestRun(name) {
        try {
          return toRun(
            await call({
              method: "GET",
              path: `${appPluginBase}/automations/${seg(name)}/runs/latest`,
            }),
          );
        } catch (error) {
          if (error instanceof ValuzApiError && error.status === 404) return null;
          throw error;
        }
      },
    },

    storage: {
      async get<T = unknown>(key: string): Promise<T | null> {
        try {
          const body = asRecord(
            await call({ method: "GET", path: `${appPluginBase}/storage/${seg(key)}` }),
          );
          return (body.value ?? null) as T | null;
        } catch (error) {
          if (error instanceof ValuzApiError && error.status === 404) return null;
          throw error;
        }
      },
      async set(key, value) {
        await call({
          method: "PUT",
          path: `${appPluginBase}/storage/${seg(key)}`,
          json: { value },
        });
      },
      async delete(key) {
        await call({ method: "DELETE", path: `${appPluginBase}/storage/${seg(key)}` });
      },
      async list(prefix) {
        const body = asRecord(
          await call({
            method: "GET",
            path: `${appPluginBase}/storage`,
            query: { prefix: prefix ?? "" },
          }),
        );
        return asArray(body.items).map((raw): StorageItem => {
          const i = asRecord(raw);
          return {
            key: String(i.key ?? ""),
            size: asNumber(i.size) ?? 0,
            updatedAt: asNumber(i.updated_at),
          };
        });
      },
    },

    notifications: {
      async post(notification) {
        const body = asRecord(
          await call({
            method: "POST",
            path: "/v1/notifications",
            json: notification,
          }),
        );
        return { id: String(body.id ?? "") };
      },
    },

    service: { fetch: serviceNotSupported },
  };
}
