export const SPREADSHEET_ROW_HEIGHT = 32;
export const SPREADSHEET_COLUMN_WIDTH = 140;

export interface SpreadsheetMerge {
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
}

export interface SpreadsheetCellStyle {
  backgroundColor?: string;
  color?: string;
  fontWeight?: number;
  justifyContent?: "flex-start" | "center" | "flex-end";
  borderTop?: boolean;
  borderRight?: boolean;
  borderBottom?: boolean;
  borderLeft?: boolean;
}

export interface SpreadsheetCellRef {
  row: number;
  column: number;
}

export interface SpreadsheetSheetData {
  name: string;
  rows: string[][];
  totalRows: number;
  totalColumns: number;
  columnWidths: number[];
  columnOffsets: number[];
  rowHeights: number[];
  rowOffsets: number[];
  merges: SpreadsheetMerge[];
  cellStyles: Array<[string, SpreadsheetCellStyle]>;
}

export type SpreadsheetWorkerRequest =
  | { type: "load"; buffer: ArrayBuffer }
  | { type: "sheet"; name: string }
  | { type: "search"; name: string; query: string; requestId: number };

export type SpreadsheetWorkerResponse =
  | {
      type: "loaded";
      sheetNames: string[];
      sheet: SpreadsheetSheetData;
    }
  | { type: "sheet"; sheet: SpreadsheetSheetData }
  | {
      type: "search";
      name: string;
      requestId: number;
      matches: SpreadsheetCellRef[];
    }
  | { type: "error"; message: string };
