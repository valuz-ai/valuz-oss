import { afterEach, describe, expect, it, vi } from "vitest";

import { docsApi, setApiBase } from "./docs-api";

afterEach(() => {
  vi.unstubAllGlobals();
  setApiBase("http://localhost:8000");
});

describe("docsApi.search", () => {
  it("sends KB selection independently from folder/document narrowing", async () => {
    setApiBase("http://api.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ hits: [] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await docsApi.search({
      query: "marker", project_id: "", knowledge_base_ids: ["kb-1"],
      folder_ids: ["folder-1"], document_ids: ["doc-1"],
    });
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/v1/docs/search");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      query: "marker", project_id: "", top_k: 5, knowledge_base_ids: ["kb-1"],
      folder_ids: ["folder-1"], document_ids: ["doc-1"],
    });
  });

  it("retains omitted scope and serializes explicit empty selection", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ hits: [] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await docsApi.search({ query: "marker", project_id: "project-1" });
    await docsApi.search({ query: "marker", project_id: "", knowledge_base_ids: [] });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      query: "marker", project_id: "project-1", top_k: 5,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
      query: "marker", project_id: "", top_k: 5, knowledge_base_ids: [],
    });
  });
});
