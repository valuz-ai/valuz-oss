import { useVirtualizer } from "@tanstack/react-virtual";
import { Loader2, Search } from "lucide-react";
import {
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ArtifactDescriptor,
  ArtifactRendererProps,
} from "./artifact-viewer.types";
import {

  SPREADSHEET_COLUMN_WIDTH,
  SPREADSHEET_ROW_HEIGHT,
  type SpreadsheetCellRef,
  type SpreadsheetCellStyle,
  type SpreadsheetMerge,
  type SpreadsheetSheetData,
  type SpreadsheetWorkerRequest,
  type SpreadsheetWorkerResponse,
} from "./spreadsheet-parser.types";

import { t as _t } from "@valuz/shared/i18n";
import { useI18n } from "../../hooks/use-i18n";

type SpreadsheetSheet = Omit<SpreadsheetSheetData, "cellStyles"> & {
  cellStyles: Map<string, SpreadsheetCellStyle>;
};

type SpreadsheetSelection = {
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
};

const ROW_HEADER_WIDTH = 52;
const COLUMN_HEADER_HEIGHT = 32;

function columnLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    label = String.fromCharCode(65 + remainder) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}

function mergeAt(
  sheet: SpreadsheetSheet,
  row: number,
  column: number,
): SpreadsheetMerge | null {
  return (
    sheet.merges.find(
      (merge) =>
        row >= merge.startRow &&
        row <= merge.endRow &&
        column >= merge.startColumn &&
        column <= merge.endColumn,
    ) ?? null
  );
}

function isMergeOrigin(merge: SpreadsheetMerge, row: number, column: number) {
  return merge.startRow === row && merge.startColumn === column;
}

function cellBoxShadow(
  selected: boolean,
  cellStyle?: SpreadsheetCellStyle,
): string | undefined {
  const shadows: string[] = [];
  const borderColor = "var(--color-ink-heading)";
  if (cellStyle?.borderTop) shadows.push(`inset 0 1px 0 ${borderColor}`);
  if (cellStyle?.borderRight) shadows.push(`inset -1px 0 0 ${borderColor}`);
  if (cellStyle?.borderBottom) shadows.push(`inset 0 -1px 0 ${borderColor}`);
  if (cellStyle?.borderLeft) shadows.push(`inset 1px 0 0 ${borderColor}`);
  if (selected) {
    shadows.push(
      "inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 55%, transparent)",
    );
  }
  return shadows.length ? shadows.join(", ") : undefined;
}

function normalizeSelection(selection: SpreadsheetSelection): SpreadsheetSelection {
  return {
    startRow: Math.min(selection.startRow, selection.endRow),
    endRow: Math.max(selection.startRow, selection.endRow),
    startColumn: Math.min(selection.startColumn, selection.endColumn),
    endColumn: Math.max(selection.startColumn, selection.endColumn),
  };
}

function isCellSelected(
  selection: SpreadsheetSelection | null,
  row: number,
  column: number,
): boolean {
  if (!selection) return false;
  const normalized = normalizeSelection(selection);
  return (
    row >= normalized.startRow &&
    row <= normalized.endRow &&
    column >= normalized.startColumn &&
    column <= normalized.endColumn
  );
}

function selectionToText(
  sheet: SpreadsheetSheet,
  selection: SpreadsheetSelection,
): string {
  const normalized = normalizeSelection(selection);
  return Array.from(
    { length: normalized.endRow - normalized.startRow + 1 },
    (_, rowOffset) => {
      const rowIndex = normalized.startRow + rowOffset;
      return Array.from(
        { length: normalized.endColumn - normalized.startColumn + 1 },
        (_, columnOffset) =>
          sheet.rows[rowIndex]?.[normalized.startColumn + columnOffset] ?? "",
      ).join("\t");
    },
  ).join("\n");
}

function hydrateSheet(data: SpreadsheetSheetData): SpreadsheetSheet {
  return { ...data, cellStyles: new Map(data.cellStyles) };
}

function SpreadsheetUnavailable({ artifact }: { artifact: ArtifactDescriptor }) {
  const { t } = useI18n();
  return (
    <div className="flex h-full items-center justify-center px-6 py-16">
      <div className="max-w-[420px] rounded-lg border border-surface-border bg-surface-soft px-5 py-4">
        <div className="text-sm font-medium text-ink-heading">
          {t("ui.artifact.sheetReadFailed")}
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-body">{artifact.name}</p>
      </div>
    </div>
  );
}

