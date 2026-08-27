import type { EffortLevel } from "@valuz/shared";
import { createFetchJson } from "./fetch-json";
import { resolveApiBase } from "./base-resolver";
import {
  DEVICE_TARGET_ID_PREFIX,
  getDefaultExecutionTarget,
} from "../edition/execution-targets";
import { fanOutTargets, getListFanOutTargets } from "../edition/list-fanout";
import { invalidateRequestCache } from "./request";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setAgentsApiBase = (url: string): void => {
  _apiBase = url;
};

/** Official, read-only agent (seeded by the backend). */
export interface Agent {
  id: string;
  slug: string;
  name: string;
  description: string;
  instructions: string;
  runtime: string;
  model: string;
  skills: string[];
  connector_types: string[];
  knowledge_scope: string[];
  /** Default model provider for instances; null = unpinned (set per-instance). */
  provider_id: string | null;
  /** Default reasoning-effort budget for instances; null = no override. */
  effort: EffortLevel | null;
  /** System agents are installed and managed by the runtime. */
  kind: "system" | "standard";
  /** Explicit bindings for normal agents; live owner-scoped resources for Valurion. */
  resource_policy: "explicit" | "all_available";
  /** Dynamically prepend the current distribution's Valurion instructions. */
  inherit_global_instructions: boolean;
  permission_mode: string;
  source: string;
  readonly: boolean;
  deletable: boolean;
  /** Preset icon key or uploaded asset URL (08-agents-module v2); null = unset. */
  avatar: string | null;
  /**
   * Execution target this agent runs on, when it is not "wherever you are".
   * An edition can list an agent that only exists on another backend (a
   * colleague's desktop reached through a relay, say); picking it in the
   * composer moves the conversation there instead of failing with "agent not
   * found" against the local backend. Absent = runs on the active target.
   */
  exec_target_id?: string;
  /**
   * Short tag for the composer row (e.g. 分享), set by whoever produced the
   * row. Deriving it from ``exec_target_id`` alone means silently rendering
   * nothing whenever that target has not been registered yet — a race the
   * reader experiences as "the tag is missing".
   */
  badge_label?: string;
  /** Palette for {@link badge_label} — "shared" / "remote" match the library's
   *  own tags, so the same agent reads the same in both places. */
  badge_tone?: "shared" | "remote";
}

/** One派驻 of an agent — the project (project) it's deployed into. */
export interface AgentDeployment {
  project_id: string;
  /** Project-local member handle. */
  agent_slug: string;
}

/** Membership row linking a project to a kernel agent. */
export interface ProjectMember {
  id: string;
  project_id: string;
  agent_slug: string;
  source_agent_slug: string | null;
}

/** Kernel agent config summary returned alongside a membership row. */
export interface AgentSummary {
  id: string;
  name: string;
  model: string;
  runtime_provider: string;
  instructions: string;
  skills: string[];
  /** Connector slugs currently bound to this agent (MCP servers). */
  connectors: string[];
  /** Pinned model provider id; null = env/default fallback at run time. */
  provider_id: string | null;
  /** Reasoning-effort budget; null = no override (runtime SDK default). */
  effort: EffortLevel | null;
  /**
   * How this member resolves resources. An ``all_available`` member (Valurion)
   * reports an EMPTY ``skills`` by design — its real set is the owner's live
   * library, resolved when the session is created. Read this before rendering
   * ``skills``, or such an agent looks like it carries nothing.
   *
   * Optional so a client stays compatible with a backend that predates the
   * field; absent is read as ``explicit``.
   */
  resource_policy?: "explicit" | "all_available";
}

export interface MemberWithAgent {
  member: ProjectMember;
  agent: AgentSummary | null;
}

export interface ConnectorBindingInput {
  type: string;
  account_id?: string | null;
}

export interface DeployAgentPayload {
  source_agent_slug: string;
  /** Optional — backend derives from the source agent's name, unique within
   *  the target project, when omitted (VALUZ-AGENT-SLUG). */
  agent_slug?: string;
}

export interface CreateBlankAgentPayload {
  /** Optional — backend derives from ``name``, unique within the project,
   *  when omitted (VALUZ-AGENT-SLUG). */
  agent_slug?: string;
  name: string;
  instructions?: string;
  runtime?: string;
  model?: string;
  provider_id?: string | null;
  effort?: EffortLevel | null;
  skills?: string[] | null;
  connector_bindings?: ConnectorBindingInput[] | null;
}

export interface CreateAgentPayload {
  /** Optional — backend derives a CJK-preserving, globally-unique slug from
   *  ``name`` when omitted (VALUZ-AGENT-SLUG). */
  slug?: string;
  name: string;
  description?: string;
  instructions?: string;
  runtime?: string;
  model?: string;
  skills?: string[];
  connector_types?: string[];
  knowledge_scope?: string[];
  inherit_global_instructions?: boolean;
  permission_mode?: string;
  provider_id?: string | null;
  effort?: EffortLevel | null;
  avatar?: string | null;
}

