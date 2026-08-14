import { describe, expect, it } from "vitest";

import {
  findHtmlQuoteRange,
  highlightHtmlDocument,
} from "./HtmlDocumentRenderer";

function html(value: string): Document {
  return new DOMParser().parseFromString(value, "text/html");
}

describe("HTML citation location", () => {
  it("matches a quote across text nodes", () => {
    const doc = html("<p>Revenue <strong>increased 18%</strong> year over year.</p>");
    const range = findHtmlQuoteRange(doc.body, {
      exact: "Revenue increased 18% year over year.",
    });

    expect(range?.toString()).toBe("Revenue increased 18% year over year.");
  });

  it("uses prefix and suffix to disambiguate repeated exact text", () => {
    const doc = html(
      "<p>First: unchanged. Old.</p><p>Guidance: unchanged. New.</p>",
    );
    const range = findHtmlQuoteRange(doc.body, {
      exact: "unchanged",
      prefix: "Guidance: ",
      suffix: ". New",
    });

    expect(range?.startContainer.parentElement?.textContent).toContain(
      "Guidance",
    );
  });

  it("matches PDF soft line wraps against continuous HTML prose", () => {
    const doc = html(
      "<p>英伟达官宣CPO交换机全面量产驱动光通信产业链走强，人工智能ETF集体上扬，市场交易活跃。</p>",
    );
    const range = findHtmlQuoteRange(doc.body, {
      exact:
        "英伟达官宣CPO交换机\n全面量产驱动光通信产业链走强，人工智能\nETF集体上扬，市场交易活跃。",
    });

    expect(range?.toString()).toBe(
      "英伟达官宣CPO交换机全面量产驱动光通信产业链走强，人工智能ETF集体上扬，市场交易活跃。",
    );
  });

  it("uses one unique long excerpt when later PDF typography differs", () => {
    const shared =
      "当前国内外科技盈利均保持高增长，同时整体盈利增速持续回升，财报季有望成为市场从估值消化切换至盈利驱动的拐点。";
    const doc = html(`<p>${shared}市场称其为“胜负手”。</p>`);
    const range = findHtmlQuoteRange(doc.body, {
      exact: `${shared}市场称其为\"胜负手\"。`,
    });

    const matched = range?.toString() ?? "";
    expect(matched.length).toBeGreaterThanOrEqual(32);
    expect(shared.startsWith(matched)).toBe(true);
  });

  it("does not relax short ambiguous quotes", () => {
    const doc = html("<p>AB</p>");

    expect(
      findHtmlQuoteRange(doc.body, { exact: "A B" }),
    ).toBeNull();
  });

  it("draws one padded rectangle around the complete quote range", () => {
    const doc = html(
      "<p>Revenue increased 18% year over year and guidance remained unchanged.</p>",
    );
    const rangePrototype = Range.prototype as Range & {
      getBoundingClientRect?: () => DOMRect;
    };
    const original = rangePrototype.getBoundingClientRect;
    Object.defineProperty(rangePrototype, "getBoundingClientRect", {
      configurable: true,
      value: () => new DOMRect(120, 80, 240, 48),
    });
    doc.body.getBoundingClientRect = () => new DOMRect(100, 40, 600, 800);

    try {
      const result = highlightHtmlDocument(doc, {
        kind: "html",
        quote: {
          exact:
            "Revenue increased 18% year over year and guidance remained unchanged.",
        },
      });
      const highlight = doc.querySelector<HTMLElement>(
        "[data-citation-highlight]",
      );

      expect(result.status).toBe("located-fallback");
      expect(highlight?.tagName).toBe("SPAN");
      expect(highlight?.style.left).toBe("16px");
      expect(highlight?.style.top).toBe("37px");
      expect(highlight?.style.width).toBe("248px");
      expect(highlight?.style.height).toBe("54px");
    } finally {
      if (original) {
        Object.defineProperty(rangePrototype, "getBoundingClientRect", {
          configurable: true,
          value: original,
        });
      } else {
        Reflect.deleteProperty(rangePrototype, "getBoundingClientRect");
      }
    }
  });

  it("cleans the previous block highlight before locating a new citation", () => {
    const doc = html(
      '<p data-chunk-id="c1">Alpha evidence.</p><p data-chunk-id="c2">Beta evidence.</p>',
    );
    highlightHtmlDocument(doc, {
      kind: "html",
      chunkId: "c1",
      quote: { exact: "Alpha" },
    });
    expect(
      doc.querySelector('[data-chunk-id="c1"]')?.getAttribute(
        "data-citation-block-highlight",
      ),
    ).toBe("true");

    highlightHtmlDocument(doc, {
      kind: "html",
      chunkId: "c2",
      quote: { exact: "Beta" },
    });
    expect(
      doc.querySelector('[data-chunk-id="c1"]')?.hasAttribute(
        "data-citation-block-highlight",
      ),
    ).toBe(false);
    expect(
      doc.querySelector('[data-chunk-id="c2"]')?.getAttribute(
        "data-citation-block-highlight",
      ),
    ).toBe("true");
  });

  it("matches chunk ids as opaque attributes without selector interpolation", () => {
    const chunkId = 'chunk-"quoted\nvalue';
    const doc = html("<p>Opaque chunk evidence.</p>");
    doc.querySelector("p")?.setAttribute("data-chunk-id", chunkId);

    const result = highlightHtmlDocument(doc, {
      kind: "html",
      chunkId,
      quote: { exact: "Opaque chunk" },
    });

    expect(result.status).toBe("located-exact");
    expect(
      doc.querySelector("[data-citation-block-highlight]")?.textContent,
    ).toBe("Opaque chunk evidence.");
  });
});
