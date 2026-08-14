import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  BookOpen,
  Download,
  ExternalLink,
  Globe,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { usePersistentScroll } from "../../hooks/use-persistent-scroll";
import { ArtifactRenderer } from "../artifacts/ArtifactViewerShell";
import type {
  ArtifactContent,
  ArtifactDescriptor,
  ArtifactPreviewKind,
} from "../artifacts/artifact-viewer.types";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { ChunksRenderer } from "./ChunksRenderer";
import { HtmlDocumentRenderer } from "./HtmlDocumentRenderer";
import { PdfDocumentRenderer } from "./PdfDocumentRenderer";
import type {
  DocumentReaderViewProps,
  DocumentSource,
} from "./document-reader.types";

const RESEARCH_WIDTH_STORAGE_KEY = "valuz.reader.researchWidth.v6";
const RESEARCH_MIN_WIDTH = 360;
// PDF pages fit the available document pane, so the standard 60/40 split no
// longer needs to reserve the fixed width of an A4 page rendered at 125%.
const DOCUMENT_MIN_WIDTH = 480;
const SPLITTER_WIDTH = 8;
const DEFAULT_RESEARCH_RATIO = 0.4;

function formatPublished(value?: number): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function previewKindFor(mimeType: string): ArtifactPreviewKind {
  if (mimeType === "application/pdf") return "pdf";
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("audio/") || mimeType.startsWith("video/"))
    return "media";
  if (mimeType === "text/html") return "html";
  return "plain";
}

/**
 * Bridge to the artifact renderers: file / media / html bodies are the same
 * embedding problem the artifact viewer already solved, so they reuse those
 * renderers rather than growing a second implementation. Only ``chunks`` — the
 * shape artifacts have no concept of — is new here.
 */
function toArtifact(
  doc: DocumentSource,
): { artifact: ArtifactDescriptor; content: ArtifactContent } | null {
  const base = {
    id: doc.id,
    kind: "document",
    name: doc.title,
    capabilities: {
      canPreview: true,
      canEdit: false,
      canOpenExternal: Boolean(doc.originalUrl),
      canCopyContent: false,
      canDownload: Boolean(doc.downloadUrl),
    },
  };
  if (doc.render.kind === "file" || doc.render.kind === "media") {
    const { url, mimeType } = doc.render;
    return {
      artifact: {
        ...base,
        mimeType,
        previewKind: previewKindFor(mimeType),
      },
      content: { kind: "binary", openUrl: url, mimeType },
    };
  }
  if (doc.render.kind === "html") {
    return {
      artifact: { ...base, mimeType: "text/html", previewKind: "html" },
      content: {
        kind: "text",
        encoding: "utf-8",
        content: doc.render.html,
        truncated: false,
      },
    };
  }
  return null;
}

/**
 * Standalone document reading view: header (title / source / actions) + body +
 * an optional left slot. Hosts own fetching and routing — this component takes
 * a resolved ``DocumentSource`` and a resolved ``location``, and never reads the
 * URL itself (deep-link contract lives with the host).
 */
