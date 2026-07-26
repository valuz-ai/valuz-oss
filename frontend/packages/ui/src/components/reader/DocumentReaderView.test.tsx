import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentReaderView } from "./DocumentReaderView";
import type { DocumentSource } from "./document-reader.types";

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
});

describe("DocumentReaderView", () => {
  it("renders the document header with source and publish time", () => {
    render(<DocumentReaderView doc={CHUNKS} />);

    expect(
      screen.getByRole("heading", { name: "Q2 earnings call" }),
    ).toBeTruthy();
    expect(screen.getByText("Acme Research")).toBeTruthy();
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
    await waitFor(() => expect(block?.className).toContain("bg-brand-light"));
  });

  // A stale or hand-edited deep link must land on the document, not on an error.
  it("stays silent when the requested chunk is missing", () => {
    render(<DocumentReaderView doc={CHUNKS} location={{ chunkId: "nope" }} />);

    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByText("Revenue grew.")).toBeTruthy();
  });

  // Segment-level links highlight the whole block: a lit-up sentence inside an
  // un-lit paragraph reads as a rendering glitch.
  it("highlights the parent block when a segment is requested", async () => {
    const { container } = render(
      <DocumentReaderView doc={CHUNKS} location={{ segmentId: "s2" }} />,
    );

    await waitFor(() =>
      expect(
        container.querySelector('[data-chunk-id="c3"]')?.className,
      ).toContain("bg-brand-light"),
    );
  });

  it("passes the page target through to the PDF renderer", async () => {
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
        location={{ page: 4 }}
      />,
    );

    await waitFor(() => {
      const frame = container.querySelector("iframe");
      expect(frame?.getAttribute("src")).toContain("page=4");
    });
  });

  it("renders the side panel slot only when supplied", () => {
    const { rerender } = render(<DocumentReaderView doc={CHUNKS} />);
    expect(screen.queryByText("panel")).toBeNull();

    rerender(<DocumentReaderView doc={CHUNKS} sidePanel={<div>panel</div>} />);
    expect(screen.getByText("panel")).toBeTruthy();
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
