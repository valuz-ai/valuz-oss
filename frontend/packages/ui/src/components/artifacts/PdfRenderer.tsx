import { ExternalLink } from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { PdfDocumentRenderer } from "../reader/PdfDocumentRenderer";
import type { ArtifactRendererProps } from "./artifact-viewer.types";

export function PdfRenderer({
  artifact,
  content,
  target,
  onOpenExternal,
  onReload,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const pdfUrl =
    content?.kind === "binary" && content.mimeType === "application/pdf"
      ? content.openUrl
      : null;

  if (!pdfUrl) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div
          className="max-w-md rounded-lg border border-error-light bg-error-light px-5 py-4 text-error-text"
          role="alert"
        >
          <div className="text-sm font-medium">
            {t("ui.artifact.pdfPreviewUnavailable")}
          </div>
          <p className="mt-1 text-xs leading-5">
            {t("ui.artifact.pdfNoAddress")}
          </p>
          {onOpenExternal ? (
            <button
              type="button"
              onClick={onOpenExternal}
              className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md border border-error-text/20 bg-surface px-3 text-xs font-medium text-error-text transition hover:bg-surface-soft"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {t("ui.artifact.openExternal")}
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <PdfDocumentRenderer
      url={pdfUrl}
      title={artifact.name}
      location={
        target?.page ? { kind: "pdf", page: target.page } : undefined
      }
      onReload={onReload}
    />
  );
}
