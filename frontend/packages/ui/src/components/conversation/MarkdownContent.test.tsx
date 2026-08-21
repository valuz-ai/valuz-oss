/** @vitest-environment jsdom */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "./MarkdownContent";
import { stripStreamingEvidenceLinkTail } from "./CitationInline";
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

function getCitationHoverCard(): HTMLElement {
  const card = document.querySelector<HTMLElement>("[data-citation-hover-card]");
  if (!card) throw new Error("citation hover card was not rendered");
  return card;
}

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

it("renders an auto-bound citation from a sidecar anchor without changing stored text", () => {
  const content = "Revenue increased 18%.";
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [CITATIONS.citations[0]!],
    projection: {
      evidenceHandleToCitationId: {},
      anchors: [
        {
          citationId: "cit_first",
          claimId: "clm_revenue",
          origin: "auto-bound",
          sourceOffset: content.indexOf("."),
          location: {
            kind: "text",
            blockIndex: 0,
            start: 0,
            end: content.indexOf("."),
            sourceStart: 0,
            sourceEnd: content.indexOf("."),
          },
        },
      ],
      provenanceRegions: [],
    },
  };

  const { container } = render(
    <MarkdownContent content={content} citationBundle={bundle} />,
  );

  expect(container.textContent).toContain("Revenue increased 18%");
  expect(container.querySelectorAll('[data-citation-id="cit_first"]')).toHaveLength(1);
  expect(content).toBe("Revenue increased 18%.");
});

it("projects a deterministic numeric correction before rendering its citation", () => {
  const content = "| Company | Market cap |\n|---|---:|\n| MU | ~$991亿 |";
  const start = content.indexOf("991");
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [
      {
        ...CITATIONS.citations[0]!,
        citationId: "cit_market_cap",
        evidence: {
          kind: "structured-data",
          datasetId: "reportify.stock_quote",
          toolName: "stock_quote",
          entityId: "MU",
          field: "market_cap",
          metric: "market_cap",
          value: 991_118_782_300,
          unit: "USD",
          capturedAt: "2026-08-10T00:00:00Z",
        },
        annotations: {
          corrections: [
            {
              claimId: "clm_mu_market_cap",
              originalText: "991",
              replacementText: "9,911",
              reason: "structured-value-conflict",
            },
          ],
        },
      },
    ],
    projection: {
      evidenceHandleToCitationId: {},
      textCorrections: [
        {
          claimId: "clm_mu_market_cap",
          citationId: "cit_market_cap",
          sourceStart: start,
          sourceEnd: start + 3,
          originalText: "991",
          replacementText: "9,911",
          reason: "structured-value-conflict",
        },
      ],
      anchors: [
        {
          citationId: "cit_market_cap",
          claimId: "clm_mu_market_cap",
          origin: "auto-bound",
          sourceOffset: content.indexOf("亿") + 1,
          location: {
            kind: "table-cell",
            blockIndex: 0,
            rowIndex: 0,
            columnIndex: 1,
            sourceStart: content.indexOf("~$991亿"),
            sourceEnd: content.indexOf("~$991亿") + "~$991亿".length,
          },
        },
      ],
      provenanceRegions: [],
    },
  };

  const { container } = render(
    <MarkdownContent content={content} citationBundle={bundle} />,
  );

  expect(container.textContent).toContain("~$9,911亿");
  expect(container.textContent).not.toContain("~$991亿");
  expect(container.querySelector('[data-citation-id="cit_market_cap"]')).not.toBeNull();
  expect(content).toContain("~$991亿");

  fireEvent.mouseEnter(
    screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
  );
  expect(
    screen.getByText(/(?:automatically corrected|已依据结构化数据自动修正).*991.*9,911/i),
  ).not.toBeNull();
});

