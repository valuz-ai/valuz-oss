import type { ComponentType } from "react";

/** Plugin API version (SemVer). It is the SDK's own version (doc 12 §9). */
export const PLUGIN_API_VERSION = "1.0.0";

// ── Public data types ───────────────────────────────────────────────────────
// Stable subsets of the host's internal types. The host adapter converts its
// own props into these, so an internal field rename never reaches a plugin.

export interface PublicSession {
  id: string;
  title: string | null;
  projectId: string | null;
  agentSlug: string | null;
  /** Unix epoch milliseconds, when known. */
  createdAt: number | null;
}

export interface PublicProject {
  id: string;
  name: string;
  kind: string | null;
  rootPath: string | null;
  icon: string | null;
}

export interface PublicTurn {
  id: string;
  /** What the user sent. */
  userText: string;
  /** The assistant's visible text, concatenated; empty while it has none. */
  assistantText: string;
  /** Unix epoch milliseconds the user sent it, when known. */
  createdAt: number | null;
  /** The run failed (an error, not a cancel). */
  failed: boolean;
}

export interface PublicTask {
  id: string;
  projectId: string | null;
  title: string;
  goal: string;
  status: string;
  leadAgentSlug: string | null;
  createdAt: number | null;
  updatedAt: number | null;
}

export interface PublicResource {
  /** ``agent`` / ``skill`` / ``connector`` / ``kb`` … */
  type: string;
  id: string;
  name: string | null;
  description: string | null;
}

export interface PublicToolCall {
  id: string;
  name: string;
  status: string;
  input: unknown;
  output: unknown;
}

// ── Slots (doc 12 §3.2) ─────────────────────────────────────────────────────

export interface ConversationTitleActionsProps {
  sessionId: string | null;
  session: PublicSession | null;
  scrollToTop: () => void;
  turns: PublicTurn[];
}

export interface ConversationHeaderBadgesProps {
  sessionId: string | null;
  session: PublicSession | null;
  project: PublicProject | null;
  agentSlug: string | null;
}

export interface ConversationTurnActionsProps {
  turn: PublicTurn;
  scrollToTurn: () => void;
}

export interface ToolCardSlotProps {
  tool: PublicToolCall;
  toolUseId: string;
  status: string;
  /** The call's arguments; a JSON string from the runtime arrives parsed. */
  input: unknown;
  /** The result as the runtime reported it (a string, MCP content blocks …). */
  output: unknown;
  /** The result's text: MCP ``text`` blocks joined by newlines, or the string itself. */
  outputText: string;
  /**
   * The result as data: MCP ``structuredContent`` when present, else the first
   * text block parsed as JSON; ``null`` while running or when it isn't JSON.
   */
  outputJson: unknown;
  thinking: unknown;
  hostRef: Record<string, unknown> | null;
}

export interface ComposerPlusMenuItemsProps {
  sessionId: string | null;
  projectId: string | null;
  setDraft: (text: string) => void;
  surface: string | null;
}

export interface ConversationEmptyExtraProps {
  projectId: string | null;
  agentSlug: string | null;
  setDraft: (text: string) => void;
}

export interface ContextPanelTabsProps {
  projectId: string | null;
  sessionId: string | null;
  surface: string | null;
}

export interface ProjectDetailTabsProps {
  projectId: string;
  navigate: (path: string) => void;
}

export interface ProjectDetailHeaderActionsProps {
  projectId: string;
  project: PublicProject | null;
  navigate: (path: string) => void;
}

export interface ResourceActionsSlotProps {
  resourceType: string;
  resource: PublicResource;
}

export interface ShellTopbarActionsProps {
  pathname: string;
  activeProjectId: string | null;
  rightPanelCollapsed: boolean;
}

export interface TaskDetailSectionsProps {
  task: PublicTask;
  isCompleted: boolean;
}

/** Slots with a fixed name. */
export interface FixedSlotProps {
  "conversation.title.actions": ConversationTitleActionsProps;
  "conversation.header.badges": ConversationHeaderBadgesProps;
  "conversation.turn.actions": ConversationTurnActionsProps;
  "conversation.composer.plus.menu-items": ComposerPlusMenuItemsProps;
  "conversation.empty.extra": ConversationEmptyExtraProps;
  "context-panel.tabs": ContextPanelTabsProps;
  "project.detail.tabs": ProjectDetailTabsProps;
  "project.detail.header.actions": ProjectDetailHeaderActionsProps;
  "shell.topbar.actions": ShellTopbarActionsProps;
  "task.detail.sections": TaskDetailSectionsProps;
}

/** ``conversation.tool-card.{tool}``: ``tool`` is the bare tool name. */
export type ToolCardSlotName = `conversation.tool-card.${string}`;
/** ``resource.{type}.actions`` for any resource type. */
export type ResourceActionsSlotName = `resource.${string}.actions`;

