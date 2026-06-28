import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { yaml } from "@codemirror/lang-yaml";
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useVirtualizer } from "@tanstack/react-virtual";
import { basicSetup } from "codemirror";
import {
  Copy,
  ExternalLink,
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import * as XLSX from "xlsx";
import { MarkdownContent } from "../conversation/MarkdownContent";

export type ArtifactPreviewKind =
  | "markdown"
  | "code"
  | "image"
  | "pdf"
  | "html"
  | "docx"
  | "media"
  | "spreadsheet"
  | "plain"
  | "unsupported";

export interface ArtifactDescriptor {
  id: string;
  kind: string;
  projectId?: string;
  path?: string;
  name: string;
  mimeType?: string | null;
  extension?: string | null;
  size?: number | null;
  modifiedAt?: string | null;
  previewKind: ArtifactPreviewKind;
  capabilities: {
    canPreview: boolean;
    canEdit: boolean;
    canOpenExternal: boolean;
    canCopyContent: boolean;
    canDownload: boolean;
  };
}

export type ArtifactContent =
  | {
      kind: "text";
      encoding: "utf-8";
      content: string;
      truncated: boolean;
      etag?: string | null;
      modifiedAt?: string | null;
    }
  | {
      kind: "binary";
      openUrl: string;
      mimeType: string;
      size?: number | null;
      reason?: string | null;
    }
  | {
      kind: "external";
      openUrl?: string | null;
      reason: string;
    };

export interface ArtifactViewerShellProps {
  artifact: ArtifactDescriptor | null;
  content: ArtifactContent | null;
  loading?: boolean;
  error?: string | null;
  onReload?: () => void;
  onClose?: () => void;
  onCopyContent?: () => void;
  onOpenExternal?: () => void;
}

type ArtifactRendererProps = {
  artifact: ArtifactDescriptor;
  content: ArtifactContent | null;
  onOpenExternal?: () => void;
};

type ArtifactRendererComponent = (props: ArtifactRendererProps) => ReactNode;
type PreviewSourceMode = "preview" | "source";

const PREVIEW_LABELS: Record<ArtifactPreviewKind, string> = {
  markdown: "Markdown",
  code: "Code",
  image: "Image",
  pdf: "PDF",
  html: "HTML",
  docx: "DOCX",
  media: "Media",
  spreadsheet: "Spreadsheet",
  plain: "Text",
  unsupported: "File",
};

function formatBytes(value?: number | null): string | null {
  if (value == null) return null;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatModified(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

function codeMirrorLanguageForPath(path: string): Extension[] {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "ts":
      return [javascript({ typescript: true })];
    case "tsx":
      return [javascript({ jsx: true, typescript: true })];
    case "js":
      return [javascript()];
    case "jsx":
      return [javascript({ jsx: true })];
    case "py":
      return [python()];
    case "json":
      return [json()];
    case "yaml":
    case "yml":
      return [yaml()];
    case "html":
    case "htm":
      return [html()];
    case "css":
    case "scss":
      return [css()];
    case "md":
    case "markdown":
      return [markdown()];
    case "sql":
      return [sql()];
    default:
      return [];
  }
}

function ArtifactIcon({ kind }: { kind: ArtifactPreviewKind }) {
  if (kind === "markdown" || kind === "plain") {
    return <FileText className="h-4 w-4 text-ink-meta" />;
  }
  if (kind === "code") {
    return <FileCode2 className="h-4 w-4 text-ink-meta" />;
  }
  if (kind === "image") {
    return <FileImage className="h-4 w-4 text-ink-meta" />;
  }
  if (kind === "docx") {
    return <FileText className="h-4 w-4 text-ink-meta" />;
  }
  if (kind === "spreadsheet") {
    return <FileSpreadsheet className="h-4 w-4 text-ink-meta" />;
  }
  return <File className="h-4 w-4 text-ink-meta" />;
}

function PreviewSourceToggle({
  mode,
  onModeChange,
}: {
  mode: PreviewSourceMode;
  onModeChange: (mode: PreviewSourceMode) => void;
}) {
  return (
    <div className="inline-flex h-7 items-center rounded-md border border-surface-border bg-surface p-0.5 text-xs shadow-sm">
      {(["preview", "source"] as const).map((item) => (
        <button
          key={item}
          type="button"
          onClick={() => onModeChange(item)}
          className={`h-6 rounded-[5px] px-2.5 transition ${
            mode === item
              ? "bg-surface-soft text-ink-heading shadow-sm"
              : "text-ink-body hover:text-ink-heading"
          }`}
        >
          {item === "preview" ? "Preview" : "Source"}
        </button>
      ))}
    </div>
  );
}

function EmptyArtifactState() {
  return (
    <div className="flex h-full items-center justify-center px-6 py-16">
      <div className="max-w-[360px] text-center">
        <FileText className="mx-auto mb-3 h-8 w-8 text-ink-muted" />
        <div className="text-sm font-medium text-ink-heading">
          选择一个项目文件
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-body">
          在右侧项目文件树中点击文件后，会在这里以 Artifact 方式打开。
        </p>
      </div>
    </div>
  );
}

function UnsupportedRenderer({
  artifact,
  content,
  onOpenExternal,
}: {
  artifact: ArtifactDescriptor;
  content: ArtifactContent | null;
  onOpenExternal?: () => void;
}) {
  const reason =
    content?.kind === "external"
      ? content.reason
      : content?.kind === "binary" && content.reason
        ? content.reason
        : "当前类型暂未注册内嵌 renderer。";
  return (
    <div className="flex h-full items-center justify-center px-6 py-16">
      <div className="max-w-[460px] rounded-[10px] border border-surface-border bg-surface-soft px-5 py-5">
        <div className="flex items-center gap-2 text-sm font-medium text-ink-heading">
          <ArtifactIcon kind={artifact.previewKind} />
          暂不支持内嵌预览
        </div>
        <p className="mt-2 text-xs leading-5 text-ink-body">{reason}</p>
        <div className="mt-4 grid grid-cols-[96px_1fr] gap-x-3 gap-y-1 text-2xs">
          <span className="text-ink-meta">文件名</span>
          <span className="min-w-0 truncate text-ink-heading">{artifact.name}</span>
          <span className="text-ink-meta">路径</span>
          <span className="min-w-0 truncate text-ink-heading">{artifact.path}</span>
          <span className="text-ink-meta">类型</span>
          <span className="text-ink-heading">
            {artifact.mimeType ?? artifact.extension ?? "unknown"}
          </span>
        </div>
        {onOpenExternal && artifact.capabilities.canOpenExternal ? (
          <div className="mt-5 flex justify-center border-t border-surface-border pt-4">
            <button
              type="button"
              onClick={onOpenExternal}
              className="inline-flex h-8 items-center rounded-md border border-surface-border bg-surface px-3 text-xs font-medium text-ink-heading transition hover:bg-surface-muted"
            >
              本地打开
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CodeMirrorRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const languageExtensions = useMemo(
    () => codeMirrorLanguageForPath(artifact.path ?? artifact.name),
    [artifact.name, artifact.path],
  );
  const extensions = useMemo<Extension[]>(
    () => [
      basicSetup,
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
      EditorView.lineWrapping,
      EditorView.theme({
        "&": {
          height: "100%",
          backgroundColor: "transparent",
          color: "#23272f",
        },
        ".cm-scroller": {
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          fontSize: "12px",
          lineHeight: "1.6",
        },
        ".cm-gutters": {
          backgroundColor: "#f8fafc",
          borderRight: "1px solid #e6e7e9",
          color: "#8993a4",
        },
        ".cm-activeLine": {
          backgroundColor: "rgba(114, 92, 249, 0.06)",
        },
        ".cm-activeLineGutter": {
          backgroundColor: "rgba(114, 92, 249, 0.06)",
        },
        ".cm-selectionBackground": {
          backgroundColor: "rgba(114, 92, 249, 0.18) !important",
        },
        ".cm-content": {
          padding: "16px 0",
        },
        ".cm-line": {
          padding: "0 16px",
        },
      }),
      ...languageExtensions,
    ],
    [languageExtensions],
  );

  useEffect(() => {
    if (!containerRef.current || !content || content.kind !== "text") return;
    const view = new EditorView({
      parent: containerRef.current,
      state: EditorState.create({
        doc: content.content,
        extensions,
      }),
    });
    return () => view.destroy();
  }, [content, extensions]);

  if (!content || content.kind !== "text") {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      <div ref={containerRef} className="min-h-0 flex-1 overflow-hidden" />
      {content.truncated ? (
        <div className="border-t border-surface-border bg-warning-light px-4 py-2 text-xs text-warning-text">
          文件较大，当前仅显示前 5 MiB。
        </div>
      ) : null}
    </div>
  );
}

function TextRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const [markdownMode, setMarkdownMode] =
    useState<PreviewSourceMode>("preview");
  if (!content || content.kind !== "text") {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  if (artifact.previewKind === "markdown" && markdownMode === "preview") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">Markdown</span>
          <PreviewSourceToggle mode={markdownMode} onModeChange={setMarkdownMode} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-8 py-7">
          <div className="mx-auto max-w-[820px]">
            {content.truncated ? (
              <div className="mb-4 rounded-md border border-warning-light bg-warning-light px-3 py-2 text-xs text-warning-text">
                文件较大，当前仅显示前 5 MiB。
              </div>
            ) : null}
            <MarkdownContent content={content.content} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {artifact.previewKind === "markdown" ? (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">Markdown</span>
          <PreviewSourceToggle mode={markdownMode} onModeChange={setMarkdownMode} />
        </div>
      ) : null}
      <pre className="min-h-0 flex-1 overflow-auto bg-surface-base p-4 font-mono text-xs leading-6 text-ink-heading">
        {content.content}
      </pre>
      {content.truncated ? (
        <div className="border-t border-surface-border bg-warning-light px-4 py-2 text-xs text-warning-text">
          文件较大，当前仅显示前 5 MiB。
        </div>
      ) : null}
    </div>
  );
}

function ImageRenderer({ artifact, content }: ArtifactRendererProps) {
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [dragStart, setDragStart] = useState<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);

  const zoomValue = zoom === "fit" ? 1 : zoom;
  const updateZoom = (nextZoom: number) => {
    setZoom(Math.min(Math.max(nextZoom, 0.25), 4));
  };

  const handleDragStart = (event: MouseEvent<HTMLDivElement>) => {
    if (!viewportRef.current || zoom === "fit" || zoom <= 1) return;
    setDragStart({
      x: event.clientX,
      y: event.clientY,
      left: viewportRef.current.scrollLeft,
      top: viewportRef.current.scrollTop,
    });
  };

  const handleDragMove = (event: MouseEvent<HTMLDivElement>) => {
    if (!dragStart || !viewportRef.current) return;
    viewportRef.current.scrollLeft = dragStart.left - (event.clientX - dragStart.x);
    viewportRef.current.scrollTop = dragStart.top - (event.clientY - dragStart.y);
  };

  if (content?.kind === "binary" && content.mimeType.startsWith("image/")) {
    return (
      <div className="flex h-full min-h-0 flex-col bg-surface-base">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">Image</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => updateZoom(zoomValue - 0.25)}
              className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
              disabled={zoom !== "fit" && zoom <= 0.25}
              title="缩小"
            >
              <ZoomOut className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setZoom("fit")}
              className="h-7 min-w-12 rounded-md px-2 text-xs text-ink-body transition hover:bg-surface-muted hover:text-ink-heading"
              title="适合窗口"
            >
              {zoom === "fit" ? "Fit" : `${Math.round(zoom * 100)}%`}
            </button>
            <button
              type="button"
              onClick={() => updateZoom(zoomValue + 0.25)}
              className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
              disabled={zoom !== "fit" && zoom >= 4}
              title="放大"
            >
              <ZoomIn className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <div
          ref={viewportRef}
          onMouseDown={handleDragStart}
          onMouseMove={handleDragMove}
          onMouseUp={() => setDragStart(null)}
          onMouseLeave={() => setDragStart(null)}
          className={`min-h-0 flex-1 overflow-auto bg-surface-base p-6 ${
            zoom !== "fit" && zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""
          }`}
        >
          <div className="flex min-h-full min-w-full items-center justify-center">
            <img
              src={content.openUrl}
              alt={artifact.name}
              draggable={false}
              className="rounded-md border border-surface-border bg-surface object-contain shadow-sm"
              style={{
                maxWidth: zoom === "fit" ? "100%" : "none",
                maxHeight: zoom === "fit" ? "100%" : "none",
                width: zoom === "fit" ? "auto" : `${zoom * 100}%`,
              }}
            />
          </div>
        </div>
      </div>
    );
  }
  return <UnsupportedRenderer artifact={artifact} content={content} />;
}

function MediaRenderer({ artifact, content }: ArtifactRendererProps) {
  if (content?.kind === "binary" && content.mimeType.startsWith("audio/")) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-base p-6">
        <audio controls src={content.openUrl} className="w-full max-w-[720px]">
          当前环境不支持音频预览。
        </audio>
      </div>
    );
  }
  if (content?.kind === "binary" && content.mimeType.startsWith("video/")) {
    return (
      <div className="flex h-full items-center justify-center overflow-auto bg-surface-base p-6">
        <video
          controls
          src={content.openUrl}
          className="max-h-full max-w-full rounded-md border border-surface-border bg-black shadow-sm"
        >
          当前环境不支持视频预览。
        </video>
      </div>
    );
  }
  return <UnsupportedRenderer artifact={artifact} content={content} />;
}

