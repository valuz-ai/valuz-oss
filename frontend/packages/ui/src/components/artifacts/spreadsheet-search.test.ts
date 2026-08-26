import { describe, expect, it } from "vitest";

import { searchSpreadsheetRows } from "./spreadsheet-search";

describe("searchSpreadsheetRows", () => {
  it("finds case-insensitive matches in row order", () => {
    expect(
      searchSpreadsheetRows(
        [
          ["Alpha", "beta"],
          ["alphabet", "Gamma"],
        ],
        " ALPHA ",
      ),
    ).toEqual([
      { row: 0, column: 0 },
      { row: 1, column: 0 },
    ]);
  });

  it("does not scan for an empty query", () => {
    expect(searchSpreadsheetRows([["value"]], "   ")).toEqual([]);
  });
});
