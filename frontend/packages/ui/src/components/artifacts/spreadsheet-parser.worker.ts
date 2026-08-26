/// <reference lib="webworker" />

import * as XLSX from "xlsx";

import {
  SPREADSHEET_COLUMN_WIDTH,
  SPREADSHEET_ROW_HEIGHT,
  type SpreadsheetCellStyle,
  type SpreadsheetMerge,
  type SpreadsheetSheetData,
  type SpreadsheetWorkerRequest,
  type SpreadsheetWorkerResponse,
} from "./spreadsheet-parser.types";
import { searchSpreadsheetRows } from "./spreadsheet-search";

let workbook: XLSX.WorkBook | null = null;
const parsedSheets = new Map<string, SpreadsheetSheetData>();

function post(response: SpreadsheetWorkerResponse): void {
  self.postMessage(response);
}

function cumulativeOffsets(sizes: number[]): number[] {
  const offsets = [0];
  sizes.forEach((size) => offsets.push(offsets[offsets.length - 1] + size));
  return offsets;
}

function colorFromSheetValue(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const color = value as Record<string, unknown>;
  const rgb = typeof color.rgb === "string" ? color.rgb : undefined;
  if (!rgb) return undefined;
  const normalized = rgb.length === 8 ? rgb.slice(2) : rgb;
  return /^[0-9a-fA-F]{6}$/.test(normalized) ? `#${normalized}` : undefined;
}

function cellStyleFromSheetCell(cell: XLSX.CellObject): SpreadsheetCellStyle {
  const rawStyle =
    "s" in cell && cell.s && typeof cell.s === "object"
      ? (cell.s as Record<string, unknown>)
      : {};
  const fill =
    rawStyle.fill && typeof rawStyle.fill === "object"
      ? (rawStyle.fill as Record<string, unknown>)
      : {};
  const font =
    rawStyle.font && typeof rawStyle.font === "object"
      ? (rawStyle.font as Record<string, unknown>)
      : {};
  const alignment =
    rawStyle.alignment && typeof rawStyle.alignment === "object"
      ? (rawStyle.alignment as Record<string, unknown>)
      : {};
  const border =
    rawStyle.border && typeof rawStyle.border === "object"
      ? (rawStyle.border as Record<string, unknown>)
      : {};
  const horizontal =
    typeof alignment.horizontal === "string" ? alignment.horizontal : undefined;

  return {
    backgroundColor:
      colorFromSheetValue(fill.fgColor) ?? colorFromSheetValue(fill.bgColor),
    color: colorFromSheetValue(font.color),
    fontWeight: font.bold ? 700 : undefined,
    justifyContent:
      horizontal === "center"
        ? "center"
        : horizontal === "right"
          ? "flex-end"
          : undefined,
    borderTop: Boolean(border.top),
    borderRight: Boolean(border.right),
    borderBottom: Boolean(border.bottom),
    borderLeft: Boolean(border.left),
  };
}

function parseSheet(name: string): SpreadsheetSheetData {
  if (!workbook) throw new Error("工作簿尚未加载。");
  const cached = parsedSheets.get(name);
  if (cached) return cached;
  const worksheet = workbook.Sheets[name];
  if (!worksheet) throw new Error(`找不到工作表：${name}`);

  const rawRows = XLSX.utils.sheet_to_json<unknown[]>(worksheet, {
    header: 1,
    defval: "",
    raw: false,
    blankrows: true,
  });
  const rows = rawRows.map((row) =>
    row.map((value) => (value == null ? "" : String(value))),
  );
  const merges: SpreadsheetMerge[] = (worksheet["!merges"] ?? []).map(
    (range) => ({
      startRow: range.s.r,
      startColumn: range.s.c,
      endRow: range.e.r,
      endColumn: range.e.c,
    }),
  );
  const refRange = worksheet["!ref"]
    ? XLSX.utils.decode_range(worksheet["!ref"])
    : null;
  const totalRows = Math.max(
    rows.length,
    refRange ? refRange.e.r + 1 : 0,
    ...merges.map((merge) => merge.endRow + 1),
    worksheet["!rows"]?.length ?? 0,
  );
  const totalColumns = Math.max(
    rows.reduce((max, row) => Math.max(max, row.length), 0),
    refRange ? refRange.e.c + 1 : 0,
    ...merges.map((merge) => merge.endColumn + 1),
    worksheet["!cols"]?.length ?? 0,
  );
  const columns = worksheet["!cols"] ?? [];
  const columnWidths = Array.from({ length: totalColumns }, (_, index) => {
    const width = columns[index];
    const rawWidth = width?.wpx ?? (width?.wch ? width.wch * 8 : undefined);
    if (!rawWidth || Number.isNaN(rawWidth)) return SPREADSHEET_COLUMN_WIDTH;
    return Math.min(Math.max(Math.round(rawWidth), 72), 320);
  });
  const worksheetRows = worksheet["!rows"] ?? [];
  const rowHeights = Array.from({ length: totalRows }, (_, index) => {
    const height = worksheetRows[index]?.hpx;
    if (!height || Number.isNaN(height)) return SPREADSHEET_ROW_HEIGHT;
    return Math.min(Math.max(Math.round(height), 24), 96);
  });
  const cellStyles: Array<[string, SpreadsheetCellStyle]> = [];
  for (const [address, cell] of Object.entries(worksheet)) {
    if (address.startsWith("!") || !cell || typeof cell !== "object") continue;
    const position = XLSX.utils.decode_cell(address);
    const style = cellStyleFromSheetCell(cell as XLSX.CellObject);
    if (Object.values(style).some(Boolean)) {
      cellStyles.push([`${position.r}:${position.c}`, style]);
    }
  }

  const sheet = {
    name,
    rows,
    totalRows,
    totalColumns,
    columnWidths,
    columnOffsets: cumulativeOffsets(columnWidths),
    rowHeights,
    rowOffsets: cumulativeOffsets(rowHeights),
    merges,
    cellStyles,
  };
  parsedSheets.set(name, sheet);
  return sheet;
}

self.onmessage = (event: MessageEvent<SpreadsheetWorkerRequest>) => {
  try {
    if (event.data.type === "load") {
      workbook = XLSX.read(event.data.buffer, {
        type: "array",
        cellDates: true,
        cellNF: true,
        cellStyles: true,
      });
      parsedSheets.clear();
      const sheetNames = workbook.SheetNames;
      const firstName = sheetNames[0];
      if (!firstName) throw new Error("工作簿中没有可显示的 sheet。");
      post({ type: "loaded", sheetNames, sheet: parseSheet(firstName) });
      return;
    }
    if (event.data.type === "sheet") {
      post({ type: "sheet", sheet: parseSheet(event.data.name) });
      return;
    }
    post({
      type: "search",
      name: event.data.name,
      requestId: event.data.requestId,
      matches: searchSpreadsheetRows(
        parseSheet(event.data.name).rows,
        event.data.query,
      ),
    });
  } catch (cause) {
    post({
      type: "error",
      message: cause instanceof Error ? cause.message : "无法解析表格文件。",
    });
  }
};