function HtmlRenderer({ artifact, content }: ArtifactRendererProps) {
  const [mode, setMode] = useState<PreviewSourceMode>("preview");

  if (!content || content.kind !== "text") {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  if (mode === "source") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">HTML</span>
          <PreviewSourceToggle mode={mode} onModeChange={setMode} />
        </div>
        <div className="min-h-0 flex-1">
          <CodeMirrorRenderer artifact={artifact} content={content} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
        <span className="text-xs text-ink-body">HTML</span>
        <PreviewSourceToggle mode={mode} onModeChange={setMode} />
      </div>
      <div className="min-h-0 flex-1 overflow-hidden bg-surface-base p-4">
        <iframe
          srcDoc={content.content}
          title={artifact.name}
          sandbox=""
          scrolling="auto"
          className="h-full w-full rounded-md border border-surface-border bg-white"
        />
      </div>
      {content.truncated ? (
        <div className="border-t border-surface-border bg-warning-light px-4 py-2 text-xs text-warning-text">
          文件较大，当前仅显示前 5 MiB。
        </div>
      ) : null}
    </div>
  );
}

function DocxRenderer({ artifact, content }: ArtifactRendererProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const openUrl = content?.kind === "binary" ? content.openUrl : null;

  useEffect(() => {
    if (!openUrl || !containerRef.current) return;
    const documentUrl = openUrl;
    const controller = new AbortController();
    const container = containerRef.current;
    setLoading(true);
    setError(null);
    container.innerHTML = "";

    async function renderDocx() {
      try {
        const response = await fetch(documentUrl, { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`读取失败：HTTP ${response.status}`);
        }
        const buffer = await response.arrayBuffer();
        const { renderAsync } = await import("docx-preview");
        if (controller.signal.aborted) return;
        await renderAsync(buffer, container, undefined, {
          breakPages: true,
          inWrapper: true,
          renderAltChunks: false,
          renderChanges: false,
          renderComments: false,
          renderEndnotes: true,
          renderFooters: true,
          renderFootnotes: true,
          renderHeaders: true,
          useBase64URL: true,
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "无法渲染 DOCX 文件。");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void renderDocx();
    return () => {
      controller.abort();
      container.innerHTML = "";
    };
  }, [openUrl]);

  if (!openUrl) {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  return (
    <div className="relative h-full min-h-0 overflow-auto bg-surface-base p-5">
      {loading ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base/80 text-sm text-ink-meta">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          正在渲染 DOCX
        </div>
      ) : null}
      {error ? (
        <div className="flex h-full items-center justify-center px-6 py-16">
          <div className="max-w-[420px] rounded-[10px] border border-error-light bg-error-light px-5 py-4 text-error-text">
            <div className="text-sm font-medium">无法预览 DOCX</div>
            <p className="mt-1 text-xs leading-5">{error}</p>
          </div>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="mx-auto min-h-full max-w-full [&_.docx-wrapper]:!bg-transparent [&_.docx-wrapper]:!p-0 [&_section.docx]:mx-auto [&_section.docx]:!mb-5 [&_section.docx]:rounded-sm [&_section.docx]:shadow-sm"
        />
      )}
    </div>
  );
}

type SpreadsheetSheet = {
  name: string;
  rows: string[][];
  totalRows: number;
  totalColumns: number;
  columnWidths: number[];
  columnOffsets: number[];
  rowHeights: number[];
  rowOffsets: number[];
  merges: SpreadsheetMerge[];
  cellStyles: Map<string, SpreadsheetCellStyle>;
};

const SPREADSHEET_ROW_HEIGHT = 32;
const SPREADSHEET_COLUMN_WIDTH = 140;
const SPREADSHEET_ROW_HEADER_WIDTH = 52;
const SPREADSHEET_COLUMN_HEADER_HEIGHT = 32;

type SpreadsheetMerge = {
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
};

type SpreadsheetCellStyle = {
  backgroundColor?: string;
  color?: string;
  fontWeight?: number;
  justifyContent?: "flex-start" | "center" | "flex-end";
  borderTop?: boolean;
  borderRight?: boolean;
  borderBottom?: boolean;
  borderLeft?: boolean;
};

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

function normalizeSpreadsheetRows(rows: unknown[][]): {
  rows: string[][];
  totalRows: number;
  totalColumns: number;
} {
  const totalRows = rows.length;
  const totalColumns = rows.reduce(
    (max, row) => Math.max(max, row.length),
    0,
  );
  return {
    rows: rows.map((row) => row.map((value) => (value == null ? "" : String(value)))),
    totalRows,
    totalColumns,
  };
}

function spreadsheetMerges(worksheet: XLSX.WorkSheet): SpreadsheetMerge[] {
  return (worksheet["!merges"] ?? []).map((range) => ({
    startRow: range.s.r,
    startColumn: range.s.c,
    endRow: range.e.r,
    endColumn: range.e.c,
  }));
}

function spreadsheetColumnWidths(
  worksheet: XLSX.WorkSheet,
  totalColumns: number,
): number[] {
  const columns = worksheet["!cols"] ?? [];
  return Array.from({ length: totalColumns }, (_, index) => {
    const width = columns[index];
    const rawWidth = width?.wpx ?? (width?.wch ? width.wch * 8 : undefined);
    if (!rawWidth || Number.isNaN(rawWidth)) return SPREADSHEET_COLUMN_WIDTH;
    return Math.min(Math.max(Math.round(rawWidth), 72), 320);
  });
}

function spreadsheetRowHeights(
  worksheet: XLSX.WorkSheet,
  totalRows: number,
): number[] {
  const rows = worksheet["!rows"] ?? [];
  return Array.from({ length: totalRows }, (_, index) => {
    const height = rows[index]?.hpx;
    if (!height || Number.isNaN(height)) return SPREADSHEET_ROW_HEIGHT;
    return Math.min(Math.max(Math.round(height), 24), 96);
  });
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
  const backgroundColor =
    colorFromSheetValue(fill.fgColor) ?? colorFromSheetValue(fill.bgColor);
  const color = colorFromSheetValue(font.color);
  const fontWeight = font.bold ? 700 : undefined;
  const justifyContent =
    horizontal === "center"
      ? "center"
      : horizontal === "right"
        ? "flex-end"
        : undefined;

  return {
    backgroundColor,
    color,
    fontWeight,
    justifyContent,
    borderTop: Boolean(border.top),
    borderRight: Boolean(border.right),
    borderBottom: Boolean(border.bottom),
    borderLeft: Boolean(border.left),
  };
}

function spreadsheetCellStyles(
  worksheet: XLSX.WorkSheet,
  totalRows: number,
  totalColumns: number,
): Map<string, SpreadsheetCellStyle> {
  const styles = new Map<string, SpreadsheetCellStyle>();
  for (let row = 0; row < totalRows; row += 1) {
    for (let column = 0; column < totalColumns; column += 1) {
      const address = XLSX.utils.encode_cell({ r: row, c: column });
      const cell = worksheet[address];
      if (!cell) continue;
      const style = cellStyleFromSheetCell(cell);
      if (Object.values(style).some(Boolean)) {
        styles.set(`${row}:${column}`, style);
      }
    }
  }
  return styles;
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

function spreadsheetCellBoxShadow(
  selected: boolean,
  cellStyle?: SpreadsheetCellStyle,
): string | undefined {
  const shadows: string[] = [];
  if (cellStyle?.borderTop) shadows.push("inset 0 1px 0 #1f2937");
  if (cellStyle?.borderRight) shadows.push("inset -1px 0 0 #1f2937");
  if (cellStyle?.borderBottom) shadows.push("inset 0 -1px 0 #1f2937");
  if (cellStyle?.borderLeft) shadows.push("inset 1px 0 0 #1f2937");
  if (selected) shadows.push("inset 0 0 0 1px rgba(114, 92, 249, 0.55)");
  return shadows.length ? shadows.join(", ") : undefined;
}

type SpreadsheetSelection = {
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
};

type SpreadsheetCellRef = {
  row: number;
  column: number;
};

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

function spreadsheetSelectionToText(
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

function SpreadsheetRenderer({ artifact, content }: ArtifactRendererProps) {
  const [sheets, setSheets] = useState<SpreadsheetSheet[]>([]);
  const [activeSheetName, setActiveSheetName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [selection, setSelection] = useState<SpreadsheetSelection | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [scrollOffset, setScrollOffset] = useState({ left: 0, top: 0 });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const openUrl = content?.kind === "binary" ? content.openUrl : null;

  useEffect(() => {
    if (!openUrl) return;
    const workbookUrl = openUrl;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setSheets([]);
    setActiveSheetName(null);

    async function loadWorkbook() {
      try {
        const response = await fetch(workbookUrl, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`读取失败：HTTP ${response.status}`);
        }
        const buffer = await response.arrayBuffer();
        const workbook = XLSX.read(buffer, {
          type: "array",
          cellDates: true,
          cellNF: true,
          cellStyles: true,
        });
        const parsedSheets = workbook.SheetNames.map((name) => {
          const worksheet = workbook.Sheets[name];
          const rawRows = XLSX.utils.sheet_to_json<unknown[]>(worksheet, {
            header: 1,
            defval: "",
            raw: false,
            blankrows: true,
          });
          const normalized = normalizeSpreadsheetRows(rawRows);
          const merges = spreadsheetMerges(worksheet);
          const totalRows = Math.max(
            normalized.totalRows,
            ...merges.map((merge) => merge.endRow + 1),
            worksheet["!rows"]?.length ?? 0,
          );
          const totalColumns = Math.max(
            normalized.totalColumns,
            ...merges.map((merge) => merge.endColumn + 1),
            worksheet["!cols"]?.length ?? 0,
          );
          const columnWidths = spreadsheetColumnWidths(worksheet, totalColumns);
          const rowHeights = spreadsheetRowHeights(worksheet, totalRows);
          return {
            name,
            rows: normalized.rows,
            totalRows,
            totalColumns,
            columnWidths,
            columnOffsets: cumulativeOffsets(columnWidths),
            rowHeights,
            rowOffsets: cumulativeOffsets(rowHeights),
            merges,
            cellStyles: spreadsheetCellStyles(
              worksheet,
              totalRows,
              totalColumns,
            ),
          };
        });
        if (!parsedSheets.length) {
          throw new Error("工作簿中没有可显示的 sheet。");
        }
        setSheets(parsedSheets);
        setActiveSheetName(parsedSheets[0]?.name ?? null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "无法解析表格文件。");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void loadWorkbook();
    return () => controller.abort();
  }, [openUrl]);

  const activeSheet =
    sheets.find((sheet) => sheet.name === activeSheetName) ?? sheets[0] ?? null;
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

  const searchMatches = useMemo<SpreadsheetCellRef[]>(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!activeSheet || !query) return [];
    const matches: SpreadsheetCellRef[] = [];
    activeSheet.rows.forEach((row, rowIndex) => {
      row.forEach((cell, columnIndex) => {
        if (cell.toLowerCase().includes(query)) {
          matches.push({ row: rowIndex, column: columnIndex });
        }
      });
    });
    return matches;
  }, [activeSheet, searchQuery]);

  useEffect(() => {
    setSearchIndex(0);
  }, [activeSheetName, searchQuery]);

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
    const text = spreadsheetSelectionToText(activeSheet, selection);
    await navigator.clipboard.writeText(text);
    setCopyStatus("已复制");
    window.setTimeout(() => setCopyStatus(null), 1400);
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
      if (!extendSelection || !current) {
        return target;
      }
      return {
        ...current,
        endRow: target.endRow,
        endColumn: target.endColumn,
      };
    });
  };

  if (!openUrl) {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-meta">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        正在解析表格
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div className="max-w-[420px] rounded-[10px] border border-error-light bg-error-light px-5 py-4 text-error-text">
          <div className="text-sm font-medium">无法预览表格</div>
          <p className="mt-1 text-xs leading-5">{error}</p>
        </div>
      </div>
    );
  }

  if (!activeSheet) {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
          {sheets.map((sheet) => (
            <button
              key={sheet.name}
              type="button"
              onClick={() => {
                setActiveSheetName(sheet.name);
                setSelection(null);
              }}
              className={`h-7 max-w-[180px] shrink-0 truncate rounded-md px-3 text-xs transition ${
                sheet.name === activeSheet.name
                  ? "bg-surface text-ink-heading shadow-sm"
                  : "text-ink-body hover:bg-surface-muted hover:text-ink-heading"
              }`}
              title={sheet.name}
            >
              {sheet.name}
            </button>
          ))}
        </div>
        <div className="ml-3 flex shrink-0 items-center gap-2">
          <label className="flex h-7 w-[220px] items-center rounded-md border border-surface-border bg-surface px-2 text-xs text-ink-body">
            <Search className="mr-1.5 h-3.5 w-3.5 text-ink-meta" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="搜索单元格"
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
                上一个
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
                下一个
              </button>
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => void copySelection()}
            disabled={!selection}
            className="h-7 rounded-md px-2 text-xs text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
          >
            {copyStatus ?? "复制"}
          </button>
          <div className="shrink-0 text-xs text-ink-meta">
            {activeSheet.totalRows} rows · {activeSheet.totalColumns} cols
          </div>
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={(event) => {
          setScrollOffset({
            left: event.currentTarget.scrollLeft,
            top: event.currentTarget.scrollTop,
          });
        }}
        className="min-h-0 flex-1 overflow-auto bg-surface"
      >
        <div
          className="relative text-xs"
          style={{
            width:
              SPREADSHEET_ROW_HEADER_WIDTH +
              columnVirtualizer.getTotalSize(),
            height:
              SPREADSHEET_COLUMN_HEADER_HEIGHT + rowVirtualizer.getTotalSize(),
          }}
        >
          <div
            className="absolute left-0 top-0 z-30 border-b border-r border-surface-border bg-surface-soft"
            style={{
              left: scrollOffset.left,
              top: scrollOffset.top,
              width: SPREADSHEET_ROW_HEADER_WIDTH,
              height: SPREADSHEET_COLUMN_HEADER_HEIGHT,
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
                left: SPREADSHEET_ROW_HEADER_WIDTH + virtualColumn.start,
                width: virtualColumn.size,
                height: SPREADSHEET_COLUMN_HEADER_HEIGHT,
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
                top: SPREADSHEET_COLUMN_HEADER_HEIGHT + virtualRow.start,
                left: scrollOffset.left,
                width: SPREADSHEET_ROW_HEADER_WIDTH,
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
                    top: SPREADSHEET_COLUMN_HEADER_HEIGHT + virtualRow.start,
                    left: SPREADSHEET_ROW_HEADER_WIDTH + virtualColumn.start,
                    width: cellWidth,
                    height: cellHeight,
                    backgroundColor:
                      selected
                        ? "rgba(114, 92, 249, 0.12)"
                        : matched
                          ? "rgba(251, 191, 36, 0.24)"
                          : cellStyle?.backgroundColor
                            ? cellStyle.backgroundColor
                          : virtualRow.index % 2 === 0
                            ? "#ffffff"
                            : "#f8fafc",
                    boxShadow: spreadsheetCellBoxShadow(selected, cellStyle),
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

function PdfRenderer({ artifact, content }: ArtifactRendererProps) {
  if (content?.kind === "binary" && content.mimeType === "application/pdf") {
    return (
      <div className="h-full min-h-0 bg-surface-base p-4">
        <iframe
          src={content.openUrl}
          title={artifact.name}
          className="h-full w-full rounded-md border border-surface-border bg-surface"
        />
      </div>
    );
  }
  return <UnsupportedRenderer artifact={artifact} content={content} />;
}

const ARTIFACT_RENDERERS: Partial<
  Record<ArtifactPreviewKind, ArtifactRendererComponent>
> = {
  markdown: TextRenderer,
  code: CodeMirrorRenderer,
  plain: CodeMirrorRenderer,
  html: HtmlRenderer,
  docx: DocxRenderer,
  image: ImageRenderer,
  media: MediaRenderer,
  pdf: PdfRenderer,
  spreadsheet: SpreadsheetRenderer,
};

function ArtifactRenderer({
  artifact,
  content,
  onOpenExternal,
}: ArtifactRendererProps) {
  const Renderer = ARTIFACT_RENDERERS[artifact.previewKind] ?? UnsupportedRenderer;
  return (
    <Renderer
      artifact={artifact}
      content={content}
      onOpenExternal={onOpenExternal}
    />
  );
}

export function ArtifactViewerShell({
  artifact,
  content,
  loading = false,
  error = null,
  onReload,
  onClose,
  onCopyContent,
  onOpenExternal,
}: ArtifactViewerShellProps) {
  const metadata = useMemo(() => {
    if (!artifact) return [];
    return [
      PREVIEW_LABELS[artifact.previewKind],
      formatBytes(artifact.size),
      formatModified(artifact.modifiedAt),
    ].filter(Boolean);
  }, [artifact]);
  const canShowCurrentPreview = Boolean(artifact);

  if (!artifact && !loading && !error) {
    return <EmptyArtifactState />;
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && onClose) {
      event.preventDefault();
      onClose();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
      if (!onReload) return;
      event.preventDefault();
      onReload();
      return;
    }
    if (
      (event.metaKey || event.ctrlKey) &&
      event.shiftKey &&
      event.key.toLowerCase() === "o"
    ) {
      if (!onOpenExternal) return;
      event.preventDefault();
      onOpenExternal();
    }
  };

  return (
    <article
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-surface shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <header className="shrink-0 border-b border-surface-border bg-surface">
        <div className="flex items-start gap-4 px-5 py-4">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
            {artifact ? (
              <ArtifactIcon kind={artifact.previewKind} />
            ) : (
              <Loader2 className="h-4 w-4 animate-spin text-ink-meta" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-medium text-ink-heading">
              {artifact?.name ?? "读取文件中"}
            </h2>
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-body">
              {artifact?.path ? (
                <span className="min-w-0 max-w-full truncate">{artifact.path}</span>
              ) : null}
              {metadata.map((item) => (
                <span key={item} className="rounded-full bg-surface-soft px-2 py-0.5">
                  {item}
                </span>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {onCopyContent ? (
              <button
                type="button"
                onClick={onCopyContent}
                disabled={!artifact?.capabilities.canCopyContent}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
                title="复制内容"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onOpenExternal ? (
              <button
                type="button"
                onClick={onOpenExternal}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title="外部打开"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onReload ? (
              <button
                type="button"
                onClick={onReload}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title="刷新"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title="关闭"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        </div>
      </header>
      <div className="relative min-h-0 flex-1">
        {loading && !canShowCurrentPreview ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-meta">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            正在读取文件
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-6 py-16">
            <div className="max-w-[420px] rounded-[10px] border border-error-light bg-error-light px-5 py-4 text-error-text">
              <div className="text-sm font-medium">无法预览文件</div>
              <p className="mt-1 text-xs leading-5">{error}</p>
            </div>
          </div>
        ) : artifact ? (
          <ArtifactRenderer
            artifact={artifact}
            content={content}
            onOpenExternal={onOpenExternal}
          />
        ) : null}
        {loading && canShowCurrentPreview ? (
          <div className="pointer-events-none absolute inset-x-0 top-0 z-20">
            <div className="h-px w-full bg-brand/70 animate-pulse">
              <div className="h-full w-full bg-brand/70" />
            </div>
            <div className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-md border border-surface-border bg-surface/95 px-2.5 py-1 text-2xs text-ink-body shadow-sm backdrop-blur">
              <Loader2 className="h-3 w-3 animate-spin" />
              正在读取
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}