export type SlotName =
  keyof FixedSlotProps | ToolCardSlotName | ResourceActionsSlotName;

/** The props the host passes to a contribution in slot ``N``. */
export type SlotProps<N extends SlotName> = N extends keyof FixedSlotProps
  ? FixedSlotProps[N]
  : N extends ToolCardSlotName
    ? ToolCardSlotProps
    : N extends ResourceActionsSlotName
      ? ResourceActionsSlotProps
      : never;

/** The 10 fixed public slot names, in doc 12 §3.2 order. */
export const FIXED_PUBLIC_SLOTS = [
  "conversation.title.actions",
  "conversation.header.badges",
  "conversation.turn.actions",
  "conversation.composer.plus.menu-items",
  "conversation.empty.extra",
  "context-panel.tabs",
  "project.detail.tabs",
  "project.detail.header.actions",
  "shell.topbar.actions",
  "task.detail.sections",
] as const satisfies readonly (keyof FixedSlotProps)[];

/** Public slots whose host kind is ``keyed`` (a ``key`` is required). */
export const KEYED_PUBLIC_SLOTS: readonly string[] = [
  "context-panel.tabs",
  "project.detail.tabs",
];

export interface SlotRegistration<N extends SlotName = SlotName> {
  /** Local id; the host namespaces it as ``x:<plugin id>:<id>``. */
  id: string;
  component: ComponentType<SlotProps<N>>;
  /** Lower renders first (and wins in single / keyed slots). Default 0. */
  priority?: number;
  /** Required for the keyed slots (``context-panel.tabs``, ``project.detail.tabs``). */
  key?: string;
  /**
   * Tab title for the keyed slots: a key of the plugin's own ``locales``, or
   * plain text. Defaults to the plugin's name.
   */
  label?: string;
}

// ── Registration helpers ────────────────────────────────────────────────────

export interface RouteSpec {
  /** Local id. */
  id: string;
  /** Must be ``/x/<plugin id>`` or below it. */
  path: string;
  /** A key of the plugin's ``locales``, or plain text. */
  title: string;
  description?: string;
  /**
   * Sidebar icon id (with ``nav: true``): assistant, knowledge, skills, scheduled, playbooks, activity, settings, agents, connectors, plugins, marketplace, projectTasks, star, compass, watchlist, portfolio, dashboard, globe.
   * Unknown ids show a gear; the default is ``plugins``.
   */
  icon?: string;
}

export interface SettingsSectionSpec {
  id: string;
  title: string;
  description?: string;
  icon?: string;
}

export interface NavItemSpec {
  id: string;
  label: string;
  /** Sidebar icon id — same set as ``RouteSpec.icon``; default ``plugins``. */
  icon?: string;
  /** Must point at one of the plugin's own pages (``/x/<plugin id>…``). */
  path: string;
}

/** What a plugin may register (doc 12 §3.1). Everything else throws. */
export interface PluginRegistry {
  slot<N extends SlotName>(name: N, registration: SlotRegistration<N>): void;
  route(route: RouteSpec, component: ComponentType): void;
  settingsSection(section: SettingsSectionSpec, component: ComponentType): void;
  navItem(item: NavItemSpec): void;
}

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface PluginLog {
  debug(message: string, ...details: unknown[]): void;
  info(message: string, ...details: unknown[]): void;
  warn(message: string, ...details: unknown[]): void;
  error(message: string, ...details: unknown[]): void;
}

// ── The plugin definition and its context (doc 12 §2) ───────────────────────

export interface PluginContext {
  readonly pluginId: string;
  readonly registry: PluginRegistry;
  /** Hold a resource; the returned function runs when the plugin unloads. */
  effect(setup: () => (() => void) | void): void;
  /** Mount a child plugin whose lifetime is bound to this one. */
  plugin(child: PluginDefinition): Promise<void>;
  /** Valuz's built-in backend capabilities (doc 12 §5). */
  readonly valuz: ValuzClient;
  /** The plugin's configuration as of now (``config`` of the manifest). */
  readonly config: Readonly<Record<string, unknown>>;
  /** Writes to the plugin's log in Settings → Extensions. */
  readonly log: PluginLog;
}

export interface PluginDefinition {
  /** Must equal the ``id`` of ``valuz-plugin.json``. */
  id: string;
  apply(ctx: PluginContext): void | Promise<void>;
}

// ── Host interaction ────────────────────────────────────────────────────────

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: "default" | "success" | "error" | "warning";
}

export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
}

export interface DraftConversationOptions {
  projectId?: string;
  /** An agent slug. */
  agent?: string;
  text: string;
}

export interface HostContextValue {
  locale: string;
  theme: "light" | "dark";
  edition: string;
  deployment: "local" | "cloud";
  orgId: string | null;
}

