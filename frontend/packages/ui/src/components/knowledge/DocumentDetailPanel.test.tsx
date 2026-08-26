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
});
