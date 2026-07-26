import {
  Copy,
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
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { Badge } from "../ui/badge";
import { PdfRenderer } from "./PdfRenderer";
import type {
  ArtifactContent,
  ArtifactDescriptor,
  ArtifactPreviewKind,
  ArtifactRendererProps,
  ArtifactViewerShellProps,
} from "./artifact-viewer.types";

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

  if (imageUrl) {
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
              aria-label="缩小图片"
              title="缩小"
            >
              <ZoomOut className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setZoom("fit")}
              aria-label="图片适合窗口"
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
              aria-label="放大图片"
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
              正在加载图片
            </div>
          ) : null}
          {loadState === "error" ? (
            <div
              className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base px-6 text-sm text-error-text"
              role="alert"
            >
              无法加载图片
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

function MediaRenderer({ artifact, content }: ArtifactRendererProps) {
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
        正在加载媒体
      </div>
    ) : loadState === "error" ? (
      <div
        className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base px-6 text-sm text-error-text"
        role="alert"
      >
        无法加载媒体文件
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
          当前环境不支持音频预览。
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
          当前环境不支持视频预览。
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
  const [mode, setMode] = useState<PreviewSourceMode>("preview");
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
          文件较大，当前仅显示前 5 MiB。
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
}: ArtifactRendererProps) {
  const Renderer = ARTIFACT_RENDERERS[artifact.previewKind] ?? UnsupportedRenderer;
  return (
    <Suspense
      fallback={
        <div
          className="flex h-full items-center justify-center text-sm text-ink-meta"
          role="status"
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          正在加载预览器
        </div>
      }
    >
      <Renderer
        artifact={artifact}
        content={content}
        target={target}
        onOpenExternal={onOpenExternal}
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
  onReload,
  onClose,
  onCopyContent,
  onOpenExternal,
}: ArtifactViewerShellProps) {
  const shellRef = useRef<HTMLElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const fullscreenSupported =
    typeof Element !== "undefined" &&
    typeof Element.prototype.requestFullscreen === "function";
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
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-surface shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
      tabIndex={0}
      aria-busy={loading}
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
          <div className="flex shrink-0 items-center gap-1">
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
                aria-label={isFullscreen ? "退出全屏" : "进入全屏"}
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title={`${isFullscreen ? "退出全屏" : "进入全屏"}（⌘⇧F）`}
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
                aria-label="复制内容"
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
                title="复制内容"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onOpenExternal ? (
              <button
                type="button"
                onClick={onOpenExternal}
                disabled={!artifact?.capabilities.canOpenExternal}
                aria-label="外部打开"
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading disabled:pointer-events-none disabled:opacity-40"
                title="外部打开"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onReload ? (
              <button
                type="button"
                onClick={onReload}
                aria-label="刷新"
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title="刷新"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭"
                className="flex h-9 w-9 items-center justify-center rounded-md text-ink-body transition hover:bg-surface-soft hover:text-ink-heading"
                title="关闭"
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
            正在读取文件
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-6 py-16">
            <div
              className="max-w-[420px] rounded-[10px] border border-error-light bg-error-light px-5 py-4 text-error-text"
              role="alert"
            >
              <div className="text-sm font-medium">无法预览文件</div>
              <p className="mt-1 text-xs leading-5">{error}</p>
              {onReload ? (
                <button
                  type="button"
                  onClick={onReload}
                  className="mt-3 inline-flex h-8 items-center rounded-md border border-error-text/20 bg-surface px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
                >
                  重试
                </button>
              ) : null}
            </div>
          </div>
        ) : artifact ? (
          <ArtifactRenderer
            artifact={artifact}
            content={content}
            target={target}
            onOpenExternal={onOpenExternal}
          />
        ) : null}
      </div>
    </article>
  );
}
