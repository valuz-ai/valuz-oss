import { ValuzApiError } from "../valuz-client";
import type {
  AutomationRun,
  ConnectorInfo,
  ConversationEvent,
  DraftConversationOptions,
  KnowledgeDocument,
  KnowledgeHit,
  ProjectDetail,
  ProjectFileNode,
  PublicArtifact,
  PublicProject,
  StorageItem,
  ValuzClient,
} from "../types";

export interface MockValuzSpec {
  projects?: Array<PublicProject & { instructions?: string | null }>;
  projectFiles?: Record<string, ProjectFileNode[]>;
  artifacts?: Array<
    PublicArtifact & { projectId?: string; content?: string | null }
  >;
  /** Documents the knowledge base holds. */
  knowledge?: Array<{ id: string; filename: string; text?: string }>;
  conversationEvents?: Record<string, ConversationEvent[]>;
  /** ``{ connectorSlug: { toolName: (args) => result } }``; results are the structured output. */
  connectors?: Record<
    string,
    Record<string, (args: Record<string, unknown>) => unknown>
  >;
  /** Names of connector tools that change something outside Valuz. */
  writeTools?: string[];
  /** ``{ name: (input) => ({ status, output }) }`` */
  automations?: Record<
    string,
    (input: unknown) => { status?: string; output?: unknown; summary?: string }
  >;
  storage?: Record<string, unknown>;
}

export interface MockValuzCall {
  method: string;
  args: unknown[];
}

export type MockValuz = ValuzClient & {
  /** Every call, in order: ``{ method: "connectors.callTool", args: [...] }``. */
  readonly calls: MockValuzCall[];
  readonly storageData: Map<string, unknown>;
  readonly drafts: DraftConversationOptions[];
  readonly sent: Array<{ sessionId: string; text: string }>;
  readonly notifications: Array<{ title: string; body?: string; link?: string }>;
};

/**
 * An in-memory ``ctx.valuz`` for plugin tests: connectors and automations call
 * the functions you give, storage keeps its values, drafts / sent messages /
 * notifications are recorded.
 */
const serviceNotSupported = (): Promise<never> =>
  Promise.reject(
    new ValuzApiError(
      501,
      "not_supported",
      "valuz.service.fetch is not supported in plugin API 1.x",
    ),
  );

