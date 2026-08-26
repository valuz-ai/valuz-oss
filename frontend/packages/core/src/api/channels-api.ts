import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setChannelsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface WeComAIBotBinding {
  enabled: boolean;
  channel_instance_id: string;
  owner_user_id: string;
  agent_slug: string;
  bot_id: string;
  has_secret: boolean;
  connected: boolean;
  connection_status: string;
  connection_error?: string | null;
}

export interface UpdateWeComAIBotBindingPayload {
  enabled: boolean;
  channel_instance_id?: string;
  agent_slug: string;
  bot_id: string;
  secret?: string;
}

export interface FeishuBinding {
  enabled: boolean;
  channel_instance_id: string;
  owner_user_id: string;
  agent_slug: string;
  app_id: string;
  has_app_secret: boolean;
  has_verification_token: boolean;
  has_encrypt_key: boolean;
  connected: boolean;
  connection_status: string;
  connection_error?: string | null;
}

export interface UpdateFeishuBindingPayload {
  enabled: boolean;
  channel_instance_id?: string;
  agent_slug: string;
  app_id: string;
  app_secret?: string;
}

export interface ChannelChatItem {
  external_chat_id: string;
  name: string;
  bound_project_id?: string | null;
  /** Valuz created this group, so the bot owns it and may delete it. */
  created_by_valuz?: boolean;
  /** …and nobody has joined yet, so a join link is the only way in. */
  needs_join?: boolean;
}

export interface ChatProjectBinding {
  channel_instance_id: string;
  external_chat_id: string;
  project_id: string;
  external_chat_name?: string | null;
  default_agent_slug?: string | null;
  /** Which IM the group lives in — "feishu" | "wecom_aibot". */
  platform?: string;
  created_by_valuz?: boolean;
  /** Nobody has joined yet, so a join link is the only way in. */
  needs_join?: boolean;
}

export interface CreatedChat {
  external_chat_id: string;
  name: string;
  project_id: string;
  /** How the person joins: the bot is the creator, so nobody else is in yet. */
  share_link?: string | null;
}

export interface FeishuBindingTestResult {
  credential_ok: boolean;
  error?: string | null;
  connected: boolean;
  connection_status: string;
  connection_error?: string | null;
}

const fetchJson = createFetchJson(() => _apiBase);

// A chat binding belongs to a project, and a project lives on exactly one
// backend — so these calls have to follow it, the way projects-api and
// sessions-api do. Without the project id they fall back to the module
// default, which on a multi-target edition means a cloud project's bindings
// are read from (and written to) the local backend.
const chatBase = (projectId?: string): string =>
  resolveApiBase({ projectId }, _apiBase);

