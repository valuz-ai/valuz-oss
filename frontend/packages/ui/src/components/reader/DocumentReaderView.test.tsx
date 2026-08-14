import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentReaderView } from "./DocumentReaderView";
import type { DocumentSource } from "./document-reader.types";

const pdfjsMock = vi.hoisted(() => ({ getDocument: vi.fn() }));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: pdfjsMock.getDocument,
}));

const CHUNKS: DocumentSource = {
  id: "doc-1",
  title: "Q2 earnings call",
  source: { name: "Acme Research" },
  publishedAt: Date.UTC(2026, 6, 20, 4, 30),
  originalUrl: "https://example.com/original",
  render: {
    kind: "chunks",
    chunks: [
      { id: "c1", type: "heading", text: "Opening remarks" },
      { id: "c2", type: "paragraph", text: "Revenue grew." },
      {
        id: "c3",
        type: "speaker",
        speaker: "CFO",
        segments: [
          { id: "s1", text: "Margins held. " },
          { id: "s2", text: "Guidance is unchanged." },
        ],
      },
    ],
  },
};

beforeEach(() => {
  // jsdom has no layout, so scrollIntoView is not implemented.
  Element.prototype.scrollIntoView = vi.fn();
  pdfjsMock.getDocument.mockReset().mockReturnValue({
    promise: new Promise(() => undefined),
    destroy: vi.fn(),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DocumentReaderView", () => {
  it("keeps a panel frame around the document workspace", () => {
    const { container } = render(<DocumentReaderView doc={CHUNKS} />);
    const frame = container.firstElementChild;

    expect(frame?.classList.contains("overflow-hidden")).toBe(true);
    expect(frame?.classList.contains("bg-surface")).toBe(true);
    expect(frame?.classList.contains("rounded-[14px]")).toBe(true);
    expect(frame?.classList.contains("border")).toBe(true);
    expect(frame?.classList.contains("border-surface-border")).toBe(true);
    expect(frame?.classList.contains("shadow-sm")).toBe(false);
  });

  it("does not draw a second frame when embedded in a panel", () => {
    const { container } = render(
      <DocumentReaderView doc={CHUNKS} framed={false} />,
    );
    const frame = container.firstElementChild;

    expect(frame?.classList.contains("overflow-hidden")).toBe(true);
    expect(frame?.classList.contains("border")).toBe(false);
    expect(frame?.classList.contains("rounded-[14px]")).toBe(false);
  });

  it("renders the document header with source and publish time", () => {
    const { container } = render(<DocumentReaderView doc={CHUNKS} />);

    expect(
      screen.getByRole("heading", { name: "Q2 earnings call" }),
    ).toBeTruthy();
    expect(screen.getByText("Acme Research")).toBeTruthy();
    const header = container.querySelector("header");
    expect(header?.classList.contains("border-b")).toBe(true);
    expect(header?.classList.contains("border-surface-border")).toBe(true);
  });

  it("renders the original source link after a chunk document body", async () => {
    const user = userEvent.setup();
    render(<DocumentReaderView doc={CHUNKS} />);

    const link = screen.getByRole("link", { name: "原文链接" });
    expect(link.getAttribute("href")).toBe("https://example.com/original");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.querySelector("svg")).toBeTruthy();
    expect(link.parentElement?.classList.contains("px-8")).toBe(true);
    const label = screen.getByText("原文链接");
    expect(label.classList.contains("border-dotted")).toBe(true);
    expect(label.classList.contains("border-transparent")).toBe(true);
    expect(label.classList.contains("group-hover:border-current")).toBe(true);
    expect(link.compareDocumentPosition(screen.getByText("Revenue grew."))).toBe(
      Node.DOCUMENT_POSITION_PRECEDING,
    );

    await user.hover(link);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain(
      "https://example.com/original",
    );
    const tooltipSurface = document.querySelector<HTMLElement>(
      "[data-original-link-tooltip]",
    );
    expect(tooltipSurface).toBeTruthy();
    expect(tooltipSurface?.classList.contains("bg-surface")).toBe(true);
    expect(tooltipSurface?.classList.contains("border-surface-border")).toBe(
      true,
    );
    expect(tooltipSurface?.classList.contains("shadow-xl")).toBe(true);
    expect(
      tooltipSurface?.classList.contains(
        "w-[min(360px,calc(100vw-32px))]",
      ),
    ).toBe(true);
  });

  it("does not render an original source footer without an original URL", () => {
    render(
      <DocumentReaderView
        doc={{
          ...CHUNKS,
          originalUrl: undefined,
        }}
      />,
    );

    expect(screen.queryByRole("link", { name: "原文链接" })).toBeNull();
  });

  it("anchors every chunk so hosts can deep-link to a block", () => {
    const { container } = render(<DocumentReaderView doc={CHUNKS} />);

    expect(
      Array.from(container.querySelectorAll("[data-chunk-id]")).map((el) =>
        el.getAttribute("data-chunk-id"),
      ),
    ).toEqual(["c1", "c2", "c3"]);
    expect(container.querySelector('[data-segment-id="s2"]')).toBeTruthy();
  });

  it("scrolls to the requested chunk and highlights it", async () => {
    const { container } = render(
      <DocumentReaderView doc={CHUNKS} location={{ chunkId: "c2" }} />,
    );

    const block = container.querySelector('[data-chunk-id="c2"]');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    await waitFor(() =>
      expect(block?.getAttribute("data-citation-block-highlight")).toBe("true"),
    );
  });

  // A stale or hand-edited deep link must land on the document, not on an error.
  it("stays silent when the requested chunk is missing", () => {
    render(<DocumentReaderView doc={CHUNKS} location={{ chunkId: "nope" }} />);

    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByText("Revenue grew.")).toBeTruthy();
  });

  it("highlights only the requested segment", async () => {
    const { container } = render(
      <DocumentReaderView doc={CHUNKS} location={{ segmentId: "s2" }} />,
    );

    const segment = container.querySelector('[data-segment-id="s2"]');
    await waitFor(() =>
      expect(segment?.querySelector("[data-citation-highlight]")).toBeTruthy(),
    );
    expect(
      container.querySelector(
        '[data-segment-id="s1"] [data-citation-highlight]',
      ),
    ).toBeNull();
  });

  it("does not cross into another chunk when a chunk-scoped segment is stale", () => {
    const doc: DocumentSource = {
      ...CHUNKS,
      render: {
        kind: "chunks",
        chunks: [
          {
            id: "expected",
            type: "paragraph",
            segments: [{ id: "old-segment", text: "Expected block." }],
          },
          {
            id: "other",
            type: "paragraph",
            segments: [{ id: "shared-segment", text: "Wrong block." }],
          },
        ],
      },
    };
    const { container } = render(
      <DocumentReaderView
        doc={doc}
        location={{
          chunkId: "expected",
          segmentId: "shared-segment",
        }}
      />,
    );

    expect(container.querySelector("[data-citation-highlight]")).toBeNull();
    expect(
      container
        .querySelector("[data-locate-status]")
        ?.getAttribute("data-locate-status"),
    ).toBe("not-found");
  });

  it("falls back to an exact quote when a chunk id is stale", async () => {
    const { container } = render(
      <DocumentReaderView
        doc={CHUNKS}
        location={{
          chunkId: "old-id",
          quote: { exact: "Guidance is unchanged." },
        }}
      />,
    );

    await waitFor(() =>
      expect(
        container.querySelector("[data-citation-highlight]")?.textContent,
      ).toBe("Guidance is unchanged."),
    );
    expect(
      container
        .querySelector("[data-locate-status]")
        ?.getAttribute("data-locate-status"),
    ).toBe("located-fallback");
  });

  it("renders every PDF with PDF.js even without a citation locator", async () => {
    const user = userEvent.setup();
    const observers: Array<{
      callback: IntersectionObserverCallback;
      nodes: Set<Element>;
      observer: IntersectionObserver;
    }> = [];
    class MockIntersectionObserver {
      readonly root = null;
      readonly rootMargin = "0px";
      readonly thresholds = [0];
      readonly nodes = new Set<Element>();
      readonly callback: IntersectionObserverCallback;

      constructor(callback: IntersectionObserverCallback) {
        this.callback = callback;
        observers.push({
          callback,
          nodes: this.nodes,
          observer: this as unknown as IntersectionObserver,
        });
      }

      observe = (node: Element) => this.nodes.add(node);
      unobserve = (node: Element) => this.nodes.delete(node);
      disconnect = () => this.nodes.clear();
      takeRecords = () => [];
    }
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    const getPage = vi.fn((pageNumber: number) => {
      void pageNumber;
      return new Promise(() => undefined);
    });
    pdfjsMock.getDocument.mockReturnValue({
      promise: Promise.resolve({
        numPages: 4,
        getPage,
      }),
      destroy: vi.fn(),
    });
    const { container } = render(
      <DocumentReaderView
        doc={{
          id: "doc-2",
          title: "Annual report",
          render: {
            kind: "file",
            url: "https://example.com/a.pdf",
            mimeType: "application/pdf",
          },
        }}
      />,
    );

    expect(container.querySelector("[data-pdfjs-document]")).toBeTruthy();
    expect(container.querySelector("iframe")).toBeNull();
    await waitFor(() =>
      expect(
        Array.from(container.querySelectorAll("[data-pdf-page]")).map((page) =>
          page.getAttribute("data-pdf-page"),
        ),
      ).toEqual(["1", "2", "3", "4"]),
    );
    await waitFor(() =>
      expect(
        Array.from(new Set(getPage.mock.calls.map(([page]) => page))),
      ).toEqual([1]),
    );

    const pageThreeObserver = observers.find(({ nodes }) =>
      Array.from(nodes).some(
        (node) => node.getAttribute("data-pdf-page") === "3",
      ),
    );
    const pageThree = Array.from(pageThreeObserver?.nodes ?? []).find(
      (node) => node.getAttribute("data-pdf-page") === "3",
    );
    expect(pageThreeObserver).toBeTruthy();
    expect(pageThree).toBeTruthy();
    act(() => {
      pageThreeObserver?.callback(
        [
          {
            target: pageThree,
            isIntersecting: true,
          } as IntersectionObserverEntry,
        ],
        pageThreeObserver.observer,
      );
    });
    await waitFor(() =>
      expect(
        Array.from(new Set(getPage.mock.calls.map(([page]) => page))),
      ).toEqual([1, 3]),
    );
    expect(
      container
        .querySelector("[data-pdfjs-document]")
        ?.getAttribute("data-pdf-zoom-mode"),
    ).toBe("fit-width");
    expect(screen.queryByRole("menuitemradio")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: "缩放 适合宽度" }),
    );
    await user.click(screen.getByRole("menuitemradio", { name: "适合整页" }));
    expect(
      container
        .querySelector("[data-pdfjs-document]")
        ?.getAttribute("data-pdf-zoom-mode"),
    ).toBe("fit-page");
    expect(
      screen.getByRole("button", { name: "缩放 适合整页" }),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "缩放 适合整页" }),
    );
    expect(
      screen
        .getByRole("menuitemradio", { name: "适合整页" })
        .querySelector(".lucide-check"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("menuitemradio", { name: "适合宽度" })
        .querySelector(".lucide-check"),
    ).toBeNull();
    await user.keyboard("{Escape}");

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(
      container
        .querySelector("[data-pdfjs-document]")
        ?.getAttribute("data-pdf-zoom-mode"),
    ).toBe("custom");
    expect(
      screen.getByRole("button", { name: "缩放 150%" }),
    ).toBeTruthy();
    const firstPageBeforePreset = container.querySelector(
      '[data-pdf-page="1"]',
    );
    await user.click(screen.getByRole("button", { name: "缩放 150%" }));
    expect(
      screen
        .getByRole("menuitemradio", { name: "150%" })
        .querySelector(".lucide-check"),
    ).toBeTruthy();
    expect(
      [
        "25%",
        "50%",
        "75%",
        "100%",
        "125%",
        "150%",
        "200%",
        "300%",
        "400%",
      ].every((label) =>
        Boolean(screen.getByRole("menuitemradio", { name: label })),
      ),
    ).toBe(true);
    await user.click(screen.getByRole("menuitemradio", { name: "100%" }));
    expect(
      container
        .querySelector("[data-pdfjs-document]")
        ?.getAttribute("data-pdf-zoom-mode"),
    ).toBe("custom");
    expect(
      screen.getByRole("button", { name: "缩放 100%" }),
    ).toBeTruthy();
    expect(container.querySelector('[data-pdf-page="1"]')).toBe(
      firstPageBeforePreset,
    );

    const pageInput = screen.getByRole("textbox", { name: "页码" });
    await user.clear(pageInput);
    await user.type(pageInput, "3{Enter}");
    expect((pageInput as HTMLInputElement).value).toBe("3");
    expect(Element.prototype.scrollIntoView).toHaveBeenLastCalledWith({
      block: "start",
    });

    await user.clear(pageInput);
    await user.type(pageInput, "99{Enter}");
    expect((pageInput as HTMLInputElement).value).toBe("4");
    expect(
      screen.getByRole("button", { name: "下一页" }).hasAttribute("disabled"),
    ).toBe(true);

    await user.clear(pageInput);
    fireEvent.blur(pageInput);
    expect((pageInput as HTMLInputElement).value).toBe("4");
  });

  it("renders the side panel slot only when supplied", () => {
    const { rerender } = render(<DocumentReaderView doc={CHUNKS} />);
    expect(screen.queryByText("panel")).toBeNull();

    rerender(<DocumentReaderView doc={CHUNKS} sidePanel={<div>panel</div>} />);
    expect(screen.getByText("panel")).toBeTruthy();
    expect(
      screen
        .getByText("panel")
        .closest("aside")
        ?.style.getPropertyValue("--research-width"),
    ).toBe("clamp(360px, 40%, calc(100% - 488px))");
  });

  it("surfaces load failures with a retry action", () => {
    const onReload = vi.fn();
    render(
      <DocumentReaderView doc={null} error="读取失败" onReload={onReload} />,
    );

    expect(screen.getByText("读取失败")).toBeTruthy();
    // Queried by role alone: the error state renders exactly one button, and
    // its label comes from the real locale bundle (tests do not stub i18n).
    fireEvent.click(screen.getByRole("button"));
    expect(onReload).toHaveBeenCalled();
  });
});
