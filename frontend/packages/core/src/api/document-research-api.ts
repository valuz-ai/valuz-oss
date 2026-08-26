import type {
  DocumentResearchSessionV1,
  DocumentSummaryArtifactV1,
  SharedResearchMessageV1,
} from "@valuz/shared";

import { createFetchJson, type RequestOptions } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setDocumentResearchApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

export interface DocumentResearchOrigin {
  originSessionId?: string | null;
  originMessageId?: string | null;
}

const originWire = (origin: DocumentResearchOrigin) => ({
  origin_session_id: origin.originSessionId ?? null,
  origin_message_id: origin.originMessageId ?? null,
});

export const documentResearchApi = {
  getSummary(
    documentId: string,
    profile: "brief" | "detailed" = "brief",
    options: Pick<RequestOptions, "signal"> = {},
  ): Promise<DocumentSummaryArtifactV1 | null> {
    const query = new URLSearchParams({ profile });
    return fetchJson(
      `/v1/document-research/documents/${encodeURIComponent(documentId)}/summary?${query}`,
      options,
    );
  },

  generateSummary(
    documentId: string,
    input: DocumentResearchOrigin & {
      profile?: "brief" | "detailed";
      force?: boolean;
    } = {},
    options: Pick<RequestOptions, "signal"> = {},
  ): Promise<DocumentSummaryArtifactV1> {
    return fetchJson(
      `/v1/document-research/documents/${encodeURIComponent(documentId)}/summary`,
      {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: input.profile ?? "brief",
          force: input.force ?? false,
          ...originWire(input),
        }),
      },
    );
  },

  getOrCreateSession(
    documentId: string,
    origin: DocumentResearchOrigin = {},
    options: Pick<RequestOptions, "signal"> = {},
  ): Promise<DocumentResearchSessionV1> {
    return fetchJson("/v1/document-research/sessions", {
      ...options,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: documentId,
        ...originWire(origin),
      }),
    });
  },

  shareToOrigin(
    researchSessionId: string,
    sourceMessageId?: string | null,
  ): Promise<SharedResearchMessageV1> {
    return fetchJson("/v1/document-research/share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        research_session_id: researchSessionId,
        source_message_id: sourceMessageId ?? null,
      }),
    });
  },
};
