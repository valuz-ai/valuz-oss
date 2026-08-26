import { describe, expect, it } from "vitest";
import type { CitationBundleV1 } from "@valuz/shared";

import {
  citationDisplayOrder,
  projectCitationSidecarAnchors,
  projectEvidenceMarkdownLinks,
} from "./CitationInline";

const source = {
  sourceId: "doc:1",
  providerId: "docs",
  sourceType: "document" as const,
  title: "Annual report",
  retrievedAt: "2026-08-05T00:00:00Z",
};

describe("citation sidecar projection", () => {
  it("never resolves an address to a sibling field's citation", () => {
    // A collection holds many fields. Falling back to the bare handle put
    // MA250's value behind MA20's citation — a confident, unrelated number,
    // which is worse than no marker at all.
    const content =
      "MA250 $125.83 [source](evidence://evc_mcp_ind_1234#/datas/3/factor_value)";
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_ma20",
          source,
          evidence: {
            kind: "text",
            quote: "factor value: 199.56",
            snippet: "factor value: 199.56",
            capturedAt: "2026-08-09T00:00:00Z",
          },
        },
      ],
      projection: {
        // Only MA20's address and the bare collection handle are known.
        evidenceHandleToCitationId: {
          "evc_mcp_ind_1234#/datas/0/factor_value": "cit_ma20",
          evc_mcp_ind_1234: "cit_ma20",
        },
        anchors: [],
      },
    };

    expect(projectEvidenceMarkdownLinks(content, bundle).trimEnd()).toBe(
      "MA250 $125.83",
    );
  });

  it("puts a drifted anchor behind the statement, not inside it", () => {
    // Offsets are measured against the guard's normalised text, so they arrive
    // shifted. One landing mid-parenthetical used to render as
    // "（ ⊘ 如 $X.XX）？" — the marker annotating the words after it instead of
    // the statement it belongs to.
    const content = "止损价格是多少（如 $X.XX）？";
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_stop",
          source,
          evidence: {
            kind: "text",
            quote: content,
            snippet: content,
            capturedAt: "2026-08-05T00:00:00Z",
          },
        },
      ],
      projection: {
        evidenceHandleToCitationId: {},
        anchors: [
          {
            citationId: "cit_stop",
            claimId: "clm_stop",
            origin: "auto-bound",
            sourceOffset: content.indexOf("如"),
            location: {
              kind: "text",
              blockIndex: 0,
              start: 0,
              end: content.length,
            },
          },
        ],
      },
    };

    expect(projectCitationSidecarAnchors(content, bundle)).toBe(
      "止损价格是多少（如 $X.XX） [source](citation://cit_stop)？",
    );
  });

  it("inserts a trusted auto-bound anchor at the raw Markdown offset", () => {
    const content = "Revenue increased 18%.";
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_revenue",
          source,
          evidence: {
            kind: "text",
            quote: "Revenue increased 18%.",
            snippet: "Revenue increased 18%.",
            capturedAt: "2026-08-05T00:00:00Z",
          },
        },
      ],
      projection: {
        evidenceHandleToCitationId: {},
        anchors: [
          {
            citationId: "cit_revenue",
            claimId: "clm_revenue",
            origin: "auto-bound",
            sourceOffset: content.indexOf("."),
            location: {
              kind: "text",
              blockIndex: 0,
              start: 0,
              end: content.indexOf("."),
            },
          },
        ],
      },
    };

    expect(projectCitationSidecarAnchors(content, bundle)).toBe(
      "Revenue increased 18% [source](citation://cit_revenue).",
    );
    expect(content).toBe("Revenue increased 18%.");
  });

  it("uses one terminal marker for a multi-cell provenance region", () => {
    const content = "| Company | 2024 |\n| --- | ---: |\n| A | 10 |\n| B | 20 |";
    const sourceOffset = content.lastIndexOf("20") + 2;
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_2024",
          source,
          evidence: {
            kind: "text",
            quote: "A 10; B 20",
            snippet: "A 10; B 20",
            capturedAt: "2026-08-05T00:00:00Z",
          },
        },
      ],
      projection: {
        evidenceHandleToCitationId: {},
        provenanceRegions: [
          {
            regionId: "prv_2024",
            blockIndex: 0,
            rowStart: 0,
            rowEnd: 1,
            columnStart: 1,
            columnEnd: 1,
            citationIds: ["cit_2024"],
            sourceOffset,
            anchor: {
              kind: "table-cell",
              blockIndex: 0,
              rowIndex: 1,
              columnIndex: 1,
            },
          },
        ],
      },
    };

    const projected = projectCitationSidecarAnchors(content, bundle);
    expect(projected.match(/citation:\/\/cit_2024/gu)).toHaveLength(1);
    expect(projected).toContain("B | 20 [source](citation://cit_2024) |");
  });

  it("keeps repeated citations for adjacent claims", () => {
    const content = "Revenue was 10. Profit was 2.";
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_financials",
          source,
          evidence: {
            kind: "text",
            quote: "Revenue was 10. Profit was 2.",
            snippet: "Revenue was 10. Profit was 2.",
            capturedAt: "2026-08-05T00:00:00Z",
          },
        },
      ],
      projection: {
        evidenceHandleToCitationId: {},
        anchors: [
          {
            citationId: "cit_financials",
            claimId: "clm_revenue",
            origin: "auto-bound",
            sourceOffset: content.indexOf("."),
            location: { kind: "text", blockIndex: 0, start: 0, end: 14 },
          },
          {
            citationId: "cit_financials",
            claimId: "clm_profit",
            origin: "auto-bound",
            sourceOffset: content.lastIndexOf("."),
            location: { kind: "text", blockIndex: 1, start: 16, end: 28 },
          },
        ],
      },
    };

    expect(
      projectCitationSidecarAnchors(content, bundle).match(
        /citation:\/\/cit_financials/gu,
      ),
    ).toHaveLength(2);
  });

  it("does not allocate a source number to calculation derivations", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_calculation",
          source: {
            ...source,
            sourceId: "calculation:1",
            sourceType: "tool-result",
            title: "Growth calculation",
          },
          evidence: {
            kind: "calculation",
            expression: "(current / prior) - 1",
            inputs: [],
            result: 0.2,
            unit: "%",
            calculatedAt: "2026-08-05T00:00:00Z",
          },
        },
        {
          citationId: "cit_revenue",
          source,
          evidence: {
            kind: "text",
            quote: "Revenue was 120.",
            snippet: "Revenue was 120.",
            capturedAt: "2026-08-05T00:00:00Z",
          },
        },
      ],
    };

    expect(
      Array.from(
        citationDisplayOrder(
          "Growth [calc](citation://cit_calculation), revenue [source](citation://cit_revenue).",
          bundle,
        ).entries(),
      ),
    ).toEqual([["cit_revenue", 1]]);
  });
});