export const channelsApi = {
  getWeComAIBotBinding(agentSlug: string): Promise<WeComAIBotBinding> {
    return fetchJson(
      `/v1/channels/wecom-aibot/bindings/${encodeURIComponent(agentSlug)}`,
    );
  },

  updateWeComAIBotBinding(
    payload: UpdateWeComAIBotBindingPayload,
  ): Promise<WeComAIBotBinding> {
    const secret = payload.secret?.trim();
    const body: UpdateWeComAIBotBindingPayload = {
      enabled: payload.enabled,
      agent_slug: payload.agent_slug,
      bot_id: payload.bot_id,
    };
    if (payload.channel_instance_id?.trim()) {
      body.channel_instance_id = payload.channel_instance_id.trim();
    }
    if (secret) {
      body.secret = secret;
    }
    return fetchJson(
      `/v1/channels/wecom-aibot/bindings/${encodeURIComponent(
        payload.agent_slug,
      )}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  getFeishuBinding(agentSlug: string): Promise<FeishuBinding> {
    return fetchJson(
      `/v1/channels/feishu/bindings/${encodeURIComponent(agentSlug)}`,
    );
  },

  updateFeishuBinding(
    payload: UpdateFeishuBindingPayload,
  ): Promise<FeishuBinding> {
    const appSecret = payload.app_secret?.trim();
    const body: UpdateFeishuBindingPayload = {
      enabled: payload.enabled,
      agent_slug: payload.agent_slug,
      app_id: payload.app_id,
    };
    if (payload.channel_instance_id?.trim()) {
      body.channel_instance_id = payload.channel_instance_id.trim();
    }
    if (appSecret) {
      body.app_secret = appSecret;
    }
    return fetchJson(
      `/v1/channels/feishu/bindings/${encodeURIComponent(payload.agent_slug)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  /** Groups the bot is already a member of — the project page's picker. */
  listFeishuChats(
    agentSlug?: string,
    projectId?: string,
  ): Promise<ChannelChatItem[]> {
    const qs = new URLSearchParams();
    if (agentSlug) qs.set("agent_slug", agentSlug);
    const suffix = qs.toString() ? `?${qs}` : "";
    return fetchJson(`/v1/channels/feishu/chats${suffix}`, {
      baseUrl: chatBase(projectId),
      cache: "no-store",
    });
  },

  /** Create a Feishu group with the bot already in it, bound to a project. */
  createFeishuChat(payload: {
    name: string;
    project_id: string;
    channel_instance_id?: string;
  }): Promise<CreatedChat> {
    return fetchJson("/v1/channels/feishu/chats", {
      baseUrl: chatBase(payload.project_id),
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: payload.name,
        project_id: payload.project_id,
        channel_instance_id: payload.channel_instance_id ?? "feishu-main",
      }),
    });
  },

  /** A join link for a group the bot is in, generated on demand. */
  async feishuChatLink(
    externalChatId: string,
    projectId?: string,
  ): Promise<string | null> {
    const result = await fetchJson<{ share_link: string | null }>(
      `/v1/channels/feishu/chats/${encodeURIComponent(externalChatId)}/link`,
      { baseUrl: chatBase(projectId), cache: "no-store" },
    );
    return result.share_link ?? null;
  },

  /** Dissolve a group Valuz created (and drop its binding). */
  async deleteFeishuChat(
    externalChatId: string,
    projectId?: string,
  ): Promise<void> {
    await fetchJson(
      `/v1/channels/feishu/chats/${encodeURIComponent(externalChatId)}`,
      { baseUrl: chatBase(projectId), method: "DELETE" },
    );
  },

  listChatBindings(projectId?: string): Promise<ChatProjectBinding[]> {
    const qs = new URLSearchParams();
    if (projectId) qs.set("project_id", projectId);
    const suffix = qs.toString() ? `?${qs}` : "";
    // ``no-store``: this is read right after linking, unlinking or dissolving
    // a group, and a browser-cached copy would show the state before the
    // change — indistinguishable from "the panel never refreshed".
    return fetchJson(`/v1/channels/chat-bindings${suffix}`, {
      baseUrl: chatBase(projectId),
      cache: "no-store",
    });
  },

  bindChatToProject(payload: {
    external_chat_id: string;
    project_id: string;
    channel_instance_id?: string;
    external_chat_name?: string | null;
    default_agent_slug?: string | null;
  }): Promise<ChatProjectBinding> {
    return fetchJson("/v1/channels/chat-bindings", {
      baseUrl: chatBase(payload.project_id),
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel_instance_id: payload.channel_instance_id ?? "feishu-main",
        external_chat_id: payload.external_chat_id,
        project_id: payload.project_id,
        external_chat_name: payload.external_chat_name ?? null,
        default_agent_slug: payload.default_agent_slug ?? null,
      }),
    });
  },

  async unbindChat(
    externalChatId: string,
    channelInstanceId = "feishu-main",
    projectId?: string,
  ): Promise<void> {
    const qs = new URLSearchParams({
      external_chat_id: externalChatId,
      channel_instance_id: channelInstanceId,
    });
    await fetchJson(`/v1/channels/chat-bindings?${qs}`, {
      baseUrl: chatBase(projectId),
      method: "DELETE",
    });
  },

  testFeishuBinding(agentSlug: string): Promise<FeishuBindingTestResult> {
    return fetchJson(
      `/v1/channels/feishu/bindings/${encodeURIComponent(agentSlug)}/test`,
      { method: "POST" },
    );
  },
};
