import type { SpreadsheetCellRef } from "./spreadsheet-parser.types";

export function searchSpreadsheetRows(
  rows: string[][],
  query: string,
): SpreadsheetCellRef[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [];
  const matches: SpreadsheetCellRef[] = [];
  rows.forEach((row, rowIndex) => {
    row.forEach((cell, columnIndex) => {
      if (cell.toLowerCase().includes(normalized)) {
        matches.push({ row: rowIndex, column: columnIndex });
      }
    });
  });
  return matches;
}
