import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { projectsApi, setProjectsApiBase } from "./projects-api";
import { providersApi, setProvidersApiBase } from "./providers-api";
import { clearRequestCacheForTests } from "./request";
import { runtimesApi, setRuntimesApiBase } from "./runtimes-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API request caching", () => {
  beforeEach(() => {
    clearRequestCacheForTests();
    vi.restoreAllMocks();
    setRuntimesApiBase("http://api.test");
    setProjectsApiBase("http://api.test");
    setProvidersApiBase("http://api.test");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    clearRequestCacheForTests();
  });

  it("caches frequent runtime list reads", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        jsonResponse({
          runtimes: [
            {
              id: "codex",
              display_name: "Codex",
              supported_protocols: ["openai-response"],
              requires_binary: "codex",
              available: true,
              unavailable_reason: null,
            },
          ],
        }),
      ),
    );

    await runtimesApi.list();
    await runtimesApi.list();

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("invalidates cached project reads after project mutations", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse({
          projects: [
            {
              id: "p1",
              name: "Before",
              kind: "project",
              root_path: null,
              icon: null,
              cwd: null,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: "p2",
          name: "After",
          kind: "project",
          root_path: null,
          icon: null,
          cwd: null,
          instructions_md: null,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          projects: [
            {
              id: "p2",
              name: "After",
              kind: "project",
              root_path: null,
              icon: null,
              cwd: null,
            },
          ],
        }),
      );

    await projectsApi.list();
    await projectsApi.list();
    await projectsApi.create({ name: "After" });
    const fresh = await projectsApi.list();

    expect(fresh.projects[0]?.id).toBe("p2");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("invalidates cached provider reads after provider mutations", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ providers: [] }))
      .mockResolvedValueOnce(
        jsonResponse({
          id: "provider-1",
          name: "Provider",
          provider_kind: "compatible",
          source: "user",
          enabled: true,
          is_default: false,
          auth_type: "api_key",
          base_url: null,
          default_model: null,
          protocol: null,
          models: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          providers: [
            {
              id: "provider-1",
              name: "Provider",
              provider_kind: "compatible",
              source: "user",
              enabled: true,
              is_default: false,
              auth_type: "api_key",
              base_url: null,
              default_model: null,
              protocol: null,
              models: [],
            },
          ],
        }),
      );

    await providersApi.list();
    await providersApi.list();
    await providersApi.create({
      name: "Provider",
      provider_kind: "compatible",
    });
    const fresh = await providersApi.list();

    expect(fresh.providers).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
