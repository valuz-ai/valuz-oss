import { describe, expect, it } from "vitest";

import { countTableRows, splitIntoUnits } from "./markdown-units";

const table = (rows: number) =>
  "| Date | Close |\n|---|---|\n" +
  Array.from({ length: rows }, (_, i) => `| 1973-01-01 | ${i} |`).join("\n");

describe("splitIntoUnits", () => {
  it("cuts a long table along its rows", () => {
    // The whole point: a table is one block, so block-level splitting alone
    // leaves the document that stalls the panel in a single unit.
    const units = splitIntoUnits(table(100), 40);

    expect(units.length).toBe(3);
  });

  it("gives every table chunk its own header", () => {
    // Each unit is rendered on its own — a chunk without the header and
    // delimiter is not a table at all, it is pipes and text.
    const units = splitIntoUnits(table(100), 40);

    for (const unit of units) {
      expect(unit.startsWith("| Date | Close |\n|---|---|")).toBe(true);
    }
  });

  it("keeps every row exactly once", () => {
    const units = splitIntoUnits(table(100), 40);
    const rows = units
      .flatMap((u) => u.split("\n").slice(2))
      .filter((l) => l.trim() !== "");

    expect(rows.length).toBe(100);
    expect(new Set(rows).size).toBe(100);
  });

  it("leaves a short table whole", () => {
    expect(splitIntoUnits(table(5), 40)).toHaveLength(1);
  });

  it("does not cut a table drawn inside a code fence", () => {
    // It is text to display, not a table to paginate — and splitting it would
    // produce two unterminated fences.
    const doc = "```\n" + table(100) + "\n```";

    const units = splitIntoUnits(doc, 40);

    expect(units).toHaveLength(1);
  });

  it("does not mistake a leading pipe for a table", () => {
    // A delimiter row is required. Without this a paragraph that happens to
    // start with a pipe would be split mid-sentence.
    const doc = "| this is prose, not a table\nand it continues here";

    expect(splitIntoUnits(doc, 40)).toHaveLength(1);
  });

  it("splits prose on blank lines so units stay measurable", () => {
    const doc = "# Title\n\nfirst paragraph\n\nsecond paragraph";

    expect(splitIntoUnits(doc, 40).length).toBeGreaterThan(1);
  });

  it("keeps prose and table content in order", () => {
    const doc = `# Sheet A\n\nintro line\n\n${table(60)}\n\nclosing line`;

    const units = splitIntoUnits(doc, 40);
    const joined = units.join("\n");

    expect(joined.indexOf("intro line")).toBeLessThan(joined.indexOf("| 1973"));
    expect(joined.indexOf("| 1973")).toBeLessThan(joined.indexOf("closing line"));
  });

  it("returns nothing for an empty document", () => {
    expect(splitIntoUnits("")).toEqual([]);
  });
});

describe("countTableRows", () => {
  it("counts the body rows of a table", () => {
    expect(countTableRows(table(100))).toBe(100);
  });

  it("does not count the header or the delimiter", () => {
    expect(countTableRows(table(1))).toBe(1);
  });

  it("does not count a table drawn inside a code fence", () => {
    // It is text to display, not a table to render — counting it would send a
    // perfectly ordinary document down the windowed path and cost it
    // find-in-page for nothing.
    expect(countTableRows("```\n" + table(100) + "\n```")).toBe(0);
  });

  it("does not count prose that happens to start with a pipe", () => {
    expect(countTableRows("| not a table\nnor is this")).toBe(0);
  });

  it("adds up across several tables", () => {
    expect(countTableRows(`${table(10)}\n\nbetween\n\n${table(5)}`)).toBe(15);
  });

  it("is zero for a document with no table", () => {
    expect(countTableRows("# Title\n\njust prose")).toBe(0);
  });
});