it("projects a precise document correction before rendering its citation", () => {
  const content = "2025 年云侧 AI 半导体 TAM 约 23,500 亿美元.";
  const start = content.indexOf("23,500");
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [
      {
        ...CITATIONS.citations[0]!,
        citationId: "cit_ai_tam",
        evidence: {
          kind: "text",
          quote: "cloud AI Semi TAM may grow to US$235bn in 2025e",
          snippet: "cloud AI Semi TAM may grow to US$235bn in 2025e",
          capturedAt: "2026-08-10T00:00:00Z",
        },
        locator: {
          kind: "pdf",
          page: 7,
          chunkId: "chunk_ai_tam",
        },
        annotations: {
          corrections: [
            {
              claimId: "clm_ai_tam",
              originalText: "23,500",
              replacementText: "2,350",
              reason: "document-value-conflict",
            },
          ],
        },
      },
    ],
    projection: {
      evidenceHandleToCitationId: {},
      textCorrections: [
        {
          claimId: "clm_ai_tam",
          citationId: "cit_ai_tam",
          sourceStart: start,
          sourceEnd: start + "23,500".length,
          originalText: "23,500",
          replacementText: "2,350",
          reason: "document-value-conflict",
        },
      ],
      anchors: [
        {
          citationId: "cit_ai_tam",
          claimId: "clm_ai_tam",
          origin: "auto-bound",
          sourceOffset: content.indexOf("美元") + "美元".length,
          location: {
            kind: "text",
            blockIndex: 0,
            start: 0,
            end: content.length,
            sourceStart: 0,
            sourceEnd: content.length,
          },
        },
      ],
    },
  };

  const { container } = render(
    <MarkdownContent content={content} citationBundle={bundle} />,
  );

  expect(container.textContent).toContain("2,350 亿美元");
  expect(container.textContent).not.toContain("23,500 亿美元");
  expect(container.querySelector('[data-citation-id="cit_ai_tam"]')).not.toBeNull();

  fireEvent.mouseEnter(
    screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
  );
  expect(
    screen.getByText(
      /(?:original source|原始来源).*23,500.*2,350/i,
    ),
  ).not.toBeNull();
});

it("fails closed when a correction no longer matches the immutable source span", () => {
  const content = "MU market cap is $992.";
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [CITATIONS.citations[0]!],
    projection: {
      evidenceHandleToCitationId: {},
      textCorrections: [
        {
          claimId: "clm_mu_market_cap",
          citationId: "cit_first",
          sourceStart: content.indexOf("992"),
          sourceEnd: content.indexOf("992") + 3,
          originalText: "991",
          replacementText: "9,911",
          reason: "structured-value-conflict",
        },
      ],
    },
  };

  const { container } = render(
    <MarkdownContent content={content} citationBundle={bundle} />,
  );

  expect(container.textContent).toContain("$992");
  expect(container.textContent).not.toContain("9,911");
});

