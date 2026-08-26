import { afterEach, describe, expect, it, vi } from "vitest";

import {
  documentResearchApi,
  setDocumentResearchApiBase,
} from "./document-research-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("documentResearchApi", () => {
  it("creates a locked child session with origin identities only", async () => {
    setDocumentResearchApiBase("http://api.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: "research-1",
          purpose: "document-research",
          document_ids: ["doc-1"],
          document_versions: ["sha256:abc"],
          source_scope: "locked",
          origin_session_id: "session-1",
          origin_message_id: "message-1",
          reused: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await documentResearchApi.getOrCreateSession("doc/1", {
      originSessionId: "session-1",
      originMessageId: "message-1",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/v1/document-research/sessions");
    expect(JSON.parse(String(init.body))).toEqual({
      document_id: "doc/1",
      origin_session_id: "session-1",
      origin_message_id: "message-1",
    });
  });

  it("uses the versioned summary endpoints", async () => {
    setDocumentResearchApiBase("http://api.test");
    const fetchMock = vi
      .fn()
      .mockImplementation(async () =>
        new Response("null", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await documentResearchApi.getSummary("doc 1", "detailed");
    await documentResearchApi.generateSummary("doc 1", {
      profile: "brief",
      force: true,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/v1/document-research/documents/doc%201/summary?profile=detailed",
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://api.test/v1/document-research/documents/doc%201/summary",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
      profile: "brief",
      force: true,
      origin_session_id: null,
      origin_message_id: null,
    });
  });

  it("shares only stored message identities, never client-authored citations", async () => {
    setDocumentResearchApiBase("http://api.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          target_session_id: "origin-1",
          message_id: "imported-1",
          source_session_id: "research-1",
          source_message_id: "message-1",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await documentResearchApi.shareToOrigin("research-1", "message-1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/v1/document-research/share");
    expect(JSON.parse(String(init.body))).toEqual({
      research_session_id: "research-1",
      source_message_id: "message-1",
    });
  });
});
