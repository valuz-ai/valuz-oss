import {
  Code2,
  Copy,
  Eye,
  ExternalLink,
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  RefreshCw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { VirtualizedMarkdown } from "../knowledge/VirtualizedMarkdown";
import { Badge } from "../ui/badge";
import { PdfRenderer } from "./PdfRenderer";
import type {
  ArtifactContent,
  ArtifactDescriptor,
  ArtifactPreviewKind,
  ArtifactRendererProps,
  ArtifactViewerShellProps,
} from "./artifact-viewer.types";

import { useI18n } from "../../hooks/use-i18n";

export type {
  ArtifactContent,
  ArtifactDescriptor,
  ArtifactOpenTarget,
  ArtifactPreviewKind,
  ArtifactViewerShellProps,
} from "./artifact-viewer.types";

const CodeMirrorRenderer = lazy(() =>
  import("./CodeMirrorRenderer").then((module) => ({
    default: module.CodeMirrorRenderer,
  })),
);
const DocxRenderer = lazy(() =>
  import("./DocxRenderer").then((module) => ({
    default: module.DocxRenderer,
  })),
);
const SpreadsheetRenderer = lazy(() =>
  import("./SpreadsheetRenderer").then((module) => ({
    default: module.SpreadsheetRenderer,
  })),
);

type ArtifactRendererComponent = ComponentType<ArtifactRendererProps>;
type PreviewSourceMode = "preview" | "source";
type ImageZoom = number | "fit";

interface ArtifactViewModeContextValue {
  mode: PreviewSourceMode;
  onModeChange: (mode: PreviewSourceMode) => void;
}

const ArtifactViewModeContext =
  createContext<ArtifactViewModeContextValue | null>(null);

interface ArtifactImageZoomContextValue {
  zoom: ImageZoom;
  onZoomChange: (zoom: ImageZoom) => void;
}

const ArtifactImageZoomContext =
  createContext<ArtifactImageZoomContextValue | null>(null);

function clampImageZoom(value: number): number {
  return Math.min(Math.max(value, 0.25), 4);
}

function numericImageZoom(zoom: ImageZoom): number {
  return zoom === "fit" ? 1 : zoom;
}

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

export function ArtifactIcon({ kind }: { kind: ArtifactPreviewKind }) {
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
  // The DESIGN segmented pattern (muted track, surface active pill) — the
  // hand-rolled bordered box read as a stray input next to real controls.
  return (
    <div className="inline-flex h-7 items-center gap-0.5 rounded-lg bg-surface-muted p-0.5 text-xs">
      {(["preview", "source"] as const).map((item) => (
        <button
          key={item}
          type="button"
          onClick={() => onModeChange(item)}
          className={`flex h-full items-center rounded-md px-2.5 font-medium leading-none transition-colors ${
            mode === item
              ? "bg-surface text-ink-heading shadow-sm"
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
  const { t } = useI18n();
  return (
    <div className="flex h-full items-center justify-center px-6 py-16">
      <div className="max-w-[360px] text-center">
        <FileText className="mx-auto mb-3 h-8 w-8 text-ink-muted" />
        <div className="text-sm font-medium text-ink-heading">
          {t("ui.artifact.emptyTitle")}
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-body">
          {t("ui.artifact.emptyHint")}
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
  const { t } = useI18n();
  const reason =
    content?.kind === "external"
      ? content.reason
      : content?.kind === "binary" && content.reason
        ? content.reason
        : t("ui.artifact.unsupportedReason");
  return (
    <div className="flex h-full items-center justify-center px-6 py-16">
      <div className="max-w-[460px] rounded-xl border border-surface-border bg-surface-soft px-5 py-5">
        <div className="flex items-center gap-2 text-sm font-medium text-ink-heading">
          <ArtifactIcon kind={artifact.previewKind} />
          {t("ui.artifact.unsupportedTitle")}
        </div>
        <p className="mt-2 text-xs leading-5 text-ink-body">{reason}</p>
        <div className="mt-4 grid grid-cols-[96px_1fr] gap-x-3 gap-y-1 text-2xs">
          <span className="text-ink-meta">{t("ui.artifact.fieldName")}</span>
          <span className="min-w-0 truncate text-ink-heading">{artifact.name}</span>
          <span className="text-ink-meta">{t("ui.artifact.fieldPath")}</span>
          <span className="min-w-0 truncate text-ink-heading">{artifact.path}</span>
          <span className="text-ink-meta">{t("ui.artifact.fieldType")}</span>
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
              {t("ui.artifact.openLocally")}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TextRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const shellViewMode = useContext(ArtifactViewModeContext);
  const [localMode, setLocalMode] = useState<PreviewSourceMode>("preview");
  const markdownMode = shellViewMode?.mode ?? localMode;
  const setMarkdownMode = shellViewMode?.onModeChange ?? setLocalMode;
  if (!content || content.kind !== "text") {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  if (artifact.previewKind === "markdown" && markdownMode === "preview") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {!shellViewMode ? (
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border px-3">
            <span className="text-2xs font-medium text-ink-meta">Markdown</span>
            <PreviewSourceToggle
              mode={markdownMode}
              onModeChange={setMarkdownMode}
            />
          </div>
        ) : null}
        {content.truncated ? (
          <div className="shrink-0 px-8 pt-7">
            <div className="mx-auto max-w-[820px] rounded-md border border-warning-light bg-warning-light px-3 py-2 text-xs text-warning-text">
              {t("ui.artifact.truncated")}
            </div>
          </div>
        ) : null}
        {/* Windowed past a few hundred table rows, rendered whole below that.
            A spreadsheet flattened to GFM builds one DOM node per cell, and
            40,000 cells took 19 s to open here; the reading column, the
            source toggle and the padding are the viewer's, not the
            renderer's. */}
        <VirtualizedMarkdown
          content={content.content}
          viewportClassName="min-h-0 flex-1 px-8 py-7"
          sizerClassName="mx-auto max-w-[820px]"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {artifact.previewKind === "markdown" && !shellViewMode ? (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">Markdown</span>
          <PreviewSourceToggle
            mode={markdownMode}
            onModeChange={setMarkdownMode}
          />
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        <CodeMirrorRenderer
          artifact={artifact}
          content={content}
          wrapLines={artifact.previewKind === "markdown"}
        />
      </div>
    </div>
  );
}

function ImageRenderer({ artifact, content, onReload }: ArtifactRendererProps) {
  const { t } = useI18n();
  const shellImageZoom = useContext(ArtifactImageZoomContext);
  const [localZoom, setLocalZoom] = useState<ImageZoom>("fit");
  const zoom = shellImageZoom?.zoom ?? localZoom;
  const setZoom = shellImageZoom?.onZoomChange ?? setLocalZoom;
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [dragStart, setDragStart] = useState<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const imageUrl =
    content?.kind === "binary" && content.mimeType.startsWith("image/")
      ? content.openUrl
      : null;

  useEffect(() => {
    setLoadState("loading");
  }, [imageUrl]);

  const zoomValue = numericImageZoom(zoom);
  const updateZoom = (nextZoom: number) => {
    setZoom(clampImageZoom(nextZoom));
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

  if (imageUrl) {
    return (
      <div className="flex h-full min-h-0 flex-col bg-surface-base">
        {!shellImageZoom ? (
          <div className="flex h-10 shrink-0 items-center justify-end border-b border-surface-border bg-surface px-4">
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => updateZoom(zoomValue - 0.25)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
                disabled={zoom !== "fit" && zoom <= 0.25}
                aria-label={t("ui.artifact.zoomOutLabel")}
                title={t("ui.artifact.zoomOut")}
              >
                <ZoomOut className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setZoom("fit")}
                aria-label={t("ui.artifact.fitWindowLabel")}
                className="h-7 min-w-12 rounded-md px-2 text-xs text-ink-body transition hover:bg-surface-muted hover:text-ink-heading"
                title={t("ui.artifact.fitWindow")}
              >
                {zoom === "fit"
                  ? t("ui.artifact.fitWindow")
                  : `${Math.round(zoom * 100)}%`}
              </button>
              <button
                type="button"
                onClick={() => updateZoom(zoomValue + 0.25)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
                disabled={zoom !== "fit" && zoom >= 4}
                aria-label={t("ui.artifact.zoomInLabel")}
                title={t("ui.artifact.zoomIn")}
              >
                <ZoomIn className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ) : null}
        <div
          ref={viewportRef}
          onMouseDown={handleDragStart}
          onMouseMove={handleDragMove}
          onMouseUp={() => setDragStart(null)}
          onMouseLeave={() => setDragStart(null)}
          className={`relative min-h-0 flex-1 overflow-auto bg-surface-base p-6 ${
            zoom !== "fit" && zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""
          }`}
        >
          {loadState === "loading" ? (
            <div
              className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base/80 text-sm text-ink-meta"
              role="status"
            >
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("ui.artifact.loadingImage")}
            </div>
          ) : null}
          {loadState === "error" ? (
            <div
              className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-surface-base px-6 text-sm text-error-text"
              role="alert"
            >
              {t("ui.artifact.imageFailed")}
              {/* Re-resolve, don't re-request: the address may be an expired
                  presigned URL, which would just fail again. */}
              {onReload ? (
                <button
                  type="button"
                  onClick={() => {
                    setLoadState("loading");
                    onReload();
                  }}
                  className="inline-flex h-7 items-center gap-1.5 rounded-md border border-error-text/20 bg-surface px-2.5 text-xs font-medium text-error-text transition hover:bg-surface-soft"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {t("ui.artifact.retry")}
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="flex min-h-full min-w-full items-center justify-center">
            <img
              src={imageUrl}
              alt={artifact.name}
              draggable={false}
              onLoad={() => setLoadState("ready")}
              onError={() => setLoadState("error")}
              className={`rounded-md border border-surface-border bg-surface object-contain shadow-sm ${
                loadState === "ready" ? "opacity-100" : "opacity-0"
              }`}
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

function MediaRenderer({ artifact, content, onReload }: ArtifactRendererProps) {
  const { t } = useI18n();
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const media =
    content?.kind === "binary" &&
    (content.mimeType.startsWith("audio/") || content.mimeType.startsWith("video/"))
      ? content
      : null;

  useEffect(() => {
    setLoadState("loading");
  }, [media?.openUrl]);

  const statusOverlay =
    loadState === "loading" ? (
      <div
        className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base/80 text-sm text-ink-meta"
        role="status"
      >
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        {t("ui.artifact.loadingMedia")}
      </div>
    ) : loadState === "error" ? (
      <div
        className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-surface-base px-6 text-sm text-error-text"
        role="alert"
      >
        {t("ui.artifact.mediaFailed")}
        {/* Re-resolve, don't re-request: the address may be an expired
            presigned URL, which would just fail again. */}
        {onReload ? (
          <button
            type="button"
            onClick={() => {
              setLoadState("loading");
              onReload();
            }}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-error-text/20 bg-surface px-2.5 text-xs font-medium text-error-text transition hover:bg-surface-soft"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t("ui.artifact.retry")}
          </button>
        ) : null}
      </div>
    ) : null;

  if (media?.mimeType.startsWith("audio/")) {
    return (
      <div className="relative flex h-full items-center justify-center bg-surface-base p-6">
        {statusOverlay}
        <audio
          controls
          preload="metadata"
          src={media.openUrl}
          onLoadedData={() => setLoadState("ready")}
          onError={() => setLoadState("error")}
          className={`w-full max-w-[720px] ${loadState === "ready" ? "opacity-100" : "opacity-0"}`}
        >
          {t("ui.artifact.audioUnsupported")}
        </audio>
      </div>
    );
  }
  if (media?.mimeType.startsWith("video/")) {
    return (
      <div className="relative flex h-full items-center justify-center overflow-auto bg-surface-base p-6">
        {statusOverlay}
        <video
          controls
          preload="metadata"
          src={media.openUrl}
          onLoadedData={() => setLoadState("ready")}
          onError={() => setLoadState("error")}
          className={`max-h-full max-w-full rounded-md border border-surface-border bg-black shadow-sm ${
            loadState === "ready" ? "opacity-100" : "opacity-0"
          }`}
        >
          {t("ui.artifact.videoUnsupported")}
        </video>
      </div>
    );
  }
  return <UnsupportedRenderer artifact={artifact} content={content} />;
}

const HTML_PREVIEW_STYLE = `
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html,
    body {
      box-sizing: border-box !important;
      min-width: 0 !important;
      max-width: 100% !important;
      min-height: 100% !important;
      height: auto !important;
      overflow: auto !important;
    }

    *,
    *::before,
    *::after {
      box-sizing: inherit;
    }

    body {
      width: auto !important;
      margin: 0;
    }

    body > * {
      max-width: 100% !important;
    }

    img,
    video,
    canvas,
    svg,
    table {
      max-width: 100% !important;
    }
  </style>
`;

function htmlPreviewSrcDoc(source: string): string {
  if (/<head[\s>]/i.test(source)) {
    return source.replace(/<head([^>]*)>/i, `<head$1>${HTML_PREVIEW_STYLE}`);
  }
  if (/<html[\s>]/i.test(source)) {
    return source.replace(
      /<html([^>]*)>/i,
      `<html$1><head>${HTML_PREVIEW_STYLE}</head>`,
    );
  }
  return `<!doctype html><html><head>${HTML_PREVIEW_STYLE}</head><body>${source}</body></html>`;
}

function HtmlRenderer({ artifact, content }: ArtifactRendererProps) {
  const { t } = useI18n();
  const shellViewMode = useContext(ArtifactViewModeContext);
  const [localMode, setLocalMode] = useState<PreviewSourceMode>("preview");
  const mode = shellViewMode?.mode ?? localMode;
  const setMode = shellViewMode?.onModeChange ?? setLocalMode;
  const htmlSource = content?.kind === "text" ? content.content : null;
  const previewHostRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [iframeHeight, setIframeHeight] = useState<number | null>(null);

  const resizePreview = useCallback(() => {
    const host = previewHostRef.current;
    const iframe = iframeRef.current;
    if (!host || !iframe) return;
    try {
      const doc = iframe.contentDocument;
      const htmlElement = doc?.documentElement;
      const body = doc?.body;
      if (!doc || !htmlElement || !body) return;

      body.style.setProperty("zoom", "1");
      htmlElement.style.setProperty("height", "auto", "important");
      body.style.setProperty("height", "auto", "important");
      htmlElement.style.setProperty("overflow", "hidden", "important");
      body.style.setProperty("overflow", "hidden", "important");

      const availableWidth = Math.max(host.clientWidth, 1);
      const contentWidth = Math.max(
        htmlElement.scrollWidth,
        body.scrollWidth,
        body.offsetWidth,
        availableWidth,
      );
      const scale = Math.min(1, availableWidth / contentWidth);
      body.style.setProperty("zoom", String(scale));

      window.requestAnimationFrame(() => {
        const contentHeight = Math.max(
          htmlElement.scrollHeight,
          body.scrollHeight,
          host.clientHeight,
        );
        setIframeHeight(Math.ceil(contentHeight * scale));
      });
    } catch {
      setIframeHeight(null);
    }
  }, []);

  useEffect(() => {
    if (mode !== "preview") return;
    const host = previewHostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(() => resizePreview());
    observer.observe(host);
    resizePreview();
    return () => observer.disconnect();
  }, [htmlSource, mode, resizePreview]);

  if (!content || content.kind !== "text") {
    return <UnsupportedRenderer artifact={artifact} content={content} />;
  }

  if (mode === "source") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {!shellViewMode ? (
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
            <span className="text-xs text-ink-body">HTML</span>
            <PreviewSourceToggle mode={mode} onModeChange={setMode} />
          </div>
        ) : null}
        <div className="min-h-0 flex-1">
          <CodeMirrorRenderer
            artifact={artifact}
            content={content}
            wrapLines
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      {!shellViewMode ? (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border bg-surface-soft px-4">
          <span className="text-xs text-ink-body">HTML</span>
          <PreviewSourceToggle mode={mode} onModeChange={setMode} />
        </div>
      ) : null}
      <div
        ref={previewHostRef}
        className="min-h-0 flex-1 overflow-auto bg-surface-base p-4"
      >
        <iframe
          ref={iframeRef}
          srcDoc={htmlPreviewSrcDoc(content.content)}
          title={artifact.name}
          sandbox="allow-same-origin"
          scrolling="no"
          onLoad={resizePreview}
          className="w-full bg-white"
          style={{
            height: iframeHeight ? `${iframeHeight}px` : "100%",
            minHeight: "100%",
          }}
        />
      </div>
      {content.truncated ? (
        <div className="border-t border-surface-border bg-warning-light px-4 py-2 text-xs text-warning-text">
          {t("ui.artifact.truncated")}
        </div>
      ) : null}
    </div>
  );
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

/** Body-only dispatch, without the shell's own header/toolbar. Exported so
 *  other viewers (the document reader) can reuse the registered renderers
 *  instead of re-implementing PDF / media / HTML embedding. */
export function ArtifactRenderer({
  artifact,
  content,
  target,
  onOpenExternal,
  onReload,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const Renderer = ARTIFACT_RENDERERS[artifact.previewKind] ?? UnsupportedRenderer;
  return (
    <Suspense
      fallback={
        <div
          className="flex h-full items-center justify-center text-sm text-ink-meta"
          role="status"
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          {t("ui.artifact.loadingRenderer")}
        </div>
      }
    >
      <Renderer
        artifact={artifact}
        content={content}
        target={target}
        onOpenExternal={onOpenExternal}
        onReload={onReload}
      />
    </Suspense>
  );
}

export function ArtifactViewerShell({
  artifact,
  content,
  target = null,
  loading = false,
  error = null,
  framed = true,
  compactHeader = false,
  onReload,
  onClose,
  onCopyContent,
  onOpenExternal,
}: ArtifactViewerShellProps) {
  const { t } = useI18n();
  const shellRef = useRef<HTMLElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [viewModeState, setViewModeState] = useState<{
    artifactId: string | null;
    mode: PreviewSourceMode;
  }>({ artifactId: artifact?.id ?? null, mode: "preview" });
  const viewMode =
    viewModeState.artifactId === (artifact?.id ?? null)
      ? viewModeState.mode
      : "preview";
  const setViewMode = useCallback(
    (mode: PreviewSourceMode) => {
      setViewModeState({ artifactId: artifact?.id ?? null, mode });
    },
    [artifact?.id],
  );
  const [imageZoomState, setImageZoomState] = useState<{
    artifactId: string | null;
    zoom: ImageZoom;
  }>({ artifactId: artifact?.id ?? null, zoom: "fit" });
  const imageZoom =
    imageZoomState.artifactId === (artifact?.id ?? null)
      ? imageZoomState.zoom
      : "fit";
  const setImageZoom = useCallback(
    (zoom: ImageZoom) => {
      setImageZoomState({ artifactId: artifact?.id ?? null, zoom });
    },
    [artifact?.id],
  );
  const fullscreenSupported =
    typeof Element !== "undefined" &&
    typeof Element.prototype.requestFullscreen === "function";
  // Compact mode answers "where is this, how big" only — the tab strip above
  // already carries the name, and the kind is obvious from the tab's icon.
  const compactSize = formatBytes(artifact?.size);
  const metadata = useMemo(() => {
    if (!artifact) return [];
    return [
      PREVIEW_LABELS[artifact.previewKind],
      formatBytes(artifact.size),
      formatModified(artifact.modifiedAt),
    ].filter(Boolean);
  }, [artifact]);

  useEffect(() => {
    if (artifact?.id) shellRef.current?.focus({ preventScroll: true });
  }, [artifact?.id]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === shellRef.current);
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () =>
      document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  if (!artifact && !loading && !error) {
    return <EmptyArtifactState />;
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
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
      if (!onOpenExternal || !artifact?.capabilities.canOpenExternal) return;
      event.preventDefault();
      onOpenExternal();
      return;
    }
    if (
      artifact?.previewKind === "pdf" &&
      fullscreenSupported &&
      (event.metaKey || event.ctrlKey) &&
      event.shiftKey &&
      event.key.toLowerCase() === "f"
    ) {
      event.preventDefault();
      if (document.fullscreenElement === shellRef.current) {
        void document.exitFullscreen();
      } else {
        void shellRef.current?.requestFullscreen();
      }
    }
  };

  return (
    <article
      ref={shellRef}
      className={`flex h-full min-h-0 flex-col overflow-hidden bg-surface outline-none ${
        framed
          ? "rounded-[14px] border border-surface-border shadow-sm focus-visible:ring-2 focus-visible:ring-primary/30"
          : ""
      }`}
      tabIndex={0}
      aria-busy={loading}
      onKeyDown={handleKeyDown}
    >
      <header className="shrink-0 border-b border-surface-border bg-surface">
        <div
          className={
            compactHeader
              ? "flex items-center gap-3 px-3 py-1"
              : "flex items-start gap-4 px-5 py-4"
          }
        >
          {compactHeader ? (
            // One line: the tab strip above carries the name and the kind is
            // already in its icon, so this row only answers "where, how big".
            <div className="flex min-w-0 flex-1 items-center gap-2 text-xs text-ink-body">
              <span className="min-w-0 truncate">
                {artifact?.path ?? t("ui.artifact.readingFileName")}
              </span>
              {compactSize ? (
                <span className="shrink-0 text-ink-meta">{compactSize}</span>
              ) : null}
            </div>
          ) : (
            <>
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
                {artifact ? (
                  <ArtifactIcon kind={artifact.previewKind} />
                ) : (
                  <Loader2 className="h-4 w-4 animate-spin text-ink-meta" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-lg font-medium text-ink-heading">
                  {artifact?.name ?? t("ui.artifact.readingFileName")}
                </h2>
                <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-body">
                  {artifact?.path && artifact.path !== artifact.name ? (
                    <span className="min-w-0 max-w-full truncate">{artifact.path}</span>
                  ) : null}
                  {metadata.map((item) => (
                    <Badge key={item} variant="outline">
                      {item}
                    </Badge>
                  ))}
                </div>
              </div>
            </>
          )}
          <div className="flex shrink-0 items-center gap-1">
            {artifact?.previewKind === "image" ? (
              <>
                <button
                  type="button"
                  onClick={() =>
                    setImageZoom(
                      clampImageZoom(numericImageZoom(imageZoom) - 0.25),
                    )
                  }
                  disabled={imageZoom !== "fit" && imageZoom <= 0.25}
                  aria-label={t("ui.artifact.zoomOutLabel")}
                  className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:opacity-40"
                  title={t("ui.artifact.zoomOut")}
                >
                  <ZoomOut className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setImageZoom("fit")}
                  aria-label={t("ui.artifact.fitWindowLabel")}
                  className="h-9 min-w-12 rounded-md px-2 text-xs text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                  title={t("ui.artifact.fitWindow")}
                >
                  {imageZoom === "fit"
                    ? t("ui.artifact.fitWindow")
                    : `${Math.round(imageZoom * 100)}%`}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setImageZoom(
                      clampImageZoom(numericImageZoom(imageZoom) + 0.25),
                    )
                  }
                  disabled={imageZoom !== "fit" && imageZoom >= 4}
                  aria-label={t("ui.artifact.zoomInLabel")}
                  className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:opacity-40"
                  title={t("ui.artifact.zoomIn")}
                >
                  <ZoomIn className="h-3.5 w-3.5" />
                </button>
              </>
            ) : null}
            {artifact &&
            (artifact.previewKind === "markdown" ||
              artifact.previewKind === "html") ? (
              <>
                <button
                  type="button"
                  onClick={() => setViewMode("preview")}
                  aria-label={t("ui.artifact.preview")}
                  aria-pressed={viewMode === "preview"}
                  className={`flex h-9 w-9 items-center justify-center rounded-md transition ${
                    viewMode === "preview"
                      ? "bg-surface-soft text-ink-heading"
                      : "text-ink-body hover:bg-surface-soft hover:text-ink-heading"
                  }`}
                  title={t("ui.artifact.preview")}
                >
                  <Eye className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("source")}
                  aria-label={t("ui.artifact.sourceCode")}
                  aria-pressed={viewMode === "source"}
                  className={`flex h-9 w-9 items-center justify-center rounded-md transition ${
                    viewMode === "source"
                      ? "bg-surface-soft text-ink-heading"
                      : "text-ink-body hover:bg-surface-soft hover:text-ink-heading"
                  }`}
                  title={t("ui.artifact.sourceCode")}
                >
                  <Code2 className="h-3.5 w-3.5" />
                </button>
              </>
            ) : null}
            {artifact?.previewKind === "pdf" && fullscreenSupported ? (
              <button
                type="button"
                onClick={() => {
                  if (document.fullscreenElement === shellRef.current) {
                    void document.exitFullscreen();
                  } else {
                    void shellRef.current?.requestFullscreen();
                  }
                }}
                aria-label={
                  isFullscreen
                    ? t("ui.artifact.exitFullscreen")
                    : t("ui.artifact.enterFullscreen")
                }
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title={t("ui.artifact.fullscreenTitle", {
                  action: isFullscreen
                    ? t("ui.artifact.exitFullscreen")
                    : t("ui.artifact.enterFullscreen"),
                })}
              >
                {isFullscreen ? (
                  <Minimize2 className="h-3.5 w-3.5" />
                ) : (
                  <Maximize2 className="h-3.5 w-3.5" />
                )}
              </button>
            ) : null}
            {onCopyContent ? (
              <button
                type="button"
                onClick={onCopyContent}
                disabled={!artifact?.capabilities.canCopyContent}
                aria-label={t("ui.artifact.copyContent")}
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
                title={t("ui.artifact.copyContent")}
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onOpenExternal ? (
              <button
                type="button"
                onClick={onOpenExternal}
                disabled={!artifact?.capabilities.canOpenExternal}
                aria-label={t("ui.artifact.openExternal")}
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
                title={t("ui.artifact.openExternal")}
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onReload ? (
              <button
                type="button"
                onClick={onReload}
                aria-label={t("ui.artifact.refresh")}
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title={t("ui.artifact.refresh")}
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                aria-label={t("ui.artifact.close")}
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title={t("ui.artifact.close")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        </div>
      </header>
      <div className="min-h-0 flex-1">
        {loading ? (
          <div
            className="flex h-full items-center justify-center text-sm text-ink-meta"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            {t("ui.artifact.readingFile")}
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-6 py-16">
            <div
              className="max-w-[420px] rounded-xl border border-error-light bg-error-light px-5 py-4 text-error-text"
              role="alert"
            >
              <div className="text-sm font-medium">{t("ui.artifact.previewFailed")}</div>
              <p className="mt-1 text-xs leading-5">{error}</p>
              {onReload ? (
                <button
                  type="button"
                  onClick={onReload}
                  className="mt-3 inline-flex h-8 items-center rounded-md border border-error-text/20 bg-surface px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
                >
                  {t("ui.artifact.retry")}
                </button>
              ) : null}
            </div>
          </div>
        ) : artifact ? (
          <ArtifactImageZoomContext.Provider
            value={{ zoom: imageZoom, onZoomChange: setImageZoom }}
          >
            <ArtifactViewModeContext.Provider
              value={{ mode: viewMode, onModeChange: setViewMode }}
            >
              <ArtifactRenderer
                artifact={artifact}
                content={content}
                target={target}
                onOpenExternal={onOpenExternal}
                onReload={onReload}
              />
            </ArtifactViewModeContext.Provider>
          </ArtifactImageZoomContext.Provider>
        ) : null}
      </div>
    </article>
  );
}