it("renders one terminal citation for a table provenance region", () => {
  const content =
    "| Company | 2024 | 2025 |\n" +
    "| --- | ---: | ---: |\n" +
    "| A | 10 | 11 |\n" +
    "| B | 20 | 21 |";
  const terminalOffset = content.lastIndexOf("20") + "20".length;
  const bundle: CitationBundleV1 = {
    ...CITATIONS,
    citations: [CITATIONS.citations[0]!],
    projection: {
      evidenceHandleToCitationId: {},
      anchors: [],
      provenanceRegions: [
        {
          regionId: "region_2024",
          blockIndex: 0,
          rowStart: 0,
          rowEnd: 1,
          columnStart: 1,
          columnEnd: 1,
          citationIds: ["cit_first"],
          sourceOffset: terminalOffset,
          anchor: {
            kind: "table-cell",
            blockIndex: 0,
            rowIndex: 1,
            columnIndex: 1,
            sourceStart: content.lastIndexOf("20"),
            sourceEnd: terminalOffset,
          },
        },
      ],
    },
  };

  const { container } = render(
    <MarkdownContent content={content} citationBundle={bundle} />,
  );

  expect(container.querySelectorAll('[data-citation-id="cit_first"]')).toHaveLength(1);
  expect(screen.getByText("20")).not.toBeNull();
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

  it("never shows a blocked placeholder while an evidence link is still streaming", () => {
    // Mid-stream the model has emitted only part of the binding. The complete
    // form is dropped by projectEvidenceMarkdownLinks, but a half-written one
    // used to reach Streamdown, which completes the link, rejects the unknown
    // evidence: protocol and paints "[blocked]" until the sidecar lands.
    render(
      <MarkdownContent content="Revenue was 100 USD [source](evidence://ev_mcp_abc123" />,
    );

    expect(screen.queryByText(/\[blocked\]/)).toBeNull();
    expect(document.body.textContent).toContain("Revenue was 100 USD");
  });

  it("drops a streaming binding tail at every stage of the protocol", () => {
    for (const tail of [
      "[source](evidence:",
      "[source](evidence://",
      "[source](evidence://ev_mcp_abc",
      "[source](evidence://ev_mcp_abc#/data/items/9/market_cap",
    ]) {
      const { unmount } = render(
        <MarkdownContent content={`Revenue was 100 USD ${tail}`} />,
      );
      expect(screen.queryByText(/\[blocked\]/)).toBeNull();
      expect(document.body.textContent).toContain("Revenue was 100 USD");
      unmount();
    }
  });

  it("only strips a partial binding at the very end of the stream", () => {
    // Exercised directly: through the renderer this would also measure how
    // Streamdown treats malformed markdown, which is a separate concern.
    expect(
      stripStreamingEvidenceLinkTail("Revenue was 100 USD [source](evidence://ev_a"),
    ).toBe("Revenue was 100 USD ");
    // A completed binding is the other function's job and must survive here.
    expect(
      stripStreamingEvidenceLinkTail("Revenue was 100 USD [source](evidence://ev_a)"),
    ).toBe("Revenue was 100 USD [source](evidence://ev_a)");
    // Ordinary prose containing brackets is untouched.
    expect(stripStreamingEvidenceLinkTail("See [note] for the method.")).toBe(
      "See [note] for the method.",
    );
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
      screen.getByRole("button", { name: /^1 Annual report$/i }),
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
      name: /^1 Annual report$/i,
    });
    const secondSource = screen.getByRole("button", {
      name: /^2 Earnings release$/i,
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
      screen.getByRole("button", { name: /^1–2 Annual report$/i }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /^2 Annual report$/i }),
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
    expect(
      screen.getByText("For the year, revenue increased 18%."),
    ).not.toBeNull();
    expect(screen.queryByText("Revenue increased 18%.")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("keeps controls visible outside the independently scrolling evidence", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
        onCitationClick={vi.fn()}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    const body = document.querySelector("[data-citation-card-body]");
    const evidenceScroll = document.querySelector(
      "[data-citation-evidence-scroll]",
    );
    const header = document.querySelector("[data-citation-card-header]");
    const footer = document.querySelector("[data-citation-card-footer]");
    const toggle = document.querySelector(
      "[data-citation-full-evidence-toggle]",
    );
    expect(body?.className).toContain("overflow-hidden");
    expect(evidenceScroll?.className).toContain("overflow-y-auto");
    expect(evidenceScroll?.contains(toggle)).toBe(false);
    expect(header?.className).toContain("shrink-0");
    expect(footer?.className).toContain("shrink-0");
    expect(
      screen.getByText("For the year, revenue increased 18%.").className,
    ).not.toContain("line-clamp-3");
  });

  it("shows a focused excerpt first and expands the full cited content on demand", () => {
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(
      screen.getByText("For the year, revenue increased 18%."),
    ).not.toBeNull();
    expect(screen.queryByText("Revenue increased 18%.")).toBeNull();

    const toggle = screen.getByRole("button", {
      name: /(?:show full cited content|展开完整引用内容)/i,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Revenue increased 18%.")).not.toBeNull();
    expect(
      screen.queryByText("For the year, revenue increased 18%."),
    ).toBeNull();
    expect(
      document
        .querySelector('[data-citation-displayed-evidence="full"]')
        ?.textContent,
    ).toContain("Revenue increased 18%.");
  });

  it("shows quote-only evidence directly without an empty excerpt or disclosure", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          ...CITATIONS.citations[0],
          evidence: {
            kind: "text",
            quote: "Revenue increased 18%.",
            snippet: "Revenue increased 18%.",
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

    expect(screen.getByText("Revenue increased 18%.")).not.toBeNull();
    expect(
      screen.queryByRole("button", {
        name: /(?:show full cited content|展开完整引用内容)/i,
      }),
    ).toBeNull();
  });

  it("labels discovery evidence as a search summary rather than original text", () => {
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        {
          ...CITATIONS.citations[0],
          source: {
            ...CITATIONS.citations[0]!.source,
            sourceCategory: "search_summary",
          },
          evidence: {
            kind: "text",
            quote: "A search-result summary of the source.",
            snippet: "A search-result summary of the source.",
            capturedAt: "2026-07-30T08:00:00Z",
          },
        },
      ],
    };
    render(
      <MarkdownContent
        content={"Summary [source](citation://cit_first)."}
        citationBundle={bundle}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );

    expect(screen.getByText(/search summary|搜索摘要/i)).not.toBeNull();
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
    // 11px, now expressed as the design token instead of an arbitrary value.
    expect(screen.getByRole("table").className).toContain("text-2xs");
    expect(
      screen.getByRole("cell", { name: "145,928" }).className,
    ).toContain("px-2");
    expect(getCitationHoverCard().className).toContain(
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
    fireEvent.click(
      screen.getByRole("button", {
        name: /(?:show full cited content|展开完整引用内容)/i,
      }),
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
            providerId: "valuz-data",
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
    expect(evidence?.textContent).toMatch(/cited data|引用数据/i);
    expect(evidence?.textContent).toContain(
      "total revenue: 174144069958 CNY",
    );
    const tooltipText = getCitationHoverCard().textContent ?? "";
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
            providerId: "valuz-data",
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
        verifierRevision: "claim-verifier-local-v3",
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
    expect(
      screen.getByRole("button", { name: /view data|查看数据/i }),
    ).not.toBeNull();
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

      const card = getCitationHoverCard();
      expect(card.getAttribute("data-side")).toBe("bottom");

      fireEvent.mouseLeave(pill.parentElement as HTMLElement);
      fireEvent.mouseEnter(card);
      act(() => vi.advanceTimersByTime(200));

      expect(getCitationHoverCard()).toBe(card);
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens above when the card cannot fit below the citation", () => {
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        if (this.hasAttribute("data-citation-hover-card")) {
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

      expect(getCitationHoverCard().getAttribute("data-side")).toBe("top");
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
      name: /(?:view original|查看原文)/i,
    });
    const openSourceLabel = openSource.querySelector("span");
    expect(openSource.classList.contains("group")).toBe(true);
    expect(openSource.classList.contains("hover:underline")).toBe(false);
    expect(openSourceLabel?.classList.contains("border-dotted")).toBe(true);
    expect(openSourceLabel?.classList.contains("border-transparent")).toBe(
      true,
    );
    expect(
      openSourceLabel?.classList.contains("group-hover:border-current"),
    ).toBe(true);
    fireEvent.blur(pill, { relatedTarget: openSource });
    fireEvent.focus(openSource);
    fireEvent.click(openSource);

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });
  });

  it("opens the citation from the hover-card title", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownContent
        content={"Revenue [source](citation://cit_first)."}
        citationBundle={CITATIONS}
        messageId="msg-title"
        onCitationClick={onCitationClick}
      />,
    );

    fireEvent.mouseEnter(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }),
    );
    // The hover card's title link and the source-list row now share the same
    // leading text, so the accessible name no longer identifies one of them.
    // This test is about the title link — address it by its marker.
    const titleLink = document.querySelector<HTMLElement>(
      "[data-citation-title-link='true']",
    );
    expect(titleLink).not.toBeNull();
    fireEvent.click(titleLink!);

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-title",
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

  it("explains finance source tiers from the hover-card badge", () => {
    const bundle: CitationBundleV1 = {
      ...CITATIONS,
      citations: [
        {
          ...CITATIONS.citations[0],
          annotations: {
            quality: {
              policyId: "finance",
              policyRevision: "finance-citation-policy-v3",
              tier: "T4",
              status: "passed",
              label: "T4",
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

    const trigger = document.querySelector(
      '[data-citation-source-tier-trigger="T4"]',
    );
    const tooltip = trigger?.querySelector(
      "[data-citation-source-tier-tooltip]",
    );
    expect(trigger?.getAttribute("tabindex")).toBe("0");
    expect(tooltip?.getAttribute("role")).toBe("tooltip");
    expect(trigger?.getAttribute("aria-describedby")).toBe(tooltip?.id);
    for (const tier of ["T1", "T2", "T3", "T4", "T5"]) {
      expect(
        tooltip?.querySelector(`[data-citation-source-tier-row="${tier}"]`),
      ).not.toBeNull();
    }
    expect(
      tooltip
        ?.querySelector('[data-citation-source-tier-row="T4"]')
        ?.getAttribute("data-active"),
    ).toBe("true");
    expect(tooltip?.textContent).toMatch(
      /source type|来源类型/i,
    );
  });

  it("renders calculation as a hoverable derivation instead of a numbered source", () => {
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

    const calculationPill = document.querySelector<HTMLElement>(
      "[data-citation-derivation]",
    );
    expect(calculationPill).not.toBeNull();
    expect(calculationPill?.textContent).toBe("");
    fireEvent.click(calculationPill!);
    expect(onCitationClick).not.toHaveBeenCalled();

    fireEvent.focus(calculationPill!);
    fireEvent.click(screen.getByRole("button", { name: /revenue.*annual report/i }));

    expect(onCitationClick).toHaveBeenCalledWith({
      messageId: "msg-1",
      citationId: "cit_first",
    });

    expect(document.querySelector("[data-citation-calculation-source]")).toBeNull();
    expect(document.querySelector("[data-citation-source-list]")).toBeNull();
    fireEvent.mouseEnter(calculationPill!);
    expect(screen.getAllByText(/revenue \/ 100 = 1\.18 x/i).length).toBeGreaterThan(0);
  });

  it("keeps a calculation quality issue on its calculator hover card", () => {
    const content = "Growth was 1.18x [calc](citation://cit_calculation).";
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: content.length,
      sourceStart: 0,
      sourceEnd: content.length,
    };
    const bundle: CitationBundleV1 = {
      version: 1,
      citations: [
        CITATIONS.citations[0]!,
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
                citationId: "cit_first",
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
      quality: {
        policyId: "finance",
        policyRevision: "v1",
        mode: "strict-domain",
        status: "degraded",
        publishStatus: "ready",
        layers: { L4: "degraded" },
        issues: [
          {
            code: "calculation_result_mismatch",
            layer: "L4",
            severity: "degraded",
            claimId: "clm_growth",
            citationIds: ["cit_calculation"],
            claim: { exact: "Growth was 1.18x" },
            location,
          },
        ],
        claims: [
          {
            claimId: "clm_growth",
            exact: "Growth was 1.18x",
            segmentIndex: 0,
            citationRequired: true,
            citationIds: ["cit_calculation"],
            status: "unverified",
            issueCodes: ["calculation_result_mismatch"],
            location,
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

    render(<MarkdownContent content={content} citationBundle={bundle} />);

    const calculationPill = document.querySelector<HTMLElement>(
      "[data-citation-derivation]",
    );
    expect(calculationPill?.getAttribute("data-citation-quality")).toBe(
      "critical",
    );
    expect(
      document.querySelector("[data-citation-claim-quality]"),
    ).toBeNull();
    fireEvent.mouseEnter(calculationPill!);
    expect(
      document.querySelector('[data-citation-quality-issues="critical"]'),
    ).not.toBeNull();
  });

  it("keeps a claim-scoped warning local when its citation is reused elsewhere", async () => {
    const content =
      "HBM is a three-vendor market. CoWoS demand exceeds supply [source](citation://cit_first).";
    const exact = "HBM is a three-vendor market.";
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: exact.length,
      sourceStart: 0,
      sourceEnd: exact.length,
    };
    const bundle: CitationBundleV1 = {
      ...CITATIONS,
      citations: [CITATIONS.citations[0]!],
      quality: {
        policyId: "finance",
        policyRevision: "v1",
        verifierRevision: "claim-verifier-local-v3",
        mode: "strict-domain",
        status: "degraded",
        publishStatus: "ready",
        layers: { L4: "degraded" },
        issues: [
          {
            code: "claim_source_entity_conflict",
            layer: "L4",
            severity: "degraded",
            claimId: "clm_hbm",
            citationIds: ["cit_first"],
            claim: { exact },
            location,
          },
        ],
        claims: [
          {
            claimId: "clm_hbm",
            exact,
            segmentIndex: 0,
            citationRequired: true,
            citationIds: ["cit_first"],
            status: "unverified",
            issueCodes: ["claim_source_entity_conflict"],
            location,
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

    render(<MarkdownContent content={content} citationBundle={bundle} />);

    const citationPill = screen.getByRole("button", {
      name: /(?:citation|引用) 1/i,
    });
    expect(citationPill.getAttribute("data-citation-quality")).toBeNull();
    const marker = document.querySelector<HTMLElement>(
      "[data-citation-claim-quality]",
    );
    expect(marker).not.toBeNull();
    await act(async () => {
      fireEvent.pointerEnter(marker!);
    });
    await vi.waitFor(() => {
      expect(
        document.querySelector('[data-citation-claim-evidence="cit_first"]'),
      ).not.toBeNull();
    });
    expect(
      document.querySelector('[data-citation-claim-evidence="cit_first"]')
        ?.textContent,
    ).toMatch(/Annual report/i);
  });

  it("does not turn a non-adjacent advisory into a standalone warning", () => {
    const content =
      "This sentence paraphrases the source. Another fact [source](citation://cit_first).";
    const exact = "This sentence paraphrases the source.";
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: exact.length,
      sourceStart: 0,
      sourceEnd: exact.length,
    };
    render(
      <MarkdownContent
        content={content}
        citationBundle={{
          ...CITATIONS,
          citations: [CITATIONS.citations[0]!],
          quality: {
            policyId: "finance",
            policyRevision: "v1",
            mode: "strict-domain",
            status: "unverified",
            publishStatus: "ready",
            layers: { L4: "unverified" },
            issues: [
              {
                code: "claim_translation_not_verified",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_paraphrase",
                citationIds: ["cit_first"],
                claim: { exact },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_paraphrase",
                exact,
                segmentIndex: 0,
                citationRequired: true,
                citationIds: ["cit_first"],
                status: "unverified",
                issueCodes: ["claim_translation_not_verified"],
                location,
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

    expect(
      document.querySelector("[data-citation-claim-quality]"),
    ).toBeNull();
    expect(
      screen
        .getByRole("button", { name: /(?:citation|引用) 1/i })
        .getAttribute("data-citation-quality"),
    ).toBeNull();
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
        messageId="message-critical-document"
        onCitationClick={vi.fn()}
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
    const evidenceScroll = document.querySelector(
      "[data-citation-evidence-scroll]",
    );
    expect(evidenceSection?.textContent).toMatch(/cited content|引用内容/i);
    expect(qualityIssues?.textContent).toMatch(
      /needs review|需要核验/i,
    );
    expect(qualityIssues?.textContent).toMatch(
      /number or calculation|数字或计算依据/i,
    );
    expect(evidenceScroll?.contains(qualityIssues)).toBe(false);
    expect(
      screen.getByRole("button", {
        name: /(?:view original|查看原文)/i,
      }),
    ).not.toBeNull();
    expect(
      evidenceSection?.compareDocumentPosition(qualityIssues!),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("marks an uncited claim neutrally without a turn-level warning", () => {
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

    // The statement itself is marked so a reader can tell it carries no
    // source, but in the neutral tone reserved for coverage gaps.
    const marker = document.querySelector("[data-citation-claim-quality]");
    expect(marker).not.toBeNull();
    expect(marker?.getAttribute("data-citation-claim-tone")).toBe("unsourced");
    // No turn-level alarm: a missing source is not a defect claim.
    expect(
      document.querySelector("[data-citation-quality-warning]"),
    ).toBeNull();
    expect(
      document.querySelector("[data-citation-quality-summary]"),
    ).toBeNull();
  });

  it("marks an unsourced statement in place with a neutral tone", () => {
    const content = "AI segment revenue reached 2.561 billion USD.";
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: content.length,
      sourceStart: 0,
      sourceEnd: content.length,
    };
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
            publishStatus: "ready",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_unsourced",
                claim: { exact: content },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_unsourced",
                exact: content,
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported" as const,
                issueCodes: ["numeric_claim_without_citation"],
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

    const marker = document.querySelector("[data-citation-claim-quality]");
    expect(marker).not.toBeNull();
    // Neutral tone: a coverage gap must not borrow the conflict warning style.
    expect(marker?.getAttribute("data-citation-claim-tone")).toBe("unsourced");
  });

  it("projects an address whose pointer holds parentheses and a space", () => {
    // Reportify names indicator keys after their call signature, so the
    // pointer is "/datas/0/indicators/ma(close, 20)". The raw protocol used to
    // reach the reader because the link never matched.
    const address =
      "evc_mcp_ind_12345678#/datas/0/indicators/ma(close, 20)";
    const { container } = render(
      <MarkdownContent
        content={`MA20 为 $199.56 [source](evidence://${address})。`}
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
          projection: {
            evidenceHandleToCitationId: { [address]: "cit_ma20" },
            anchors: [],
            provenanceRegions: [],
          },
        }}
      />,
    );

    expect(container.textContent).not.toContain("evidence://");
    expect(container.textContent).not.toContain("[source]");
    expect(container.textContent).toContain("$199.56");
  });

  it("keeps an unsourced marker out of the middle of a citation link", () => {
    // Markers are injected after protocol links are projected into shorter
    // citation links, so a source-text offset can land inside one and split
    // "[source](citation://cit_x)" into "[sourc ⊘ e](…)".
    const content = "MA60 $231.76 [source](citation://cit_ma60) 收敛中。";
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: content.length,
      sourceStart: 0,
      sourceEnd: content.indexOf("citation://") + 6,
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
            publishStatus: "ready",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_link",
                claim: { exact: "MA60 $231.76" },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_link",
                exact: "MA60 $231.76",
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported" as const,
                issueCodes: ["numeric_claim_without_citation"],
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

    // The link must survive intact: no fragment of the protocol may surface.
    expect(container.textContent).not.toContain("citation:");
    expect(container.textContent).not.toContain("sourc");
    expect(container.textContent).toContain("$231.76");
  });

  it("keeps a sidecar anchor out of the middle of a collection address", () => {
    // The anchor offset is measured against the text the guard normalised,
    // where the address has already shrunk to its materialized handle. The
    // client still holds what the model streamed, so the offset lands inside
    // the URL, splits it, and the protocol is left on screen — the whole
    // "$199.56 [source](evidence://evc_… ① …#/datas/0/indicators/ma(close,
    // 20))" cell the reader was shown.
    const address =
      "evc_mcp_33727617f83d033bfcb54a3a#/datas/0/indicators/ma(close, 20)";
    const content = `| MA20 | $199.56 [source](evidence://${address}) |`;
    const { container } = render(
      <MarkdownContent
        content={content}
        citationBundle={{
          version: 1,
          citations: [
            {
              citationId: "cit_ma20",
              source: {
                sourceId: "reportify.indicators",
                title: "Reportify · indicators",
                retrievedAt: "2026-08-09T00:00:00Z",
                sourceType: "dataset",
                providerId: "reportify",
              },
              evidence: {
                kind: "structured-data",
                datasetId: "reportify.indicators",
                toolName: "indicators",
                field: "/datas/0/indicators/ma(close, 20)",
                value: 199.56,
                capturedAt: "2026-08-09T00:00:00Z",
              },
              resolutionStatus: "ready",
              annotations: { binding: { evidenceHandle: "ev_mat_ma20" } },
            },
          ],
          integrity: {
            status: "passed",
            unknownCitationIds: [],
            unusedCitationIds: [],
            missingLocatorCitationIds: [],
            repairAttempts: 0,
            policyRevision: "citation-v1",
          },
          projection: {
            evidenceHandleToCitationId: {
              [address]: "cit_ma20",
              ev_mat_ma20: "cit_ma20",
            },
            // Deliberately points into the middle of the URL.
            anchors: [
              {
                citationId: "cit_ma20",
                claimId: "clm_ma20",
                origin: "auto-bound" as const,
                sourceOffset: content.indexOf("evc_mcp_33727617") + 8,
                location: {
                  kind: "table-cell" as const,
                  blockIndex: 0,
                  rowIndex: 0,
                  columnIndex: 1,
                },
              },
            ],
            provenanceRegions: [],
          },
        }}
      />,
    );

    const shown = container.textContent ?? "";
    expect(shown).not.toContain("evidence://");
    expect(shown).not.toContain("[sourc");
    expect(shown).toContain("$199.56");
  });

  it("marks the sentence the claim names, not where its offset points", () => {
    // Quality offsets are measured against the text the audit judged, which has
    // been normalised and had its protocol links rewritten. Replayed against
    // the streamed text one landed in a table three sections below, so a
    // sentence about FCF margin appeared to annotate an unrelated cell.
    const claim = "微软当前FCF利润率（20%）远低于经营利润率（47%）。";
    const content = [
      claim,
      "",
      "| 评估维度 | 结论 |",
      "|---|---|",
      "| 利润代表性 | PE有参考意义 |",
    ].join("\n");
    const location = {
      kind: "text" as const,
      blockIndex: 0,
      start: 0,
      end: claim.length,
      sourceStart: 0,
      // Deliberately wrong: points into the table row far below.
      sourceEnd: content.indexOf("PE有参考意义") + 4,
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
            publishStatus: "ready",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_fcf",
                claim: { exact: claim },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_fcf",
                exact: claim,
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported" as const,
                issueCodes: ["numeric_claim_without_citation"],
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

    // The marker belongs to the paragraph, so the table cell stays clean.
    const cells = Array.from(container.querySelectorAll("td")).map(
      (cell) => cell.textContent ?? "",
    );
    expect(cells).toContain("PE有参考意义");
    expect(document.querySelector("[data-citation-claim-quality]")).not.toBeNull();
  });

  it("keeps an unsourced marker out of the middle of a number", () => {
    // A drifted source offset used to land inside the value, rendering
    // "13.82%" as "1 [marker] 3.82%" — two numbers where the answer had one.
    const content = "Gross margin 13.82% this quarter.";
    const location = {
      kind: "table-cell" as const,
      blockIndex: 0,
      rowIndex: 0,
      columnIndex: 0,
      sourceStart: 0,
      sourceEnd: content.indexOf("13.82%") + 1,
    };
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
            publishStatus: "ready",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_split",
                claim: { exact: "13.82%" },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_split",
                exact: "13.82%",
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported" as const,
                issueCodes: ["numeric_claim_without_citation"],
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
    expect(document.body.textContent).toContain("13.82%");
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
    // jsdom implements no `:has()` — a `:scope > div:has(...)` query silently
    // returns nothing there, so the region split is computed in JS instead.
    // (The `:has()` strings asserted on `className` above are Tailwind
    // variants shipped to the browser; only the queries need this.)
    const wrapperRegions = Array.from(wrapper?.children ?? []) as HTMLElement[];
    const containsTable = (node: HTMLElement) =>
      node.querySelector("[data-streamdown='table']") !== null;
    const dataRegion = wrapperRegions.find(containsTable);
    expect(dataRegion).not.toBeUndefined();
    const claimMarker = dataRegion?.querySelector("[data-citation-claim-quality]");
    expect(claimMarker).not.toBeNull();
    const toolbarRegions = wrapperRegions.filter((node) => !containsTable(node));
    expect(toolbarRegions).toHaveLength(1);
    expect(toolbarRegions[0]?.contains(claimMarker ?? null)).toBe(false);
  });

  it("keeps a table valid when a table-cell audit offset drifted into its delimiter", () => {
    const content = [
      "| 指标 | FY2027 Q1 | FY2026 Q4 | 环比 |",
      "|---|---:|---:|---:|",
      "| 现金及短期投资 | 805.72 亿美元 | 625.56 亿美元 | +28.8% |",
      "| 长期投资 | 433.64 亿美元 | 222.51 亿美元 | +94.9% |",
    ].join("\n");
    const delimiterOffset = content.indexOf("---:|") + 3;
    const location = {
      kind: "table-cell" as const,
      blockIndex: 4,
      rowIndex: 0,
      columnIndex: 3,
      sourceStart: delimiterOffset - 6,
      sourceEnd: delimiterOffset,
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
            publishStatus: "ready",
            layers: { L4: "degraded" },
            issues: [
              {
                code: "numeric_claim_without_citation",
                layer: "L4",
                severity: "unverified",
                claimId: "clm_cash_growth",
                claim: { exact: "现金及短期投资 — 环比: +28.8%" },
                location,
              },
            ],
            claims: [
              {
                claimId: "clm_cash_growth",
                exact: "现金及短期投资 — 环比: +28.8%",
                segmentIndex: 0,
                citationRequired: true,
                citationIds: [],
                status: "unsupported",
                issueCodes: ["numeric_claim_without_citation"],
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

    const table = screen.getByRole("table");
    expect(table).not.toBeNull();
    const cashRow = screen.getByText("现金及短期投资").closest("tr");
    expect(cashRow?.textContent).toContain("+28.8%");
    expect(
      cashRow?.querySelector('[data-citation-claim-tone="unsourced"]'),
    ).not.toBeNull();
    expect(container.textContent).not.toContain("|---|");
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
