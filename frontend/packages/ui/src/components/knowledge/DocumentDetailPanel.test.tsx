/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

import { DocumentDetailPanel } from "./DocumentDetailPanel";
import { splitIntoUnits } from "./markdown-units";

describe("DocumentDetailPanel", () => {
  it("mounts only a window of a long preview", () => {
    // The point of the change this asserts. Rendering cost tracks the nodes
    // built, so a 40,000-cell spreadsheet took 19 s to open; mounting a
    // screenful holds it near 1.5 s. Not viewport-dependent: whatever the
    // container height, fewer units are mounted than the document has.
    const preview = `# README\n\n${"a paragraph\n\n".repeat(250)}`;

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

    const mounted = screen.getAllByTestId("markdown-content");
    expect(mounted.length).toBeGreaterThan(0);
    expect(mounted.length).toBeLessThan(splitIntoUnits(preview).length);
  });

  it("renders the mounted units verbatim and in order", () => {
    // Each unit is handed to the renderer alone, so a unit that lost a table
    // header or half a fence would render as garbage rather than fail.
    const preview = `# README\n\n\`\`\`text\n${"tree entry\n".repeat(250)}\`\`\``;
    const units = splitIntoUnits(preview);

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

    const mounted = screen
      .getAllByTestId("markdown-content")
      .map((n) => n.textContent);
    expect(mounted).toEqual(units.slice(0, mounted.length));
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

    expect(screen.getByText(/开头|beginning/i)).toBeTruthy();
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

    expect(screen.queryByText(/开头|beginning/i)).toBeNull();
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
