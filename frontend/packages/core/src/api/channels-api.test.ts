import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setApiBaseResolver } from "./base-resolver";
import {
  channelsApi,
  setChannelsApiBase,
  type FeishuBinding,
  type WeComAIBotBinding,
} from "./channels-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const binding: WeComAIBotBinding = {
  enabled: true,
  channel_instance_id: "wecom-aibot-main",
  owner_user_id: "u1",
  agent_slug: "developer",
  bot_id: "bot-1",
  has_secret: true,
  connected: false,
  connection_status: "stopped",
  connection_error: null,
};

const feishuBinding: FeishuBinding = {
  enabled: true,
  channel_instance_id: "feishu-main",
  owner_user_id: "u1",
  agent_slug: "developer",
  app_id: "cli_app_1",
  has_app_secret: true,
  has_verification_token: true,
  has_encrypt_key: true,
  connected: false,
  connection_status: "stopped",
  connection_error: null,
};

describe("channelsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setChannelsApiBase("http://api.test");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the WeCom AIBot local binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(binding));

    await expect(channelsApi.getWeComAIBotBinding("developer")).resolves.toEqual(binding);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/wecom-aibot/bindings/developer",
    );
  });

  it("does not send an empty secret when saving a binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(binding));

    await channelsApi.updateWeComAIBotBinding({
      enabled: true,
      agent_slug: "developer",
      bot_id: "bot-1",
      secret: "",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/wecom-aibot/bindings/developer",
    );
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      enabled: true,
      agent_slug: "developer",
      bot_id: "bot-1",
    });
  });

  it("loads the Feishu binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(feishuBinding));

    await expect(channelsApi.getFeishuBinding("developer")).resolves.toEqual(
      feishuBinding,
    );

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/feishu/bindings/developer",
    );
  });

  it("does not send an empty Feishu app secret when saving a binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(feishuBinding));

    await channelsApi.updateFeishuBinding({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
      app_secret: "",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/feishu/bindings/developer",
    );
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
    });
  });

  it("sends the Feishu app secret when provided", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(feishuBinding));

    await channelsApi.updateFeishuBinding({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
      app_secret: " app-secret ",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
      app_secret: "app-secret",
    });
  });
  it("probes a Feishu binding via the test endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        credential_ok: true,
        error: null,
        connected: false,
        connection_status: "stopped",
        connection_error: null,
      }),
    );

    const result = await channelsApi.testFeishuBinding("developer");

    expect(result.credential_ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/v1/channels/feishu/bindings/developer/test");
    expect(init?.method).toBe("POST");
  });
});

describe("channelsApi project routing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setChannelsApiBase("http://api.test");
    // A chat binding lives on the backend that owns its project.
    setApiBaseResolver((ref) =>
      ref.projectId === "cloud-project" ? "http://cloud.test" : undefined,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setApiBaseResolver(null);
  });

  it("reads a cloud project's bindings from the cloud backend", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse([]));

    await channelsApi.listChatBindings("cloud-project");

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("http://cloud.test/");
  });

  it("writes a cloud project's binding to the cloud backend", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({}));

    await channelsApi.bindChatToProject({
      external_chat_id: "chat-1",
      project_id: "cloud-project",
    });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("http://cloud.test/");
  });

  it("routes chat-scoped calls by the project passed alongside them", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({}));

    await channelsApi.unbindChat("chat-1", "feishu-main", "cloud-project");

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("http://cloud.test/");
  });

  it("stays on the module base for a local project", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse([]));

    await channelsApi.listChatBindings("local-project");

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("http://api.test/");
  });
});

