import type { EffortLevel } from "@valuz/shared";
import { createFetchJson } from "./fetch-json";

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
  /** Shared kernel AgentConfig id (v2 live-reference); null until first deploy.
   *  Maps a project member back to its library agent
   *  (member.kernel_agent_id === agent.kernel_agent_id). */
  kernel_agent_id: string | null;
}

/** One派驻 of an agent — the project (workspace) it's deployed into. */
export interface AgentDeployment {
  workspace_id: string;
  /** Project-local member handle. */
  agent_slug: string;
}

/** Membership row linking a workspace to a kernel agent. */
export interface ProjectMember {
  id: string;
  workspace_id: string;
  agent_slug: string;
  kernel_agent_id: string;
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
   *  the target workspace, when omitted (VALUZ-AGENT-SLUG). */
  agent_slug?: string;
}

export interface CreateBlankAgentPayload {
  /** Optional — backend derives from ``name``, unique within the workspace,
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

const fetchJson = createFetchJson(() => _apiBase);

export const agentsApi = {
  listAgents(source?: string): Promise<{ agents: Agent[] }> {
    const params = source ? `?source=${encodeURIComponent(source)}` : "";
    return fetchJson(`/v1/agents${params}`);
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

  createAgent(payload: CreateAgentPayload): Promise<Agent> {
    return fetchJson("/v1/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  updateAgent(slug: string, payload: UpdateAgentPayload): Promise<Agent> {
    return fetchJson(`/v1/agents/${encodeURIComponent(slug)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  deleteAgent(slug: string): Promise<void> {
    return fetchJson(`/v1/agents/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    });
  },

  listMembers(workspaceId: string): Promise<{ agents: MemberWithAgent[] }> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents`,
    );
  },

  /** v2 派驻: deploy (live-reference) a library agent into a project. */
  deploy(
    workspaceId: string,
    payload: DeployAgentPayload,
  ): Promise<MemberWithAgent> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents:deploy`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  },

  createBlank(
    workspaceId: string,
    payload: CreateBlankAgentPayload,
  ): Promise<MemberWithAgent> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  },

  deleteMember(workspaceId: string, agentSlug: string): Promise<void> {
    return fetchJson(
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentSlug)}`,
      { method: "DELETE" },
    );
  },
};
