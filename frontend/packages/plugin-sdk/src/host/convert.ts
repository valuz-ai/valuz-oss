import type {
  PublicProject,
  PublicResource,
  PublicSession,
  PublicTask,
  PublicToolCall,
  PublicTurn,
} from "../types";

type Json = Record<string, unknown>;

const rec = (value: unknown): Json =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Json)
    : {};
const str = (value: unknown): string | null =>
  typeof value === "string" ? value : null;
const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const fn = <T extends (...args: never[]) => unknown>(
  value: unknown,
  fallback: T,
): T => (typeof value === "function" ? (value as T) : fallback);
const noop = (): void => undefined;

export function toSession(raw: unknown): PublicSession | null {
  if (!raw || typeof raw !== "object") return null;
  const s = rec(raw);
  return {
    id: String(s.id ?? ""),
    title: str(s.name ?? s.title),
    projectId: str(s.project_id ?? s.projectId),
    agentSlug: str(s.agent_slug ?? s.agentSlug),
    createdAt: num(s.created_at ?? s.createdAt),
  };
}

export function toProject(raw: unknown): PublicProject | null {
  if (!raw || typeof raw !== "object") return null;
  const p = rec(raw);
  return {
    id: String(p.id ?? ""),
    name: String(p.name ?? ""),
    kind: str(p.kind),
    rootPath: str(p.root_path ?? p.rootPath),
    icon: str(p.icon),
  };
}

export function toTurn(raw: unknown): PublicTurn {
  const t = rec(raw);
  const blocks = Array.isArray(t.blocks) ? t.blocks : [];
  // Already public (a test host hands over public props): keep as is.
  const assistantText =
    str(t.assistantText) ??
    blocks
      .map(rec)
      .filter(
        (block) => block.kind === "assistant" && typeof block.text === "string",
      )
      .map((block) => block.text as string)
      .join("\n\n");
  return {
    id: String(t.id ?? ""),
    userText: str(t.userText) ?? "",
    assistantText,
    createdAt: num(t.userTimestamp ?? t.createdAt),
    failed:
      typeof t.failed === "boolean"
        ? t.failed
        : typeof t.failedMessage === "string" && t.cancelled !== true,
  };
}

export function toTask(raw: unknown): PublicTask {
  const t = rec(raw);
  return {
    id: String(t.id ?? ""),
    projectId: str(t.project_id ?? t.projectId),
    title: String(t.title ?? ""),
    goal: String(t.goal ?? ""),
    status: String(t.status ?? ""),
    leadAgentSlug: str(t.lead_agent_slug ?? t.leadAgentSlug),
    createdAt: num(t.created_at ?? t.createdAt),
    updatedAt: num(t.updated_at ?? t.updatedAt),
  };
}

export function toResource(type: string, raw: unknown): PublicResource {
  const r = rec(raw);
  return {
    type,
    id: String(r.id ?? r.slug ?? ""),
    name: str(r.name ?? r.display_name ?? r.title),
    description: str(r.description),
  };
}

export function toToolCall(raw: unknown): PublicToolCall {
  const t = rec(raw);
  return {
    id: String(t.id ?? ""),
    name: String(t.name ?? ""),
    status: String(t.status ?? ""),
    input: t.input ?? null,
    output: t.output ?? null,
  };
}

const RESOURCE_ACTIONS = /^resource\.([^.]+)\.actions$/;

/**
 * Convert the props the host passes to a contribution in ``slot`` into the
 * stable public props (``SlotProps<slot>``). The host can rename internal
 * fields; this is the one place that follows.
 */
export function adaptSlotProps(
  slot: string,
  host: Record<string, unknown>,
): Record<string, unknown> {
  const setDraft = (text: string): void => {
    const set = host.setDraft;
    if (typeof set === "function") (set as (value: string) => void)(text);
  };

  if (slot.startsWith("conversation.tool-card.")) {
    return {
      tool: toToolCall(host.tool),
      toolUseId: String(host.toolUseId ?? ""),
      status: String(host.status ?? ""),
      input: host.input ?? null,
      output: host.output ?? null,
      thinking: host.thinking ?? null,
      hostRef:
        host.hostRef && typeof host.hostRef === "object"
          ? (host.hostRef as Json)
          : null,
    };
  }
  const resource = RESOURCE_ACTIONS.exec(slot);
  if (resource) {
    const type = String(host.resourceType ?? resource[1]);
    return { resourceType: type, resource: toResource(type, host.resource) };
  }

  switch (slot) {
    case "conversation.title.actions":
      return {
        sessionId: str(host.sessionId),
        session: toSession(host.session),
        scrollToTop: fn(host.scrollToTop, noop),
        turns: (Array.isArray(host.turns) ? host.turns : []).map(toTurn),
      };
    case "conversation.header.badges":
      return {
        sessionId: str(host.sessionId),
        session: toSession(host.session),
        project: toProject(host.project),
        agentSlug: str(host.agentSlug),
      };
    case "conversation.turn.actions":
      return {
        turn: toTurn(host.turn),
        scrollToTurn: fn(host.scrollToTurn, noop),
      };
    case "conversation.composer.plus.menu-items":
      return {
        sessionId: str(host.sessionId),
        projectId: str(host.projectId),
        setDraft,
        surface: str(host.surface),
      };
    case "conversation.empty.extra":
      return {
        projectId: str(host.projectId),
        agentSlug: str(host.agentSlug),
        setDraft,
      };
    case "context-panel.tabs":
      return {
        projectId: str(host.projectId),
        sessionId: str(host.sessionId),
        surface: str(host.surface),
      };
    case "project.detail.tabs":
      return {
        projectId: String(host.projectId ?? ""),
        navigate: fn(host.navigate, noop),
      };
    case "project.detail.header.actions":
      return {
        projectId: String(host.projectId ?? ""),
        project: toProject(host.project),
        navigate: fn(host.navigate, noop),
      };
    case "shell.topbar.actions":
      return {
        pathname: String(host.pathname ?? ""),
        activeProjectId: str(host.activeProjectId),
        rightPanelCollapsed: host.rightPanelCollapsed === true,
      };
    case "task.detail.sections":
      return {
        task: toTask(host.task),
        isCompleted: host.isCompleted === true,
      };
    default:
      return {};
  }
}