export function mockValuz(spec: MockValuzSpec = {}): MockValuz {
  const calls: MockValuzCall[] = [];
  const storage = new Map<string, unknown>(Object.entries(spec.storage ?? {}));
  const drafts: DraftConversationOptions[] = [];
  const sent: Array<{ sessionId: string; text: string }> = [];
  const notifications: Array<{ title: string; body?: string; link?: string }> = [];
  const runs = new Map<string, AutomationRun>();
  const latest = new Map<string, AutomationRun>();
  let counter = 0;
  const record = (method: string, ...args: unknown[]): void => {
    calls.push({ method, args });
  };
  const notFound = (what: string): ValuzApiError =>
    new ValuzApiError(404, "not_found", `${what} not found`);
  const writeTools = new Set(spec.writeTools ?? []);

  const client: ValuzClient = {
    projects: {
      async list() {
        record("projects.list");
        return (spec.projects ?? []).map(({ instructions: _i, ...p }) => p);
      },
      async get(id) {
        record("projects.get", id);
        const found = (spec.projects ?? []).find((p) => p.id === id);
        if (!found) throw notFound(`project ${id}`);
        return { ...found, instructions: found.instructions ?? null } as ProjectDetail;
      },
      async files(id, options) {
        record("projects.files", id, options);
        return spec.projectFiles?.[id] ?? [];
      },
    },
    artifacts: {
      async list(scope) {
        record("artifacts.list", scope);
        return (spec.artifacts ?? [])
          .filter(
            (a) =>
              (!scope.projectId || a.projectId === scope.projectId) &&
              (!scope.sessionId || a.sessionId === scope.sessionId),
          )
          .map(({ content: _c, projectId: _p, ...rest }) => rest);
      },
      async content(revisionId) {
        record("artifacts.content", revisionId);
        const found = (spec.artifacts ?? []).find((a) => a.revisionId === revisionId);
        if (!found) throw notFound(`revision ${revisionId}`);
        return {
          revisionId,
          versionNo: found.versionNo,
          content: found.content ?? null,
        };
      },
    },
    knowledge: {
      async search(query, options) {
        record("knowledge.search", query, options);
        const needle = query.toLowerCase();
        return (spec.knowledge ?? [])
          .filter((d) => (d.text ?? d.filename).toLowerCase().includes(needle))
          .slice(0, options?.topK ?? 5)
          .map(
            (d): KnowledgeHit => ({
              documentId: d.id,
              filename: d.filename,
              score: 1,
              snippet: (d.text ?? "").slice(0, 200),
              pageRef: null,
            }),
          );
      },
      async get(docId) {
        record("knowledge.get", docId);
        const found = (spec.knowledge ?? []).find((d) => d.id === docId);
        if (!found) throw notFound(`document ${docId}`);
        return { id: found.id, filename: found.filename } as KnowledgeDocument;
      },
    },
    conversations: {
      async draft(options) {
        record("conversations.draft", options);
        drafts.push(options);
      },
      async send(options) {
        record("conversations.send", options);
        const sessionId = options.sessionId ?? `session-${++counter}`;
        sent.push({ sessionId, text: options.text });
        return { sessionId };
      },
      async events(sessionId, options) {
        record("conversations.events", sessionId, options);
        return spec.conversationEvents?.[sessionId] ?? [];
      },
    },
    connectors: {
      async list() {
        record("connectors.list");
        return Object.keys(spec.connectors ?? {}).map(
          (slug, index): ConnectorInfo => ({
            id: `connector-${index + 1}`,
            slug,
            name: slug,
            description: null,
            enabled: true,
            status: "connected",
            toolCount: Object.keys(spec.connectors?.[slug] ?? {}).length,
          }),
        );
      },
      async listTools(connector) {
        record("connectors.listTools", connector);
        const tools = spec.connectors?.[connector];
        if (!tools) throw notFound(`connector ${connector}`);
        return Object.keys(tools).map((name) => ({
          name,
          description: null,
          inputSchema: null,
          readOnly: !writeTools.has(name),
        }));
      },
      async callTool(connector, tool, args, options) {
        record("connectors.callTool", connector, tool, args, options);
        const handler = spec.connectors?.[connector]?.[tool];
        if (!handler) throw notFound(`tool ${connector}/${tool}`);
        if (writeTools.has(tool) && options?.write !== true) {
          throw new ValuzApiError(
            403,
            "write_tool_requires_confirmation",
            `${tool} changes data: call it with { write: true }`,
          );
        }
        const structured = await handler(args ?? {});
        return { content: [], structuredContent: structured ?? null, isError: false };
      },
    },
    automations: {
      async run(name, input) {
        record("automations.run", name, input);
        const handler = spec.automations?.[name];
        if (!handler) throw notFound(`automation ${name}`);
        const result = await handler(input);
        const runId = `run-${++counter}`;
        const status = result.status ?? "success";
        const run: AutomationRun = {
          runId,
          automationId: `automation-${name}`,
          status,
          done: status !== "queued" && status !== "running",
          startedAt: Date.now(),
          completedAt: Date.now(),
          summary: result.summary ?? null,
          errorMessage: null,
          sessionId: null,
          output: result.output ?? null,
          files: [],
          raw: {},
        };
        runs.set(runId, run);
        latest.set(name, run);
        return { runId, automationId: run.automationId };
      },
      async getRun(runId) {
        record("automations.getRun", runId);
        const run = runs.get(runId);
        if (!run) throw notFound(`run ${runId}`);
        return run;
      },
      async waitRun(runId, options) {
        record("automations.waitRun", runId, options);
        const run = runs.get(runId);
        if (!run) throw notFound(`run ${runId}`);
        return run;
      },
      async latestRun(name) {
        record("automations.latestRun", name);
        return latest.get(name) ?? null;
      },
    },
    storage: {
      async get<T = unknown>(key: string) {
        record("storage.get", key);
        return (storage.has(key) ? storage.get(key) : null) as T | null;
      },
      async set(key, value) {
        record("storage.set", key, value);
        storage.set(key, value);
      },
      async delete(key) {
        record("storage.delete", key);
        storage.delete(key);
      },
      async list(prefix) {
        record("storage.list", prefix);
        return [...storage.keys()]
          .filter((key) => key.startsWith(prefix ?? ""))
          .map((key): StorageItem => ({
            key,
            size: JSON.stringify(storage.get(key)).length,
            updatedAt: null,
          }));
      },
    },
    notifications: {
      async post(notification) {
        record("notifications.post", notification);
        notifications.push(notification);
        return { id: `notification-${notifications.length}` };
      },
    },
    service: { fetch: serviceNotSupported },
  };

  return Object.assign(client, {
    calls,
    storageData: storage,
    drafts,
    sent,
    notifications,
  });
}
