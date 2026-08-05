/** @vitest-environment jsdom */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "./MarkdownContent";
import type { CitationBundleV1 } from "@valuz/shared";

const CITATIONS: CitationBundleV1 = {
  version: 1,
  citations: [
    {
      citationId: "cit_first",
      source: {
        sourceId: "doc:1",
        providerId: "docs",
        documentId: "1",
        sourceType: "document",
        title: "Annual report",
        organization: "Example Corp",
        publishedAt: "2026-03-20T00:00:00Z",
        retrievedAt: "2026-07-30T08:00:00Z",
      },
      evidence: {
        kind: "text",
        quote: "Revenue increased 18%.",
        snippet: "For the year, revenue increased 18%.",
        capturedAt: "2026-07-30T08:00:00Z",
      },
    },
    {
      citationId: "cit_second",
      source: {
        sourceId: "doc:2",
        providerId: "docs",
        sourceType: "document",
        title: "Earnings release",
        retrievedAt: "2026-07-30T08:00:00Z",
      },
      evidence: {
        kind: "text",
        quote: "Margin expanded.",
        snippet: "Gross margin expanded.",
        capturedAt: "2026-07-30T08:00:00Z",
      },
    },
  ],
};

it("projects a post-publish evidence link from sidecar metadata", () => {
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [CITATIONS.citations[0]!],
    projection: {
      evidenceHandleToCitationId: {
        ev_revenue_12345678: "cit_first",
      },
    },
  };
  const { container } = render(
    <MarkdownContent
      content="Revenue increased [source](evidence://ev_revenue_12345678)."
      citationBundle={bundle}
    />,
  );

  expect(container.querySelector('[data-citation-id="cit_first"]')).not.toBeNull();
  expect(container.textContent).not.toContain("evidence://");
});

describe("MarkdownContent local file links", () => {
  it("routes local file hrefs through the provided handler", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Open report](/Users/ada/project/report.md:12)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("/Users/")}
      />,
    );

    fireEvent.click(screen.getByRole("link", { name: "Open report" }));

    expect(onLocalFileLinkClick).toHaveBeenCalledWith(
      "/Users/ada/project/report.md:12",
    );
  });

  it("renders file protocol local links without Streamdown blocking", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Open HTML](file:///Users/ada/Downloads/ai-crm/index.html)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("file:///Users/")}
      />,
    );

    const link = screen.getByRole("link", { name: "Open HTML" });
    expect(link.getAttribute("href")).toBe(
      "file:///Users/ada/Downloads/ai-crm/index.html",
    );
    expect(screen.queryByText("[blocked]")).toBeNull();
  });

  it("leaves non-local hrefs on the normal markdown link path", () => {
    const onLocalFileLinkClick = vi.fn();

    render(
      <MarkdownContent
        content="[Settings](/settings)"
        onLocalFileLinkClick={onLocalFileLinkClick}
        isLocalFileHref={(href) => href.startsWith("/Users/")}
      />,
    );

    fireEvent.click(screen.getByRole("link", { name: "Settings" }));

    expect(onLocalFileLinkClick).not.toHaveBeenCalled();
  });
});