export function SpreadsheetRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const [sheets, setSheets] = useState<SpreadsheetSheet[]>([]);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheetName, setActiveSheetName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [searchMatches, setSearchMatches] = useState<SpreadsheetCellRef[]>([]);
  const [selection, setSelection] = useState<SpreadsheetSelection | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [scrollOffset, setScrollOffset] = useState({ left: 0, top: 0 });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const searchRequestIdRef = useRef(0);
  const openUrl = content?.kind === "binary" ? content.openUrl : null;
  const deferredSearchQuery = useDeferredValue(searchQuery);

  useEffect(() => {
    if (!openUrl) return;
    const workbookUrl = openUrl;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setSheets([]);
    setSheetNames([]);
    setActiveSheetName(null);
    setSearchMatches([]);
    const worker = new Worker(
      new URL("./spreadsheet-parser.worker.ts", import.meta.url),
      { type: "module" },
    );
    workerRef.current = worker;

    worker.onmessage = (event: MessageEvent<SpreadsheetWorkerResponse>) => {
      if (controller.signal.aborted) return;
      const message = event.data;
      if (message.type === "error") {
        setError(message.message);
        setLoading(false);
        return;
      }
      if (message.type === "search") {
        if (message.requestId === searchRequestIdRef.current) {
          setSearchMatches(message.matches);
        }
        return;
      }
      const sheet = hydrateSheet(message.sheet);
      setSheets((current) => [
        ...current.filter((item) => item.name !== sheet.name),
        sheet,
      ]);
      if (message.type === "loaded") setSheetNames(message.sheetNames);
      setActiveSheetName(sheet.name);
      setLoading(false);
    };
    worker.onerror = () => {
      if (controller.signal.aborted) return;
      setError(_t("ui.artifact.sheetParserFailed"));
      setLoading(false);
    };

    async function loadWorkbook() {
      try {
        const response = await fetch(workbookUrl, {
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(
            _t("ui.artifact.httpReadFailed", { status: response.status }),
          );
        const buffer = await response.arrayBuffer();
        const message: SpreadsheetWorkerRequest = { type: "load", buffer };
        worker.postMessage(message, [buffer]);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof Error ? cause.message : _t("ui.artifact.sheetParseError"),
        );
        setLoading(false);
      }
    }

    void loadWorkbook();
    return () => {
      controller.abort();
      worker.terminate();
      if (workerRef.current === worker) workerRef.current = null;
    };
  }, [openUrl]);

  const activeSheet =
    sheets.find((sheet) => sheet.name === activeSheetName) ?? sheets[0] ?? null;
  // TanStack Virtual intentionally exposes imperative functions that React
  // Compiler cannot memoize. Keep that compatibility boundary in this module.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: activeSheet?.totalRows ?? 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) =>
      activeSheet?.rowHeights[index] ?? SPREADSHEET_ROW_HEIGHT,
    overscan: 12,
  });
  const columnVirtualizer = useVirtualizer({
    count: activeSheet?.totalColumns ?? 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) =>
      activeSheet?.columnWidths[index] ?? SPREADSHEET_COLUMN_WIDTH,
    horizontal: true,
    overscan: 4,
  });

  const searchSheetName = activeSheet?.name ?? null;
  useEffect(() => {
    const requestId = searchRequestIdRef.current + 1;
    searchRequestIdRef.current = requestId;
    setSearchIndex(0);
    const query = deferredSearchQuery.trim();
    const worker = workerRef.current;
    if (!searchSheetName || !query || !worker) {
      setSearchMatches([]);
      return;
    }
    const message: SpreadsheetWorkerRequest = {
      type: "search",
      name: searchSheetName,
      query,
      requestId,
    };
    worker.postMessage(message);
  }, [deferredSearchQuery, searchSheetName]);

  useEffect(() => {
    const match = searchMatches[searchIndex];
    if (!match) return;
    rowVirtualizer.scrollToIndex(match.row, { align: "center" });
    columnVirtualizer.scrollToIndex(match.column, { align: "center" });
    setSelection({
      startRow: match.row,
      endRow: match.row,
      startColumn: match.column,
      endColumn: match.column,
    });
  }, [columnVirtualizer, rowVirtualizer, searchIndex, searchMatches]);

  const copySelection = async () => {
    if (!activeSheet || !selection) return;
    await navigator.clipboard.writeText(selectionToText(activeSheet, selection));
    setCopyStatus(_t("ui.artifact.copied"));
    window.setTimeout(() => setCopyStatus(null), 1400);
  };

  const selectWorkbookSheet = (name: string) => {
    setSelection(null);
    const cached = sheets.find((sheet) => sheet.name === name);
    if (cached) {
      setActiveSheetName(name);
      return;
    }
    const worker = workerRef.current;
    if (!worker) return;
    setLoading(true);
    const message: SpreadsheetWorkerRequest = { type: "sheet", name };
    worker.postMessage(message);
  };

  const selectCell = (
    row: number,
    column: number,
    extendSelection: boolean,
  ) => {
    const merge = activeSheet ? mergeAt(activeSheet, row, column) : null;
    const target = merge
      ? {
          startRow: merge.startRow,
          endRow: merge.endRow,
          startColumn: merge.startColumn,
          endColumn: merge.endColumn,
        }
      : { startRow: row, endRow: row, startColumn: column, endColumn: column };
    setSelection((current) => {
      if (!extendSelection || !current) return target;
      return {
        ...current,
        endRow: target.endRow,
        endColumn: target.endColumn,
      };
    });
  };

  if (!openUrl) return <SpreadsheetUnavailable artifact={artifact} />;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-meta" role="status">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        {t("ui.artifact.sheetParsing")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div className="max-w-[420px] rounded-xl border border-error-light bg-error-light px-5 py-4 text-error-text" role="alert">
          <div className="text-sm font-medium">{t("ui.artifact.sheetPreviewFailed")}</div>
          <p className="mt-1 text-xs leading-5">{error}</p>
        </div>
      </div>
    );
  }

  if (!activeSheet) return <SpreadsheetUnavailable artifact={artifact} />;

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-surface-border bg-surface px-4">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
          {sheetNames.map((sheetName) => (
            <button
              key={sheetName}
              type="button"
              onClick={() => selectWorkbookSheet(sheetName)}
              className={`h-7 max-w-[180px] shrink-0 truncate rounded-md px-3 text-xs transition ${
                sheetName === activeSheet.name
                  ? "bg-surface-soft text-ink-heading"
                  : "text-ink-body hover:bg-surface-muted hover:text-ink-heading"
              }`}
              title={sheetName}
            >
              {sheetName}
            </button>
          ))}
        </div>
        <div className="ml-3 flex shrink-0 items-center gap-2">
          <label className="flex h-7 w-[220px] items-center rounded-md border border-surface-border bg-surface px-2 text-xs text-ink-body">
            <Search className="mr-1.5 h-3.5 w-3.5 text-ink-meta" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={t("ui.artifact.searchCells")}
              aria-label={t("ui.artifact.searchCells")}
              className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-ink-meta"
            />
          </label>
          {searchQuery.trim() ? (
            <div className="flex h-7 items-center gap-1 text-xs text-ink-meta">
              <span>
                {searchMatches.length ? searchIndex + 1 : 0}/
                {searchMatches.length}
              </span>
              <button
                type="button"
                onClick={() =>
                  setSearchIndex((index) =>
                    searchMatches.length
                      ? (index - 1 + searchMatches.length) % searchMatches.length
                      : 0,
                  )
                }
                disabled={!searchMatches.length}
                className="rounded px-1.5 py-0.5 hover:bg-surface-muted disabled:opacity-40"
              >
                {t("ui.artifact.prevMatch")}
              </button>
              <button
                type="button"
                onClick={() =>
                  setSearchIndex((index) =>
                    searchMatches.length ? (index + 1) % searchMatches.length : 0,
                  )
                }
                disabled={!searchMatches.length}
                className="rounded px-1.5 py-0.5 hover:bg-surface-muted disabled:opacity-40"
              >
                {t("ui.artifact.nextMatch")}
              </button>
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => void copySelection()}
            disabled={!selection}
            className="h-7 rounded-md px-2 text-xs text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
          >
            {copyStatus ?? t("ui.artifact.copy")}
          </button>
          <div className="shrink-0 text-xs text-ink-meta">
            {activeSheet.totalRows} rows · {activeSheet.totalColumns} cols
          </div>
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={(event) =>
          setScrollOffset({
            left: event.currentTarget.scrollLeft,
            top: event.currentTarget.scrollTop,
          })
        }
        className="min-h-0 flex-1 overflow-auto bg-surface"
      >
        <div
          className="relative text-xs"
          style={{
            width: ROW_HEADER_WIDTH + columnVirtualizer.getTotalSize(),
            height: COLUMN_HEADER_HEIGHT + rowVirtualizer.getTotalSize(),
          }}
        >
          <div
            className="absolute left-0 top-0 z-30 border-b border-r border-surface-border bg-surface-soft"
            style={{
              left: scrollOffset.left,
              top: scrollOffset.top,
              width: ROW_HEADER_WIDTH,
              height: COLUMN_HEADER_HEIGHT,
            }}
          />
          {columnVirtualizer.getVirtualItems().map((virtualColumn) => (
            <div
              key={virtualColumn.key}
              className="absolute top-0 z-20 flex items-center border-b border-r border-surface-border bg-surface-soft px-3 font-medium text-ink-meta"
              onClick={() =>
                setSelection({
                  startRow: 0,
                  endRow: Math.max(activeSheet.totalRows - 1, 0),
                  startColumn: virtualColumn.index,
                  endColumn: virtualColumn.index,
                })
              }
              style={{
                top: scrollOffset.top,
                left: ROW_HEADER_WIDTH + virtualColumn.start,
                width: virtualColumn.size,
                height: COLUMN_HEADER_HEIGHT,
                cursor: "pointer",
              }}
            >
              {columnLabel(virtualColumn.index)}
            </div>
          ))}
          {rowVirtualizer.getVirtualItems().map((virtualRow) => (
            <div
              key={virtualRow.key}
              className="absolute left-0 z-10 flex items-center justify-end border-b border-r border-surface-border bg-surface-soft px-2 font-normal text-ink-meta"
              onClick={() =>
                setSelection({
                  startRow: virtualRow.index,
                  endRow: virtualRow.index,
                  startColumn: 0,
                  endColumn: Math.max(activeSheet.totalColumns - 1, 0),
                })
              }
              style={{
                top: COLUMN_HEADER_HEIGHT + virtualRow.start,
                left: scrollOffset.left,
                width: ROW_HEADER_WIDTH,
                height: virtualRow.size,
                cursor: "pointer",
              }}
            >
              {virtualRow.index + 1}
            </div>
          ))}
          {rowVirtualizer.getVirtualItems().map((virtualRow) =>
            columnVirtualizer.getVirtualItems().map((virtualColumn) => {
              const merge = mergeAt(
                activeSheet,
                virtualRow.index,
                virtualColumn.index,
              );
              if (
                merge &&
                !isMergeOrigin(merge, virtualRow.index, virtualColumn.index)
              ) {
                return null;
              }
              const cell =
                activeSheet.rows[virtualRow.index]?.[virtualColumn.index] ?? "";
              const cellStyle = activeSheet.cellStyles.get(
                `${virtualRow.index}:${virtualColumn.index}`,
              );
              const selected = isCellSelected(
                selection,
                virtualRow.index,
                virtualColumn.index,
              );
              const matched =
                searchMatches[searchIndex]?.row === virtualRow.index &&
                searchMatches[searchIndex]?.column === virtualColumn.index;
              const cellWidth = merge
                ? activeSheet.columnOffsets[merge.endColumn + 1] -
                  activeSheet.columnOffsets[merge.startColumn]
                : virtualColumn.size;
              const cellHeight = merge
                ? activeSheet.rowOffsets[merge.endRow + 1] -
                  activeSheet.rowOffsets[merge.startRow]
                : virtualRow.size;
              return (
                <div
                  key={`${virtualRow.key}-${virtualColumn.key}`}
                  className="absolute flex items-center overflow-hidden border-b border-r border-surface-border px-3 text-ink-heading"
                  onClick={(event) =>
                    selectCell(
                      virtualRow.index,
                      virtualColumn.index,
                      event.shiftKey,
                    )
                  }
                  title={cell}
                  style={{
                    top: COLUMN_HEADER_HEIGHT + virtualRow.start,
                    left: ROW_HEADER_WIDTH + virtualColumn.start,
                    width: cellWidth,
                    height: cellHeight,
                    backgroundColor: selected
                      ? "color-mix(in srgb, var(--color-primary) 12%, var(--color-surface))"
                      : matched
                        ? "color-mix(in srgb, var(--color-warning) 24%, var(--color-surface))"
                        : cellStyle?.backgroundColor
                          ? cellStyle.backgroundColor
                          : virtualRow.index % 2 === 0
                            ? "var(--color-surface)"
                            : "var(--color-surface-soft)",
                    boxShadow: cellBoxShadow(selected, cellStyle),
                    color: cellStyle?.color,
                    fontWeight: cellStyle?.fontWeight,
                    justifyContent: cellStyle?.justifyContent,
                    cursor: "cell",
                  }}
                >
                  <div className="truncate">{cell}</div>
                </div>
              );
            }),
          )}
        </div>
      </div>
    </div>
  );
}