export function DocumentReaderView({
  doc,
  loading,
  error,
  framed = true,
  location,
  sidePanel,
  onClose,
  onReload,
  onLoadError,
}: DocumentReaderViewProps) {
  const { t } = useI18n();
  const bridged = useMemo(() => (doc ? toArtifact(doc) : null), [doc]);
  const published = formatPublished(doc?.publishedAt);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const documentScrollRef = useRef<HTMLDivElement>(null);
  const [researchOpen, setResearchOpen] = useState(true);
  const [mobilePane, setMobilePane] = useState<"document" | "research">(
    "document",
  );
  const [researchWidth, setResearchWidth] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const stored = Number(
      window.localStorage.getItem(RESEARCH_WIDTH_STORAGE_KEY),
    );
    return Number.isFinite(stored) && stored >= RESEARCH_MIN_WIDTH
      ? stored
      : null;
  });
  const locationKey = useMemo(() => JSON.stringify(location ?? null), [location]);
  usePersistentScroll(
    documentScrollRef,
    doc && !location ? `valuz.reader.documentScroll:${doc.id}` : null,
    Boolean(doc) && !loading && !error,
  );

  useEffect(() => {
    if (locationKey !== "null") setMobilePane("document");
  }, [locationKey]);

  const clampResearchWidth = (value: number): number => {
    const total = workspaceRef.current?.getBoundingClientRect().width ?? 960;
    const maximum = Math.max(
      RESEARCH_MIN_WIDTH,
      total - DOCUMENT_MIN_WIDTH - SPLITTER_WIDTH,
    );
    return Math.max(RESEARCH_MIN_WIDTH, Math.min(value, maximum));
  };

  const setAndPersistResearchWidth = (value: number) => {
    const next = clampResearchWidth(value);
    setResearchWidth(next);
    window.localStorage.setItem(
      RESEARCH_WIDTH_STORAGE_KEY,
      String(Math.round(next)),
    );
  };

  const beginResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const right = workspace.getBoundingClientRect().right;
    const onMove = (moveEvent: PointerEvent) => {
      setResearchWidth(clampResearchWidth(right - moveEvent.clientX));
    };
    const onUp = (upEvent: PointerEvent) => {
      const next = clampResearchWidth(right - upEvent.clientX);
      setAndPersistResearchWidth(next);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  const body = () => {
    if (loading) {
      return (
        <div
          className="flex h-full items-center justify-center text-sm text-ink-meta"
          role="status"
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          {t("common.loading")}
        </div>
      );
    }
    if (error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
          <p className="text-sm text-ink-body">{error}</p>
          {onReload ? (
            <button
              type="button"
              onClick={onReload}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-surface-border px-3 text-xs font-medium text-ink-heading transition hover:bg-surface-muted"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              {t("common.retry")}
            </button>
          ) : null}
        </div>
      );
    }
    if (!doc) {
      return (
        <div className="flex h-full items-center justify-center text-sm text-ink-meta">
          {t("ui.reader.empty")}
        </div>
      );
    }
    if (doc.render.kind === "chunks") {
      return (
        <>
          <ChunksRenderer chunks={doc.render.chunks} location={location} />
          {doc.originalUrl ? (
            <div className="mx-auto w-full max-w-[760px] px-8 pb-8">
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <a
                      href={doc.originalUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group inline-flex items-center gap-1.5 text-sm font-medium text-primary no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                    >
                      <Globe
                        className="-ml-px h-3.5 w-3.5 shrink-0"
                        aria-hidden="true"
                      />
                      <span className="border-b border-dotted border-transparent leading-5 group-hover:border-current group-focus-visible:border-current">
                        {t("ui.reader.originalLink" as Parameters<typeof t>[0])}
                      </span>
                    </a>
                  </TooltipTrigger>
                  <TooltipContent
                    data-original-link-tooltip
                    side="bottom"
                    align="start"
                    alignOffset={0}
                    sideOffset={6}
                    className="w-[min(360px,calc(100vw-32px))] rounded-lg border border-surface-border bg-surface px-3 py-2.5 text-left text-xs font-normal text-ink-body shadow-xl [overflow-wrap:anywhere]"
                  >
                    {doc.originalUrl}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          ) : null}
        </>
      );
    }
    if (doc.render.kind === "html") {
      return (
        <HtmlDocumentRenderer
          html={doc.render.html}
          title={doc.title}
          location={location}
        />
      );
    }
    if (
      doc.render.kind === "file" &&
      doc.render.mimeType === "application/pdf"
    ) {
      return (
        <PdfDocumentRenderer
          url={doc.render.url}
          title={doc.title}
          location={location}
          onReload={onReload}
          onLoadError={onLoadError}
        />
      );
    }
    if (doc.render.kind === "external") {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
          <p className="text-sm text-ink-body">
            {t("ui.reader.externalOnly")}
          </p>
          <a
            href={doc.render.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-surface-border px-3 text-xs font-medium text-ink-heading transition hover:bg-surface-muted"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            {t("ui.reader.openOriginal")}
          </a>
        </div>
      );
    }
    if (!bridged) return null;
    return (
      <ArtifactRenderer
        artifact={bridged.artifact}
        content={bridged.content}
        target={location?.page ? { page: location.page } : null}
        onOpenExternal={
          doc.originalUrl
            ? () =>
                window.open(doc.originalUrl, "_blank", "noopener,noreferrer")
            : undefined
        }
      />
    );
  };

  return (
    <div
      className={`flex h-full min-h-0 flex-col overflow-hidden bg-surface ${
        framed ? "rounded-[14px] border border-surface-border" : ""
      }`}
    >
      <header className="flex shrink-0 items-start gap-3 border-b border-surface-border px-5 py-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-medium text-ink-heading">
            {doc?.title ?? ""}
          </h1>
          <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-ink-meta">
            {doc?.source?.logoUrl ? (
              <img
                src={doc.source.logoUrl}
                alt=""
                className="h-3.5 w-3.5 shrink-0 rounded-sm object-cover"
              />
            ) : null}
            {doc?.source?.name ? (
              <span className="truncate">{doc.source.name}</span>
            ) : null}
            {doc?.source?.name && published ? <span>·</span> : null}
            {published ? (
              <span className="shrink-0 tabular-nums">{published}</span>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {doc?.originalUrl ? (
            <a
              href={doc.originalUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={t("ui.reader.openOriginal")}
              title={t("ui.reader.openOriginal")}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-meta transition hover:bg-surface-muted hover:text-ink-heading"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ) : null}
          {doc?.downloadUrl ? (
            <a
              href={doc.downloadUrl}
              download
              aria-label={t("ui.reader.download")}
              title={t("ui.reader.download")}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-meta transition hover:bg-surface-muted hover:text-ink-heading"
            >
              <Download className="h-3.5 w-3.5" />
            </a>
          ) : null}
          {sidePanel ? (
            <button
              type="button"
              onClick={() => setResearchOpen((value) => !value)}
              aria-label={t(
                researchOpen
                  ? ("ui.reader.collapseResearch" as Parameters<typeof t>[0])
                  : ("ui.reader.expandResearch" as Parameters<typeof t>[0]),
              )}
              title={t(
                researchOpen
                  ? ("ui.reader.collapseResearch" as Parameters<typeof t>[0])
                  : ("ui.reader.expandResearch" as Parameters<typeof t>[0]),
              )}
              className="hidden h-7 w-7 items-center justify-center rounded-md text-ink-meta transition hover:bg-surface-muted hover:text-ink-heading lg:inline-flex"
            >
              {researchOpen ? (
                <PanelRightClose className="h-3.5 w-3.5" />
              ) : (
                <PanelRightOpen className="h-3.5 w-3.5" />
              )}
            </button>
          ) : null}
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              aria-label={t("common.close")}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-meta transition hover:bg-surface-muted hover:text-ink-heading"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </header>

      {sidePanel ? (
        <div className="flex h-10 shrink-0 items-center border-b border-surface-border bg-surface px-2 lg:hidden">
          <button
            type="button"
            aria-pressed={mobilePane === "document"}
            onClick={() => setMobilePane("document")}
            className={`inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md text-xs font-medium ${
              mobilePane === "document"
                ? "bg-surface-muted text-ink-heading"
                : "text-ink-meta"
            }`}
          >
            <BookOpen className="h-3.5 w-3.5" />
            {t("ui.reader.documentTab" as Parameters<typeof t>[0])}
          </button>
          <button
            type="button"
            aria-pressed={mobilePane === "research"}
            onClick={() => setMobilePane("research")}
            className={`inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md text-xs font-medium ${
              mobilePane === "research"
                ? "bg-surface-muted text-ink-heading"
                : "text-ink-meta"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            {t("ui.reader.researchTab" as Parameters<typeof t>[0])}
          </button>
        </div>
      ) : null}

      <div ref={workspaceRef} className="flex min-h-0 flex-1">
        <div
          ref={documentScrollRef}
          className={`min-h-0 min-w-0 flex-1 overflow-y-auto bg-surface ${
            sidePanel && mobilePane !== "document" ? "hidden lg:block" : ""
          }`}
        >
          {body()}
        </div>
        {sidePanel ? (
          <>
            {researchOpen ? (
              <button
                type="button"
                role="separator"
                aria-orientation="vertical"
                aria-label={t(
                  "ui.reader.resizeResearch" as Parameters<typeof t>[0],
                )}
                onPointerDown={beginResize}
                onDoubleClick={() => {
                  setResearchWidth(null);
                  window.localStorage.removeItem(RESEARCH_WIDTH_STORAGE_KEY);
                }}
                onKeyDown={(event) => {
                  const current =
                    researchWidth ??
                    (workspaceRef.current?.getBoundingClientRect().width ??
                      960) *
                      DEFAULT_RESEARCH_RATIO;
                  if (event.key === "ArrowLeft") {
                    event.preventDefault();
                    setAndPersistResearchWidth(current + 16);
                  } else if (event.key === "ArrowRight") {
                    event.preventDefault();
                    setAndPersistResearchWidth(current - 16);
                  }
                }}
                className="relative hidden w-2 shrink-0 cursor-col-resize bg-transparent outline-none transition before:absolute before:inset-y-6 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-surface-border/60 hover:bg-accent/5 focus:bg-accent/10 lg:block"
              />
            ) : null}
            <aside
              className={`min-h-0 shrink-0 overflow-y-auto bg-surface ${
                mobilePane === "research" ? "block w-full" : "hidden"
              } ${
                researchOpen
                  ? "lg:block lg:w-[var(--research-width)]"
                  : "lg:hidden"
              }`}
              style={{
                "--research-width":
                  researchWidth !== null
                    ? `clamp(${RESEARCH_MIN_WIDTH}px, ${researchWidth}px, calc(100% - ${DOCUMENT_MIN_WIDTH + SPLITTER_WIDTH}px))`
                    : `clamp(${RESEARCH_MIN_WIDTH}px, ${DEFAULT_RESEARCH_RATIO * 100}%, calc(100% - ${DOCUMENT_MIN_WIDTH + SPLITTER_WIDTH}px))`,
              } as CSSProperties}
            >
              {sidePanel}
            </aside>
          </>
        ) : null}
      </div>
    </div>
  );
}
