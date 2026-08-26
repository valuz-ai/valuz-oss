import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ArtifactRendererProps } from "./artifact-viewer.types";

import { t as _t } from "@valuz/shared/i18n";
import { useI18n } from "../../hooks/use-i18n";


export function DocxRenderer({ artifact, content }: ArtifactRendererProps) {
  const { t } = useI18n();
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
        if (!response.ok)
          throw new Error(
            _t("ui.artifact.httpReadFailed", { status: response.status }),
          );
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
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof Error ? cause.message : _t("ui.artifact.docxRenderError"),
        );
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
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div className="max-w-[420px] rounded-lg border border-surface-border bg-surface-soft px-5 py-4">
          <div className="text-sm font-medium text-ink-heading">
            {t("ui.artifact.docxReadFailed")}
          </div>
          <p className="mt-1 text-xs leading-5 text-ink-body">{artifact.name}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-0 overflow-auto bg-surface-base p-5">
      {loading ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center bg-surface-base/80 text-sm text-ink-meta"
          role="status"
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          {t("ui.artifact.docxRendering")}
        </div>
      ) : null}
      {error ? (
        <div className="flex h-full items-center justify-center px-6 py-16">
          <div
            className="max-w-[420px] rounded-xl border border-error-light bg-error-light px-5 py-4 text-error-text"
            role="alert"
          >
            <div className="text-sm font-medium">{t("ui.artifact.docxPreviewFailed")}</div>
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
