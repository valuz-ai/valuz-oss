import type { EffortLevel } from "@valuz/shared";
import { createFetchJson } from "./fetch-json";
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
  /** Default model provider for instances; null = unpinned (set per-instance). */
  provider_id: string | null;
  /** Default reasoning-effort budget for instances; null = no override. */
  effort: EffortLevel | null;
  source: string;
  readonly: boolean;
  deletable: boolean;
  /** Preset icon key or uploaded asset URL (08-agents-module v2); null = unset. */
  avatar: string | null;
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
  provider_id?: string | null;
  effort?: EffortLevel | null;
  avatar?: string | null;
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
  listAgents(source?: string): Promise<{ agents: Agent[] }> {
    const params = source ? `?source=${encodeURIComponent(source)}` : "";
    return fetchJson(`/v1/agents${params}`, { cache: AGENTS_LIST_CACHE });
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
    return fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/agents`,
      { cache: projectAgentsCache(projectId) },
    );
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
      },
    );
    invalidateAgents(result.project_id);
    return result;
  },

  async deleteMember(projectId: string, agentSlug: string): Promise<void> {
    await fetchJson(
      `/v1/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentSlug)}`,
      { method: "DELETE" },
    );
    invalidateAgents(projectId);
  },
};