// ── ctx.valuz (doc 12 §5) ───────────────────────────────────────────────────

export interface ValuzApiErrorShape {
  status: number;
  code: string;
  message: string;
}

export interface ProjectDetail extends PublicProject {
  instructions: string | null;
}

export interface ProjectFileNode {
  name: string;
  path: string;
  type: "file" | "directory" | string;
  size?: number | null;
  truncated?: boolean;
  children?: ProjectFileNode[];
  [key: string]: unknown;
}

export interface PublicArtifact {
  /** Artifact (deliverable) id. */
  id: string;
  /** The version to read with ``artifacts.content``. */
  revisionId: string;
  name: string;
  kind: string | null;
  versionNo: number | null;
  fileName: string | null;
  mimeType: string | null;
  updatedAt: number | null;
  sessionId: string | null;
}

export interface ArtifactContent {
  revisionId: string;
  versionNo: number | null;
  /** The document text, or ``null`` when the bytes are gone. */
  content: string | null;
}

export interface KnowledgeHit {
  documentId: string;
  filename: string;
  score: number;
  snippet: string;
  pageRef: string | null;
}

export interface KnowledgeDocument {
  id: string;
  filename: string;
  [key: string]: unknown;
}

export interface ConversationEvent {
  seq: number;
  timestamp: number | null;
  event: Record<string, unknown>;
}

export interface ConnectorInfo {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  enabled: boolean;
  status: string;
  toolCount: number | null;
}

export interface ConnectorTool {
  name: string;
  description: string | null;
  inputSchema: Record<string, unknown> | null;
  readOnly: boolean;
}

export interface ToolCallResult {
  content: unknown[];
  structuredContent: unknown;
  isError: boolean;
}

export interface AutomationRun {
  runId: string;
  automationId: string | null;
  status: string;
  /** ``true`` once the run reached a final state. */
  done: boolean;
  startedAt: number | null;
  completedAt: number | null;
  summary: string | null;
  errorMessage: string | null;
  sessionId: string | null;
  /** The structured output (``artifact``) of the run, if any. */
  output: unknown;
  files: unknown[];
  /** The raw response, for fields this version does not name. */
  raw: Record<string, unknown>;
}

export interface StorageItem {
  key: string;
  size: number;
  updatedAt: number | null;
}

export interface ValuzClient {
  projects: {
    list(): Promise<PublicProject[]>;
    get(id: string): Promise<ProjectDetail>;
    files(
      id: string,
      options?: { path?: string; depth?: number },
    ): Promise<ProjectFileNode[]>;
  };
  artifacts: {
    list(scope: {
      projectId?: string;
      sessionId?: string;
    }): Promise<PublicArtifact[]>;
    content(revisionId: string): Promise<ArtifactContent>;
  };
  knowledge: {
    search(
      query: string,
      options?: { projectId?: string; kbIds?: string[]; topK?: number },
    ): Promise<KnowledgeHit[]>;
    get(docId: string): Promise<KnowledgeDocument>;
  };
  conversations: {
    /** Open a new conversation with the composer prefilled; the user sends. */
    draft(options: DraftConversationOptions): Promise<void>;
    /** Send a message and let the agent start (experimental). */
    send(options: {
      sessionId?: string;
      projectId?: string;
      agent?: string;
      text: string;
    }): Promise<{ sessionId: string }>;
    events(
      sessionId: string,
      options?: { afterSeq?: number },
    ): Promise<ConversationEvent[]>;
  };
  connectors: {
    list(): Promise<ConnectorInfo[]>;
    listTools(connector: string): Promise<ConnectorTool[]>;
    /** ``write: true`` asks the user first (experimental). */
    callTool(
      connector: string,
      tool: string,
      args?: Record<string, unknown>,
      options?: { write?: boolean },
    ): Promise<ToolCallResult>;
  };
  automations: {
    run(
      name: string,
      input?: unknown,
    ): Promise<{ runId: string; automationId: string | null }>;
    getRun(runId: string): Promise<AutomationRun>;
    waitRun(
      runId: string,
      options?: { timeoutMs?: number; intervalMs?: number },
    ): Promise<AutomationRun>;
    latestRun(name: string): Promise<AutomationRun | null>;
  };
  storage: {
    get<T = unknown>(key: string): Promise<T | null>;
    set(key: string, value: unknown): Promise<void>;
    delete(key: string): Promise<void>;
    list(prefix?: string): Promise<StorageItem[]>;
  };
  notifications: {
    post(notification: {
      title: string;
      body?: string;
      link?: string;
    }): Promise<{ id: string }>;
  };
  /** B-level services: not supported in plugin API 1.x (always throws). */
  service: {
    fetch(path: string, init?: unknown): Promise<never>;
  };
}
