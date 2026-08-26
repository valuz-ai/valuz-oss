/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

import { DocumentDetailPanel } from "./DocumentDetailPanel";

describe("DocumentDetailPanel", () => {
  it("shows the preview through the shared file viewer", async () => {
    // Not a bare renderer. The viewer is what brings the preview/source
    // toggle, the reading column and the padding — a hand-rolled markdown
    // block next to a document looked unstyled and could not show the source.
    const preview = "# README\n\nprose";

    render(
      <DocumentDetailPanel
        doc={{
          name: "README.md",
          format: "MARKDOWN",
          status: "ready",
          preview: { markdown: preview, truncated: false },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /source|源/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /preview|预览/i })).toBeTruthy();
    expect(screen.getByTestId("markdown-content").textContent).toContain(
      "README",
    );
  });

  it("says so when the server returned only a window of a large document", () => {
    // The flag is measured server-side now. It used to be a hardcoded
    // ``false`` on text read whole off disk, and one 1.05 MB spreadsheet
    // preview was enough to hang the tab.
    render(
      <DocumentDetailPanel
        doc={{
          name: "big.xlsx",
          format: "XLSX",
          status: "ready",
          preview: { markdown: "# part one", truncated: true },
        }}
      />,
    );

    expect(screen.getByText(/MiB|较大|large/i)).toBeTruthy();
  });

  it("says nothing when the whole document fits", () => {
    render(
      <DocumentDetailPanel
        doc={{
          name: "small.md",
          format: "MARKDOWN",
          status: "ready",
          preview: { markdown: "# all of it", truncated: false },
        }}
      />,
    );

    expect(screen.queryByText(/MiB|较大|large/i)).toBeNull();
  });

  it("keeps document actions in a dedicated footer", () => {
    render(
      <DocumentDetailPanel
        doc={{ name: "README.md", format: "MARKDOWN", status: "ready" }}
        onRegenerate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /rebuild|重建/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /delete|删除/i })).toBeTruthy();
  });

  it("uses one 24-hour time format for document and parser timestamps", () => {
    render(
      <DocumentDetailPanel
        doc={{ name: "README.md", format: "MARKDOWN", status: "ready" }}
        meta={{ importedAt: Date.parse("2026-07-22T10:13:00Z") }}
        parse={{
          parserMode: "light_local",
          attempts: [
            {
              pluginId: "light_local",
              error: "",
              occurredAt: "2026-07-22T10:13:17Z",
              ok: true,
            },
          ],
        }}
      />,
    );

    expect(screen.queryByText(/\b(?:AM|PM)\b/i)).toBeNull();
  });

  // ── The parse history ────────────────────────────────────────────────

  it("shows the newest attempt first", () => {
    // A document that failed once and then parsed is ``ready``, and the panel
    // showed the failure — the collapsed history takes the *first* entry, and
    // attempts are stored oldest-first.
    render(
      <DocumentDetailPanel
        doc={{ name: "a.pdf", format: "PDF", status: "ready" }}
        parse={{
          attempts: [
            {
              pluginId: "valuz_ocr",
              error: "parse failed",
              occurredAt: "2026-08-21T01:57:00Z",
              ok: false,
            },
            {
              pluginId: "valuz_ocr",
              error: "",
              occurredAt: "2026-08-21T02:10:00Z",
              ok: true,
            },
          ],
        }}
      />,
    );

    expect(screen.queryByText("parse failed")).toBeNull();
  });

  it("renders an epoch-millisecond attempt time as a time", () => {
    // What the cloud pipeline wrote. ``new Date("1787303620297")`` is an
    // Invalid Date, so the panel printed the raw number at the user.
    render(
      <DocumentDetailPanel
        doc={{ name: "a.pdf", format: "PDF", status: "failed" }}
        parse={{
          attempts: [
            {
              pluginId: "valuz_ocr",
              error: "parse failed",
              occurredAt: "1787303620297",
              ok: false,
            },
          ],
        }}
      />,
    );

    expect(screen.queryByText(/1787303620297/)).toBeNull();
    expect(screen.getByText(/\d{2}:\d{2}:\d{2}/)).toBeTruthy();
  });

  it("still renders an ISO attempt time", () => {
    render(
      <DocumentDetailPanel
        doc={{ name: "a.pdf", format: "PDF", status: "failed" }}
        parse={{
          attempts: [
            {
              pluginId: "light_local",
              error: "boom",
              occurredAt: "2026-08-21T01:57:03Z",
              ok: false,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText(/\d{2}:\d{2}:\d{2}/)).toBeTruthy();
  });
});