export interface UpdateAgentPayload {
  name?: string | null;
  description?: string | null;
  instructions?: string | null;
  runtime?: string | null;
  model?: string | null;
  skills?: string[] | null;
  connector_types?: string[] | null;
  knowledge_scope?: string[] | null;
  inherit_global_instructions?: boolean | null;
  permission_mode?: string | null;
  provider_id?: string | null;
  effort?: EffortLevel | null;
  avatar?: string | null;
}

export interface ListAgentsOptions {
  /** Route this request to a specific execution target. */
  baseUrl?: string;
  /** Bypass the shared list cache when the active target or roster changes. */
  fresh?: boolean;
}

export interface EffectiveAgentResource {
  id: string;
  slug: string;
  name: string;
  source: string;
  status: string;
}

export interface EffectiveAgentResourceWarning {
  resource_type: string;
  resource_id: string;
  code: string;
  message: string;
}

export interface EffectiveAgentResources {
  /** Which rule selected the set: the agent's own bindings, or the owner's
   *  live library. The always-on baseline is in both. */
  policy: "explicit" | "all_available";
  resolved_at: number;
  counts: {
    skills: number;
    connectors: number;
    knowledge_bases: number;
  };
  skills: EffectiveAgentResource[];
  connectors: EffectiveAgentResource[];
  knowledge_bases: EffectiveAgentResource[];
  warnings: EffectiveAgentResourceWarning[];
}

/** Spec of an agent the user is confirming after the assistant proposed it
 *  via the ``propose_agent`` tool. Backend derives a unique slug from name. */
export interface ProposeAgentConfirmPayload {
  name: string;
  instructions: string;
  description?: string;
  runtime?: string;
  model?: string;
  effort?: EffortLevel | null;
  skills?: string[];
  connectors?: string[];
  avatar?: string | null;
}

export interface ProposeAgentConfirmResult {
  agent: AgentSummary;
  member: ProjectMember | null;
  /** True when the session was bound to a project and the agent was deployed. */
  deployed: boolean;
  project_id: string | null;
}

const fetchJson = createFetchJson(() => _apiBase);
const projectBase = (projectId: string): string =>
  resolveApiBase({ projectId }, _apiBase);
const AGENTS_TAG = "agents";
const AGENTS_CACHE_TTL_MS = 30_000;
const AGENTS_LIST_CACHE = { ttlMs: AGENTS_CACHE_TTL_MS, tags: [AGENTS_TAG] };

function projectAgentsTag(projectId: string): string {
  return `project-agents:${projectId}`;
}

function projectAgentsCache(projectId: string) {
  return {
    ttlMs: AGENTS_CACHE_TTL_MS,
    tags: [AGENTS_TAG, projectAgentsTag(projectId)],
  };
}

function invalidateAgents(projectId?: string | null): void {
  invalidateRequestCache({
    tags: projectId ? [AGENTS_TAG, projectAgentsTag(projectId)] : [AGENTS_TAG],
  });
}

