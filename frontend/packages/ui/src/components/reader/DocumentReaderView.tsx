import { useMemo } from "react";
import { Download, ExternalLink, Loader2, RefreshCw, X } from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { ArtifactRenderer } from "../artifacts/ArtifactViewerShell";
import type {
  ArtifactContent,
  ArtifactDescriptor,
  ArtifactPreviewKind,
} from "../artifacts/artifact-viewer.types";
import { ChunksRenderer } from "./ChunksRenderer";
import type {
  DocumentReaderViewProps,
  DocumentSource,
} from "./document-reader.types";

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
  location,
  sidePanel,
  onClose,
  onReload,
}: DocumentReaderViewProps) {
  const { t } = useI18n();
  const bridged = useMemo(() => (doc ? toArtifact(doc) : null), [doc]);
  const published = formatPublished(doc?.publishedAt);

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
      return <ChunksRenderer chunks={doc.render.chunks} location={location} />;
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
    <div className="flex h-full min-h-0 flex-col">
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

      <div className="flex min-h-0 flex-1">
        {sidePanel ? (
          <aside className="hidden w-[280px] shrink-0 overflow-y-auto border-r border-surface-border lg:block">
            {sidePanel}
          </aside>
        ) : null}
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">{body()}</div>
      </div>
    </div>
  );
}
