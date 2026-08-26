import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setApiBaseResolver } from "./base-resolver";
import { filesApi, setFilesApiBase } from "./files-api";

const LOCAL = "http://local.test";
const CLOUD = "http://cloud.test";
const REF = "valuz-file:///data/valuz_data/workspace/u1/p1/sources/a.json";

function emptyResults(): Response {
  return new Response(JSON.stringify({ results: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("filesApi.resolve", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setFilesApiBase(LOCAL);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setApiBaseResolver(null);
  });

  it("uses the module base when no resolver is registered (OSS single-backend)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(emptyResults());

    await filesApi.resolve([REF], { baseRef: { projectId: "p1" } });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${LOCAL}/v1/files/resolve`,
    );
  });

  it("routes to the backend that owns the entity", async () => {
    setApiBaseResolver((ref) =>
      ref.projectId === "cloud-project" ? CLOUD : undefined,
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(emptyResults());

    await filesApi.resolve([REF], { baseRef: { projectId: "cloud-project" } });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${CLOUD}/v1/files/resolve`,
    );
  });

  it("hands the resolver the whole ref", async () => {
    const resolver = vi.fn(() => undefined);
    setApiBaseResolver(resolver);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(emptyResults());

    await filesApi.resolveOne(REF, {
      baseRef: { sessionId: "s1", projectId: "p1" },
    });

    expect(resolver).toHaveBeenCalledWith({ sessionId: "s1", projectId: "p1" });
  });

  it("keeps the module base for an unscoped call", async () => {
    // The registered resolver has no opinion without an entity id (this is what
    // the commercial one does), so an unscoped resolve stays on the default.
    setApiBaseResolver((ref) => (ref.projectId ? CLOUD : undefined));
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(emptyResults());

    await filesApi.resolve([REF]);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${LOCAL}/v1/files/resolve`,
    );
  });

  it("does not leak baseRef into the request body or init", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(emptyResults());

    await filesApi.resolve([REF], { baseRef: { projectId: "p1" } });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ refs: [REF] });
    expect(init).not.toHaveProperty("baseRef");
  });
});