export const agentsApi = {
  /**
   * The agent library.
   *
   * Multi-target editions fan out over MACHINES: another desktop has its own
   * library, and "what can I run" is the union. Rows answered by a non-default
   * target carry ``exec_target_id``, which is what makes picking one in the
   * composer move the conversation to that machine instead of failing with
   * "agent not found" locally.
   *
   * Unlike projects and sessions, a sibling runtime of the SAME account (an
   * edition's cloud execution plane) is skipped: it materializes the account's
   * own library, so fanning out to it lists every agent a second time under a
   * different target rather than finding new ones. Only ``device:*`` targets
   * hold a library this one has never seen.
   *
   * Slugs are NOT deduplicated across targets: two machines may both have an
   * "sde", and they are different agents with different instructions. The
   * caller distinguishes them by ``exec_target_id``.
   *
   * ``options.baseUrl`` addresses one specific target, so it opts out of the
   * fan-out (that is how the fan-out asks each target, and how the composer
   * reads a single machine's library).
   */
  async listAgents(
    source?: string,
    options?: ListAgentsOptions,
  ): Promise<{ agents: Agent[] }> {
    const params = source ? `?source=${encodeURIComponent(source)}` : "";
    const cache = options?.fresh ? undefined : AGENTS_LIST_CACHE;
    const machines = options?.baseUrl
      ? []
      : getListFanOutTargets().filter(
          (target) =>
            target.isDefault || target.id.startsWith(DEVICE_TARGET_ID_PREFIX),
        );
    const targets = machines.length >= 2 ? machines : [];
    if (targets.length === 0) {
      return fetchJson(`/v1/agents${params}`, {
        baseUrl: options?.baseUrl,
        cache,
      });
    }
    const defaultTargetId = getDefaultExecutionTarget()?.id;
    const outcome = await fanOutTargets(
      (target, signal) =>
        fetchJson<{ agents: Agent[] }>(`/v1/agents${params}`, {
          baseUrl: target.baseUrl,
          cache,
          signal,
        }),
      targets,
    );
    const merged: Agent[] = [];
    for (const { target, value } of outcome.values) {
      const elsewhere = target.id !== defaultTargetId;
      for (const agent of value.agents) {
        merged.push(
          elsewhere ? { ...agent, exec_target_id: target.id } : agent,
        );
      }
    }
    return { agents: merged };
  },

  getAgent(slug: string): Promise<Agent> {
    return fetchJson(`/v1/agents/${encodeURIComponent(slug)}`);
  },

  /** List the projects this agent is派驻'd (deployed) into — v2 live-reference.
   *  Backs the agent detail「派驻于 N 个项目」panel + the delete-guard UX. */
  listDeployments(
    slug: string,
  ): Promise<{ deployments: AgentDeployment[]; count: number }> {
    return fetchJson(`/v1/agents/${encodeURIComponent(slug)}/deployments`);
  },

  async createAgent(payload: CreateAgentPayload): Promise<Agent> {
    const result = await fetchJson<Agent>("/v1/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    invalidateAgents();
    return result;
  },

  async updateAgent(slug: string, payload: UpdateAgentPayload): Promise<Agent> {
    const result = await fetchJson<Agent>(
      `/v1/agents/${encodeURIComponent(slug)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    invalidateAgents();
    return result;
  },

  async copyAgent(
    slug: string,
    name?: string,
    newSlug?: string,
  ): Promise<Agent> {
    const result = await fetchJson<Agent>(
      `/v1/agents/${encodeURIComponent(slug)}/copy`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(name ? { name } : {}),
          ...(newSlug ? { slug: newSlug } : {}),
        }),
      },
    );
    invalidateAgents();
    return result;
  },

  /** What a session for this agent would actually be created with.
   *
   *  Answers for ANY agent — an explicit-binding one reports its bindings, an
   *  ``all_available`` one the owner's live library, and both include the
   *  always-on baseline the host injects into every session. Do not re-derive
   *  this from ``agent.skills``: that array is the bindings alone. */
  getEffectiveResources(
    slug: string,
    opts: { baseUrl?: string } = {},
  ): Promise<EffectiveAgentResources> {
    return fetchJson(
      `/v1/agents/${encodeURIComponent(slug)}/effective-resources`,
      opts.baseUrl ? { baseUrl: opts.baseUrl } : undefined,
    );
  },

  /** Delete an agent. ``cascade`` first 解除 every 派驻 the agent has, then
   *  deletes it — the confirmed-delete path. Without it, an agent still
   *  deployed to a project returns 409. */
  async deleteAgent(slug: string, opts?: { cascade?: boolean }): Promise<void> {
    const query = opts?.cascade ? "?cascade=true" : "";
    await fetchJson(`/v1/agents/${encodeURIComponent(slug)}${query}`, {
      method: "DELETE",
    });
    invalidateAgents();
  },

  listMembers(projectId: string): Promise<{ agents: MemberWithAgent[] }> {
    return fetchJson(`/v1/projects/${encodeURIComponent(projectId)}/agents`, {
      cache: projectAgentsCache(projectId),
      baseUrl: projectBase(projectId),
    });
  },

  /** v2 派驻: deploy (live-reference) a library agent into a project. */
  async deploy(
    projectId: string,
    payload: DeployAgentPayload,
  ): Promise<MemberWithAgent> {
    const result = await fetchJson<MemberWithAgent>(
      `/v1/projects/${encodeURIComponent(projectId)}/agents:deploy`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: projectBase(projectId),
      },
    );
    invalidateAgents(projectId);
    return result;
  },

  async createBlank(
    projectId: string,
    payload: CreateBlankAgentPayload,
  ): Promise<MemberWithAgent> {
    const result = await fetchJson<MemberWithAgent>(
      `/v1/projects/${encodeURIComponent(projectId)}/agents`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: projectBase(projectId),
      },
    );
    invalidateAgents(projectId);
    return result;
  },

  /** Confirm an agent the assistant proposed via ``propose_agent``. Creates
   *  the library agent and, when the session has a project, deploys it there. */
  async confirmProposal(
    sessionId: string,
    payload: ProposeAgentConfirmPayload,
  ): Promise<ProposeAgentConfirmResult> {
    const result = await fetchJson<ProposeAgentConfirmResult>(
      `/v1/agents/proposals/${encodeURIComponent(sessionId)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        baseUrl: resolveApiBase({ sessionId }, _apiBase),
      },
    );
    invalidateAgents(result.project_id);
    return result;
  },

  async deleteMember(projectId: string, agentSlug: string): Promise<void> {
    await fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentSlug)}`,
      { method: "DELETE", baseUrl: projectBase(projectId) },
    );
    invalidateAgents(projectId);
  },
};
