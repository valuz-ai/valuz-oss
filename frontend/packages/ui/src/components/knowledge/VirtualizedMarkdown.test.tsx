/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../conversation/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

import {
  TABLE_ROWS_BEFORE_WINDOWING,
  VirtualizedMarkdown,
} from "./VirtualizedMarkdown";
import { splitIntoUnits } from "./markdown-units";

const table = (rows: number) =>
  "| A | B |\n|---|---|\n" +
  Array.from({ length: rows }, (_, i) => `| r${i} | ${i} |`).join("\n");

describe("VirtualizedMarkdown", () => {
  it("hands an ordinary document to the renderer whole", () => {
    // Windowing costs find-in-page and anchor links: the browser cannot find
    // text that is not in the DOM. A document that renders fine should not pay
    // that, and prose is measured linear.
    const prose = "# Title\n\n" + "a paragraph\n\n".repeat(400);

    render(<VirtualizedMarkdown content={prose} />);

    const mounted = screen.getAllByTestId("markdown-content");
    expect(mounted).toHaveLength(1);
    expect(mounted[0].textContent).toBe(prose);
  });

  it("hands a small table over whole too", () => {
    const small = table(TABLE_ROWS_BEFORE_WINDOWING);

    render(<VirtualizedMarkdown content={small} />);

    expect(screen.getAllByTestId("markdown-content")).toHaveLength(1);
  });

  it("windows a document once it is mostly a large table", () => {
    // The case this exists for: cost tracks cells, and 40,000 of them took
    // 19 s to open before this.
    const big = table(TABLE_ROWS_BEFORE_WINDOWING * 10);

    render(<VirtualizedMarkdown content={big} />);

    const mounted = screen.getAllByTestId("markdown-content");
    expect(mounted.length).toBeGreaterThan(0);
    expect(mounted.length).toBeLessThan(splitIntoUnits(big).length);
  });

  it("renders the mounted units verbatim and in order", () => {
    // Each unit goes to the renderer alone, so a unit that lost its table
    // header would render as pipes and text rather than fail.
    const big = table(TABLE_ROWS_BEFORE_WINDOWING * 10);
    const units = splitIntoUnits(big);

    render(<VirtualizedMarkdown content={big} />);

    // A contiguous ascending run of the units, each one verbatim. Not "the
    // first N" — where the window lands depends on measured heights, and in
    // jsdom everything measures 0.
    const indices = screen
      .getAllByTestId("markdown-content")
      .map((n) => units.indexOf(n.textContent ?? ""));

    expect(indices.length).toBeGreaterThan(0);
    expect(indices).not.toContain(-1);
    expect(indices).toEqual(
      indices.map((_, i) => indices[0] + i),
    );
  });

  it("does not count a table drawn inside a code fence", () => {
    // It is text to display. Windowing it would split the fence in half.
    const fenced = "```\n" + table(TABLE_ROWS_BEFORE_WINDOWING * 10) + "\n```";

    render(<VirtualizedMarkdown content={fenced} />);

    expect(screen.getAllByTestId("markdown-content")).toHaveLength(1);
  });
});