describe("MarkdownContent citations", () => {
  it("does not show leaked source protocol placeholders from stored answers", () => {
    render(
      <MarkdownContent
        content={
          "管理层预计容量增长 80%。source\n\n" +
          "The primary source is the annual report."
        }
      />,
    );

    expect(document.body.textContent).toContain("管理层预计容量增长 80%。");
    expect(document.body.textContent).not.toContain("80%。source");
    expect(document.body.textContent).toContain(
      "The primary source is the annual report.",
    );
  });

  it("removes citation-only lines that cannot be associated with a claim", () => {
    render(
      <MarkdownContent
        content={
          "| Product | Revenue |\n" +
          "| --- | --- |\n" +
          "| Moutai | 1,459.28 [source](citation://cit_first) |\n\n" +
          "[source](citation://cit_second)\n\n---\n\n" +
          "Next section."
        }
        citationBundle={CITATIONS}
      />,
    );

    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 1/i }),
    ).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: /(?:citation|引用) 2/i }),
    ).toBeNull();
    expect(document.body.textContent).toContain("Next section.");
  });

  it("removes citations attached only to decorative section headings", () => {
    render(
      <MarkdownContent
        content={
          "**By product**[source](citation://cit_second)\n\n" +
          "Revenue 120 USD [source](citation://cit_first)."
        }
        citationBundle={CITATIONS}
      />,
    );

    expect(document.body.textContent).toContain("By product");
    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 1/i }),
    ).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: /(?:citation|引用) 2/i }),
    ).toBeNull();
  });

  it("numbers citations by first appearance and reuses duplicate numbers", () => {
    render(
      <MarkdownContent
        content={
          "First [source](citation://cit_second), then [source](citation://cit_first), again [source](citation://cit_second)."
        }
        citationBundle={CITATIONS}
      />,
    );

    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 1/i }),
    ).toHaveLength(2);
    expect(
      screen.getAllByRole("button", { name: /(?:citation|引用) 2/i }),
    ).toHaveLength(1);
    expect(
      screen
        .getAllByRole("button", { name: /(?:citation|引用) 1/i })
        .every((pill) => pill.textContent === "1"),
    ).toBe(true);
  });

  it("renders citation numbers without visual brackets in pills, hover cards, and sources", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
      />,
    );

    const pill = screen.getByRole("button", {
      name: /(?:citation|引用) 1/i,
    });
    expect(pill.textContent).toBe("1");

    fireEvent.mouseEnter(pill);
    expect(screen.getByText("1 Annual report")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: /^1Annual report$/i }),
    ).not.toBeNull();
  });

  it("uses neutral circular inline controls and stacks sources one per row", () => {
    render(
      <MarkdownContent
        content={
          "Revenue [source](citation://cit_first), margin [source](citation://cit_second)."
        }
        citationBundle={CITATIONS}
      />,
    );

    const pill = screen.getByRole("button", {
      name: /(?:citation|引用) 1/i,
    });
    expect(pill.className).toContain("h-4");
    expect(pill.className).toContain("w-4");
    expect(pill.className).toContain("rounded-full");
    expect(pill.className).toContain("bg-surface-muted");
    expect(pill.parentElement?.className).toContain("align-middle");
    expect(pill.parentElement?.className).toContain("-top-px");
    expect(pill.parentElement?.className).toContain("mx-0.5");

    const firstSource = screen.getByRole("button", {
      name: /^1Annual report$/i,
    });
    const secondSource = screen.getByRole("button", {
      name: /^2Earnings release$/i,
    });
    expect(firstSource.parentElement).toBe(secondSource.parentElement);
    expect(firstSource.parentElement?.className).toContain("flex-col");
    expect(firstSource.className).toContain("w-full");
    expect(secondSource.className).toContain("w-full");
    expect(firstSource.className).not.toContain("border");
    expect(secondSource.className).not.toContain("border");
  });

  it("uses a smaller centered font for multi-digit citation pills", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
        citationDisplayOrderOverride={new Map([["cit_first", 15]])}
      />,
    );

    const pills = screen.getAllByRole("button", {
      name: /(?:citation|引用) 15/i,
    });
    expect(pills).toHaveLength(1);
    expect(pills[0]?.textContent).toBe("15");
    const label = pills[0]?.querySelector("span");
    expect(label?.className).toContain("text-micro");
    expect(label?.className).toContain("justify-center");
    expect(label?.style.transform).toBe("translateX(-0.5px) scale(0.9)");
  });

  it("groups chunk citations from the same document into one source row", () => {
    const sameDocumentBundle: CitationBundleV1 = {
      ...CITATIONS,
      citations: [
        CITATIONS.citations[0]!,
        {
          ...CITATIONS.citations[1]!,
          source: {
            ...CITATIONS.citations[0]!.source,
          },
        },
      ],
    };

    render(
      <MarkdownContent
        content={
          "Revenue [source](citation://cit_first), margin [source](citation://cit_second)."
        }
        citationBundle={sameDocumentBundle}
      />,
    );

    expect(
      screen.getByRole("button", { name: /(?:citation|引用) 2/i }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /^1–2Annual report$/i }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /^2Annual report$/i }),
    ).toBeNull();
  });

  it("shows the evidence snapshot on hover without fetching", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(screen.getByText("Annual report")).not.toBeNull();
    expect(screen.getByText("Revenue increased 18%.")).not.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("shows full text evidence without the three-line clamp", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    const evidence = document.querySelector("[data-citation-evidence-text]");
    expect(evidence).not.toBeNull();
    expect(evidence?.className).toContain("max-h-64");
    expect(evidence?.className).toContain("overflow-auto");
    expect(screen.getByText("For the year, revenue increased 18%.").className).not.toContain(
      "line-clamp-3",
    );
  });

  it("does not repeat a snippet that only normalizes quote whitespace", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          ...CITATIONS.citations[0],
          evidence: {
            kind: "text",
            quote: "Revenue increased 18%.\nNet income also grew.",
            snippet: "Revenue increased 18%. Net income also grew.",
            capturedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(
      screen.getAllByText(/Revenue increased 18%.*Net income also grew/s),
    ).toHaveLength(1);
  });

  it("does not repeat a truncated table snippet already contained by the quote", () => {
    const snippet =
      "| Product | Revenue | | --- | --- | | Moutai | 145,928 | | Series | 24,683 | 增加 0...";
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          ...CITATIONS.citations[0],
          evidence: {
            kind: "text",
            quote: [
              "| Product | Revenue |",
              "| --- | --- |",
              "| Moutai | 145,928 |",
              "| Series | 24,683 | 增加 0.11 |",
              "| Direct | 74,843 |",
            ].join("\n"),
            snippet,
            capturedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(screen.queryByText(snippet)).toBeNull();
    expect(
      document.querySelectorAll("[data-citation-evidence-text]"),
    ).toHaveLength(1);
    expect(screen.getByRole("table").className).toContain("text-[11px]");
    expect(
      screen.getByRole("cell", { name: "145,928" }).className,
    ).toContain("px-2");
    expect(screen.getByRole("tooltip").className).toContain(
      "w-[min(680px,calc(100vw-32px))]",
    );
  });

  it("spans extracted table section labels across the compact table", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          ...CITATIONS.citations[0],
          evidence: {
            kind: "text",
            quote: [
              "| Product data | |",
              "| --- | --- |",
              "| Product | Revenue |",
              "| Moutai | 145,928 |",
            ].join("\n"),
            snippet: "Product data: Moutai revenue 145,928.",
            capturedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    const sectionRow = document.querySelector(
      "[data-citation-table-section]",
    );
    expect(sectionRow).not.toBeNull();
    expect(sectionRow?.querySelector("th")?.colSpan).toBe(2);
    expect(sectionRow?.textContent).toBe("Product data");
  });

  it("shows readable structured evidence without internal record metadata", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_structured",
          source: {
            sourceId: "income:600519",
            providerId: "valuz-stock",
            sourceType: "dataset",
            title: "Company income statement · 600519",
            retrievedAt: "2026-07-31T03:41:38Z",
          },
          evidence: {
            kind: "structured-data",
            datasetId: "reportify-financial-income-statement",
            toolName: "company_income_statement",
            recordKey: "600519|2024 FY|2024-12-31",
            field: "total_revenue.total_revenue",
            value: 174144069958,
            unit: "CNY",
            period: "2024 FY",
            asOf: "2024-12-31",
            capturedAt: "2026-07-31T03:41:38Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_structured)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    const evidence = document.querySelector("[data-citation-evidence-section]");
    expect(evidence).not.toBeNull();
    expect(evidence?.textContent).toMatch(/cited content|引用内容/i);
    expect(evidence?.textContent).toContain(
      "total revenue: 174144069958 CNY",
    );
    const tooltipText = screen.getByRole("tooltip").textContent ?? "";
    expect(tooltipText).not.toContain("record ·");
    expect(tooltipText).not.toContain("dataset ·");
    expect(tooltipText).not.toContain("tool ·");
  });

  it("explains a structured field mismatch once without cascading validator errors", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          citationId: "cit_revenue",
          source: {
            sourceId: "reportify-financial-income-statement:600519",
            providerId: "valuz-stock",
            sourceType: "dataset",
            title: "Company income statement · 600519",
            retrievedAt: "2026-08-01T07:26:44Z",
          },
          evidence: {
            kind: "structured-data",
            datasetId: "reportify-financial-income-statement",
            toolName: "company_income_statement",
            field: "total_comprehensive_income",
            value: 89330873529,
            period: "2024 FY",
            asOf: "2024-12-31",
            capturedAt: "2026-08-01T07:26:44Z",
          },
        },
      ],
      quality: {
        policyId: "finance",
        policyRevision: "v1",
        mode: "strict-domain",
        status: "unverified",
        publishStatus: "draft-only",
        layers: { L1: "degraded", L4: "degraded" },
        issues: [
          {
            code: "numeric_unit_missing",
            layer: "L1",
            severity: "degraded",
            citationIds: ["cit_revenue"],
          },
          {
            code: "structured_value_not_present_in_answer",
            layer: "L4",
            severity: "degraded",
            citationIds: ["cit_revenue"],
          },
          {
            code: "claim_evidence_mismatch",
            layer: "L4",
            severity: "unverified",
            citationIds: ["cit_revenue"],
            claimId: "clm_revenue",
            claim: {
              exact: "Revenue was 170899152276 CNY.",
            },
          },
        ],
        claims: [
          {
            claimId: "clm_revenue",
            exact: "Revenue was 170899152276 CNY.",
            segmentIndex: 0,
            citationRequired: true,
            citationIds: ["cit_revenue"],
            status: "unverified",
            issueCodes: [
              "numeric_unit_missing",
              "structured_value_not_present_in_answer",
              "claim_evidence_mismatch",
            ],
          },
        ],
        metrics: {
          citationCount: 1,
          unsourcedClaimCount: 0,
          unverifiedClaimCount: 1,
          tierCounts: {},
        },
      },
    };

    render(
      <MarkdownContent
        content={
          "Revenue was 170899152276 CNY [source](citation://cit_revenue)."
        }
        citationBundle={bundle}
        messageId="message-1"
        onCitationClick={vi.fn()}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    const quality = document.querySelector("[data-citation-quality-issues]");
    expect(quality?.textContent).toMatch(
      /total comprehensive income.*89330873529.*may not match|total comprehensive income.*89330873529.*可能不一致/i,
    );
    expect(quality?.textContent).not.toMatch(
      /no unit|未标明单位|numeric basis|数字或计算依据/i,
    );
    expect(screen.getByRole("button", { name: /view evidence|查看依据/i })).not.toBeNull();
  });

  it("keeps advisory verification failures as neutral citations with a light hover note", () => {
    render(
      <MarkdownContent
        content={
          "Revenue [source](citation://cit_first). Margin [source](citation://cit_second)."
        }
        citationBundle={{
          ...CITATIONS,
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "unverified",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "claim_evidence_mismatch",
                layer: "L4",
                severity: "unverified",
                citationIds: ["cit_first"],
              },
              {
                code: "claim_partially_supported",
                layer: "L4",
                severity: "unverified",
                citationIds: ["cit_second"],
              },
            ],
            metrics: {
              citationCount: 2,
              unsourcedClaimCount: 0,
              unverifiedClaimCount: 2,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    const pills = screen.getAllByRole("button", {
      name: /(?:citation|引用) [12]/i,
    });
    expect(pills).toHaveLength(2);
    for (const pill of pills) {
      expect(pill.getAttribute("data-citation-quality")).toBeNull();
      expect(pill.className).toContain("border-surface-border");
      expect(pill.parentElement?.className).toContain("mx-0.5");
    }

    fireEvent.mouseEnter(pills[0]!);
    const note = document.querySelector('[data-citation-quality-issues="advisory"]');
    expect(note?.textContent).toMatch(
      /check against the source|建议结合原文确认/i,
    );
    expect(note?.className).toContain("bg-surface-muted");
    expect(note?.className).not.toContain("bg-warning-light");
    const qualityIconWrapper = note?.querySelector("svg")?.parentElement;
    expect(qualityIconWrapper?.className).toContain("h-5");
    expect(qualityIconWrapper?.className).toContain("items-center");
  });

  it("explains cross-language paraphrases without calling them mismatches", () => {
    render(
      <MarkdownContent
        content="管理层表示需求持续增长 [source](citation://cit_first)。"
        citationBundle={{
          ...CITATIONS,
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "unverified",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "claim_translation_not_verified",
                layer: "L4",
                severity: "unverified",
                citationIds: ["cit_first"],
              },
            ],
            metrics: {
              citationCount: 1,
              unsourcedClaimCount: 0,
              unverifiedClaimCount: 1,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    const pill = screen.getByRole("button", { name: /(?:citation|引用) 1/i });
    expect(pill.getAttribute("data-citation-quality")).toBeNull();
    fireEvent.mouseEnter(pill);
    expect(
      document.querySelector('[data-citation-quality-issues="advisory"]')
        ?.textContent,
    ).toMatch(/another language|外文原文/i);
  });

  it("limits a claim issue to the matching occurrence when a citation is reused", () => {
    vi.useFakeTimers();
    try {
      const content =
        "Revenue was 100 USD [source](citation://cit_first).\n\nTest value was 23.5% [source](citation://cit_first).";
      const secondClaimStart = content.indexOf("Test value");

      render(
        <MarkdownContent
          content={content}
          citationBundle={{
            ...CITATIONS,
            quality: {
              policyId: "finance",
              policyRevision: "finance-citation-policy-v1",
              mode: "strict-domain",
              status: "unverified",
              publishStatus: "draft-only",
              layers: { L4: "degraded" },
              issues: [
                {
                  code: "claim_evidence_mismatch",
                  layer: "L4",
                  severity: "unverified",
                  citationIds: ["cit_first"],
                  claimId: "claim-second",
                  location: {
                    kind: "text",
                    blockIndex: 1,
                    start: 0,
                    end: content.length - secondClaimStart,
                    sourceStart: secondClaimStart,
                    sourceEnd: content.length,
                  },
                },
              ],
              claims: [
                {
                  claimId: "claim-second",
                  exact: "Test value was 23.5%",
                  segmentIndex: 1,
                  citationRequired: true,
                  citationIds: ["cit_first"],
                  status: "unverified",
                  issueCodes: ["claim_evidence_mismatch"],
                  location: {
                    kind: "text",
                    blockIndex: 1,
                    start: 0,
                    end: content.length - secondClaimStart,
                    sourceStart: secondClaimStart,
                    sourceEnd: content.length,
                  },
                },
              ],
              metrics: {
                citationCount: 1,
                unsourcedClaimCount: 0,
                unverifiedClaimCount: 1,
                tierCounts: {},
              },
            },
          }}
        />,
      );

      const pills = screen.getAllByRole("button", {
        name: /(?:citation|引用) 1/i,
      });
      expect(pills).toHaveLength(2);

      fireEvent.mouseEnter(pills[0]!);
      expect(
        document.querySelector('[data-citation-quality-issues="advisory"]'),
      ).toBeNull();

      fireEvent.mouseLeave(pills[0]!.parentElement as HTMLElement);
      act(() => vi.advanceTimersByTime(200));
      fireEvent.mouseEnter(pills[1]!);
      expect(
        document.querySelector('[data-citation-quality-issues="advisory"]'),
      ).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps confirmed conflicts as colored citation warnings", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={{
          ...CITATIONS,
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "unverified",
            publishStatus: "draft-only",
            layers: { L3: "degraded" },
            issues: [
              {
                code: "claim_evidence_conflict",
                layer: "L3",
                severity: "unverified",
                citationIds: ["cit_first"],
              },
            ],
            metrics: {
              citationCount: 1,
              unsourcedClaimCount: 0,
              unverifiedClaimCount: 1,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    const pill = screen.getByRole("button", {
      name: /(?:citation|引用) 1.*(?:needs review|需要核验)/i,
    });
    expect(pill.getAttribute("data-citation-quality")).toBe("critical");
    expect(pill.className).toContain("border-warning");

    fireEvent.mouseEnter(pill);
    expect(
      document.querySelector('[data-citation-quality-issues="critical"]'),
    ).not.toBeNull();
  });

  it("prefers opening below and stays open while the pointer enters the card", () => {
    vi.useFakeTimers();
    try {
      render(
        <MarkdownContent
          content={"Revenue [source](citation://cit_first)."}
          citationBundle={CITATIONS}
        />,
      );

      const pill = screen.getByRole("button", {
        name: /(?:citation|引用) 1/i,
      });
      fireEvent.mouseEnter(pill);

      const card = screen.getByRole("tooltip");
      expect(card.getAttribute("data-side")).toBe("bottom");

      fireEvent.mouseLeave(pill.parentElement as HTMLElement);
      fireEvent.mouseEnter(card);
      act(() => vi.advanceTimersByTime(200));

      expect(screen.getByRole("tooltip")).toBe(card);
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens above when the card cannot fit below the citation", () => {
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        if (this.getAttribute("role") === "tooltip") {
          return {
            bottom: 950,
            height: 200,
            left: 0,
            right: 360,
            top: 750,
            width: 360,
            x: 0,
            y: 750,
            toJSON: () => ({}),
          };
        }
        if (this.getAttribute("aria-label")?.match(/(?:citation|引用) 1/i)) {
          return {
            bottom: 750,
            height: 16,
            left: 100,
            right: 116,
            top: 734,
            width: 16,
            x: 100,
            y: 734,
            toJSON: () => ({}),
          };
        }
        return {
          bottom: 0,
          height: 0,
          left: 0,
          right: 0,
          top: 0,
          width: 0,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        };
      });

    try {
      render(
        <MarkdownContent
          content={"Revenue [source](citation://cit_first)."}
          citationBundle={CITATIONS}
        />,
      );

      fireEvent.mouseEnter(
        screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
      );

      expect(screen.getByRole("tooltip").getAttribute("data-side")).toBe("top");
    } finally {
      rectSpy.mockRestore();
    }
  });

  it("keeps the hover action usable while keyboard focus moves into the card", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    const pill = screen.getByRole("button", { name: /(?:citation|引用) 1/i });
    fireEvent.focus(pill);
    const openSource = screen.getByRole("button", {
      name: /(?:open source|打开原文)/i,
    });
    fireEvent.blur(pill, { relatedTarget: openSource });
    fireEvent.focus(openSource);
    fireEvent.click(openSource);

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
  });

  it("renders an additive policy quality badge without changing citation identity", () => {
    const bundle: CitationBundleV1 = {
      ...CITATIONS,
      citations: [
        {
          ...CITATIONS.citations[0],
          annotations: {
            quality: {
              policyId: "domain-policy",
              policyRevision: "v1",
              tier: "P1",
              status: "passed",
              label: "P1",
            },
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    const badges = screen.getAllByText("P1");
    expect(badges).toHaveLength(2);
    expect(
      badges.every(
        (badge) => badge.getAttribute("data-citation-quality") === "passed",
      ),
    ).toBe(true);
  });

  it("opens the authoritative inputs from a calculation citation card", () => {
    const onCitationClick = vi.fn();
    const input = CITATIONS.citations[0];
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        input,
        {
          citationId: "cit_calculation",
          source: {
            sourceId: "calculation:1",
            providerId: "runtime",
            sourceType: "tool-result",
            title: "Growth calculation",
            retrievedAt: "2026-07-30T08:00:00Z",
          },
          evidence: {
            kind: "calculation",
            expression: "revenue / 100",
            inputs: [
              {
                name: "revenue",
                citationId: input.citationId,
                value: 118,
                unit: "USD million",
              },
            ],
            result: 1.18,
            unit: "x",
            calculatedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Growth [calc](citation://cit_calculation)."}
        citationBundle={bundle}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    const calculationPill = screen.getByRole("button", {
      name: /(?:citation|引用) 1/i,
    });
    fireEvent.click(calculationPill);
    expect(onCitationClick).not.toHaveBeenCalled();

    fireEvent.focus(calculationPill);
    fireEvent.click(screen.getByRole("button", { name: /revenue.*annual report/i }));

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });

    const calculationSource = document.querySelector(
      "[data-citation-calculation-source]",
    );
    expect(calculationSource).not.toBeNull();
    expect(calculationSource?.closest("button")).toBeNull();
    fireEvent.mouseEnter(calculationSource!);
    expect(screen.getAllByText(/revenue \/ 100 = 1\.18 x/i).length).toBeGreaterThan(0);
  });

  it("opens a known citation and degrades an unknown citation", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownContent
        content={
          "Known [source](citation://cit_first), unknown [source](citation://cit_missing)."
        }
        citationBundle={CITATIONS}
        messageId="msg-1"
        onCitationClick={onCitationClick}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
    const unavailable = screen.getByRole("button", {
      name: /(?:citation unavailable|引用不可用)/i,
    });
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
    expect(unavailable.textContent).toBe("2");
  });

  it("keeps body-derived numbering when the citation bundle is unavailable", () => {
    render(
      <MarkdownContent
        content="Source [report](citation://cit_from_newer_bundle)."
      />,
    );

    const unavailable = screen.getByRole("button", {
      name: /(?:citation unavailable|引用不可用)/i,
    });
    expect(unavailable.textContent).toBe("1");
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
  });

  it("leaves an unbound plain [1] as normal text", () => {
    render(<MarkdownContent content="Plain [1] text." citationBundle={CITATIONS} />);

    expect(screen.getByText(/Plain \[1\] text/)).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /(?:citation|引用)/i }),
    ).toBeNull();
  });

  it("does not surface a generic warning when citation integrity cannot be localized", () => {
    render(
      <MarkdownContent
        content="The answer could not bind a source."
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "degraded",
            unknownCitationIds: ["ev_missing"],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 1,
            policyRevision: "citation-v1",
          },
        }}
      />,
    );

    expect(
      document.querySelector('[data-citation-integrity="degraded"]'),
    ).toBeNull();
    expect(
      screen.queryByText(
        /some citations could not be located or verified|部分引用无法定位或验证/i,
      ),
    ).toBeNull();
  });

  it("marks only the cited claim when a quality issue has citation ids", () => {
    render(
      <MarkdownContent
        content="Revenue was 100 USD [source](citation://cit_first)."
        citationBundle={{
          ...CITATIONS,
          integrity: {
            status: "degraded",
            unknownCitationIds: ["cit_missing"],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "calculation_result_mismatch",
                layer: "L4",
                severity: "degraded",
                citationIds: ["cit_first"],
              },
              {
                code: "source_tier_unmatched",
                layer: "L2",
                severity: "degraded",
              },
            ],
            metrics: {
              citationCount: 1,
              unsourcedClaimCount: 0,
              unverifiedClaimCount: 0,
              tierCounts: { T1: 1 },
            },
          },
        }}
      />,
    );

    const pill = screen.getByRole("button", {
      name: /(?:citation|引用) 1.*(?:needs review|需要核验)/i,
    });
    expect(pill.getAttribute("data-citation-quality")).toBe("critical");
    expect(pill.parentElement?.className).toContain("mx-1");
    expect(
      document.querySelector("[data-citation-quality-warning]"),
    ).toBeNull();
    expect(
      document.querySelector('[data-citation-integrity="degraded"]'),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-quality-summary]"),
    ).toBeNull();

    fireEvent.mouseEnter(pill);
    const evidenceSection = document.querySelector(
      "[data-citation-evidence-section]",
    );
    const qualityIssues = document.querySelector(
      "[data-citation-quality-issues]",
    );
    expect(evidenceSection?.textContent).toMatch(/cited content|引用内容/i);
    expect(qualityIssues?.textContent).toMatch(
      /needs review|需要核验/i,
    );
    expect(qualityIssues?.textContent).toMatch(
      /number or calculation|数字或计算依据/i,
    );
    expect(
      evidenceSection?.compareDocumentPosition(qualityIssues!),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("does not add a scary marker to an uncited advisory claim", () => {
    render(
      <MarkdownContent
        content="Revenue was 100 USD [source](citation://cit_first). Margin was 23.5%."
        citationBundle={{
          ...CITATIONS,
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "degraded",
                claim: { exact: "Margin was 23.5%." },
              },
            ],
            metrics: {
              citationCount: 1,
              unsourcedClaimCount: 1,
              unverifiedClaimCount: 0,
              tierCounts: { T1: 1 },
            },
          },
        }}
      />,
    );

    expect(
      document.querySelector("[data-citation-claim-quality]"),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-quality-warning]"),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-quality-summary]"),
    ).toBeNull();
  });

  it("uses claim source offsets to mark repeated critical claims independently", () => {
    const content = "Metric repeated. Metric repeated.";
    const locations = [
      { kind: "text" as const, blockIndex: 0, start: 0, end: 16, sourceStart: 0, sourceEnd: 16 },
      { kind: "text" as const, blockIndex: 0, start: 17, end: 33, sourceStart: 17, sourceEnd: 33 },
    ];
    render(
      <MarkdownContent
        content={content}
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: locations.map((location, index) => ({
              code: "claim_evidence_conflict",
              layer: "L4",
              severity: "unverified",
              claimId: `clm_${index + 1}`,
              claim: { exact: "Metric repeated." },
              location,
            })),
            claims: locations.map((location, index) => ({
              claimId: `clm_${index + 1}`,
              exact: "Metric repeated.",
              segmentIndex: index,
              citationRequired: true,
              citationIds: [],
              status: "unsupported" as const,
              issueCodes: ["claim_evidence_conflict"],
              location,
            })),
            metrics: {
              citationCount: 0,
              unsourcedClaimCount: 2,
              unverifiedClaimCount: 0,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    expect(document.querySelectorAll("[data-citation-claim-quality]")).toHaveLength(2);
    expect(
      document.querySelector("[data-citation-claim-quality]")?.getAttribute("aria-label"),
    ).toMatch(/cross-check|conflict|inconsistent|交叉验证|冲突|不一致/i);
    expect(document.querySelector("[data-citation-quality-warning]")).toBeNull();
  });

  it("places a critical table-cell claim marker using its stable source location", () => {
    const content = "| Metric | 2024 |\n|---|---:|\n| Revenue | 120 USD |";
    const valueStart = content.indexOf("120 USD");
    const location = {
      kind: "table-cell" as const,
      blockIndex: 0,
      rowIndex: 0,
      columnIndex: 1,
      sourceStart: valueStart,
      sourceEnd: valueStart + "120 USD".length,
    };
    const { container } = render(
      <MarkdownContent
        content={content}
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "claim_evidence_conflict",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_table",
                claim: { exact: "Revenue — 2024: 120 USD" },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_table",
                exact: "Revenue — 2024: 120 USD",
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported",
                issueCodes: ["claim_evidence_conflict"],
                location,
              },
            ],
            metrics: {
              citationCount: 0,
              unsourcedClaimCount: 1,
              unverifiedClaimCount: 0,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    expect(document.querySelector("[data-citation-claim-quality]")).not.toBeNull();
    expect(screen.getByText("120 USD")).not.toBeNull();
    const richText = container.querySelector<HTMLElement>("#streamdown");
    expect(richText?.className).toContain(
      "div:not(:has([data-streamdown='table']))",
    );
    expect(richText?.className).not.toContain("div:has(button)");
    const wrapper = container.querySelector<HTMLElement>(
      "[data-streamdown='table-wrapper']",
    );
    const dataRegion = wrapper?.querySelector<HTMLElement>(
      ":scope > div:has([data-streamdown='table'])",
    );
    expect(dataRegion).not.toBeNull();
    expect(dataRegion?.querySelector("[data-citation-claim-quality]")).not.toBeNull();
    const toolbarRegions = wrapper?.querySelectorAll(
      ":scope > div:not(:has([data-streamdown='table']))",
    );
    expect(toolbarRegions).toHaveLength(1);
    expect(
      toolbarRegions?.[0]?.contains(
        dataRegion?.querySelector("[data-citation-claim-quality]") ?? null,
      ),
    ).toBe(false);
  });

  it("moves a critical claim marker outside display math without exposing its internal URL", () => {
    const content = "计算如下：\n\n$$\n增长率 = 15.71\\%\n$$";
    const mathValueEnd = content.indexOf("15.71") + "15.71".length;
    render(
      <MarkdownContent
        content={content}
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "claim_evidence_conflict",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_math",
                claim: { exact: "增长率 = 15.71%" },
                location: {
                  kind: "text",
                  blockIndex: 1,
                  start: 0,
                  end: 13,
                  sourceStart: content.indexOf("增长率"),
                  sourceEnd: mathValueEnd,
                },
              },
            ],
            metrics: {
              citationCount: 0,
              unsourcedClaimCount: 1,
              unverifiedClaimCount: 0,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    expect(document.body.textContent).not.toContain("valuz.quality-claim.invalid");
    expect(document.querySelector("[data-citation-claim-quality]")).not.toBeNull();
    expect(document.querySelector(".katex")).not.toBeNull();
  });

  it("hides quality issues that cannot be located to a concrete claim", () => {
    render(
      <MarkdownContent
        content="Revenue was 100 USD [source](citation://cit_first)."
        citationBundle={{
          ...CITATIONS,
          integrity: {
            status: "repaired",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 1,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "draft-only",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "degraded",
              },
            ],
            metrics: {
              citationCount: 1,
              unsourcedClaimCount: 1,
              unverifiedClaimCount: 0,
              tierCounts: { T1: 1 },
            },
          },
        }}
      />,
    );

    expect(
      document.querySelector('[data-citation-quality-warning="degraded"]'),
    ).toBeNull();
    expect(
      screen.queryByText(
        /some conclusions lack sufficient citation support|部分结论缺少充分的引用支持/i,
      ),
    ).toBeNull();
  });

  it("hides unverified conflicts that cannot be located to a concrete claim", () => {
    render(
      <MarkdownContent
        content="Revenue may differ across sources."
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "unverified",
            publishStatus: "draft-only",
            layers: { L3: "degraded" },
            issues: [
              {
                code: "cross_source_value_conflict",
                layer: "L3",
                severity: "unverified",
              },
            ],
            metrics: {
              citationCount: 0,
              unsourcedClaimCount: 0,
              unverifiedClaimCount: 1,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    expect(
      document.querySelector('[data-citation-quality-warning="unverified"]'),
    ).toBeNull();
    expect(
      screen.queryByText(
        /some sources have not been cross-checked or conflict|部分来源尚未交叉验证或存在冲突/i,
      ),
    ).toBeNull();
  });

  it("never hides useful content for legacy blocked citation metadata", () => {
    render(
      <MarkdownContent
        content="Citation verification failed after one automatic repair attempt."
        citationBundle={{
          version: 1,
          citations: [],
          integrity: {
            status: "repaired",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 1,
            policyRevision: "citation-v1",
            publicationBlocked: true,
          },
          quality: {
            policyId: "finance",
            policyRevision: "finance-citation-policy-v1",
            mode: "strict-domain",
            status: "degraded",
            publishStatus: "blocked",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "claim_evidence_mismatch",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_blocked",
                claim: { exact: "Revenue was 100 USD." },
              },
            ],
            claims: [
              {
                claimId: "clm_blocked",
                exact: "Revenue was 100 USD.",
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unverified",
                issueCodes: ["claim_evidence_mismatch"],
              },
            ],
            metrics: {
              citationCount: 0,
              unsourcedClaimCount: 1,
              unverifiedClaimCount: 1,
              tierCounts: {},
            },
          },
        }}
      />,
    );

    expect(
      screen.getByText(/after one automatic repair attempt/i),
    ).not.toBeNull();
    expect(
      screen.queryByText(
        /unverified answer was not published|未经验证的回答未发布/i,
      ),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-quality-warning]"),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-claim-quality]"),
    ).toBeNull();
  });
});
