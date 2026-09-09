import { useEffect, useRef, useState } from "react";

import type { ArtifactRendererProps } from "./artifact-viewer.types";

import { t as _t } from "@valuz/shared/i18n";
import { useI18n } from "../../hooks/use-i18n";
import { LoadingState } from "../common/LoadingState";

/**
 * ``.pptx`` preview, in the same shape as {@link DocxRenderer}: fetch the bytes
 * from the resolved address, hand them to a client-side renderer, tear it down
 * on unmount. The backend never proxies the file, so this works unchanged for a
 * local desktop path (``valuz-local://``) and a cloud presigned URL alike.
 *
 * Slide geometry is authored in absolute units, so the renderer needs concrete
 * pixel dimensions rather than a CSS-sized box. We measure the container and
 * re-render when it changes width, which is what keeps a deck readable in the
 * narrow split pane and in fullscreen.
 */

/**
 * The library's own declarations are reachable (``dist/index.d.ts``) but it
 * re-exports no type names, so the previewer's type is taken off ``init``.
 */
type SlideDeck = ReturnType<typeof import("pptx-preview").init>;

/** 16:9 at a size that stays sharp when the pane is wide. */
const SLIDE_ASPECT = 9 / 16;
const MAX_SLIDE_WIDTH = 1280;
/** Resize granularity — each step costs a re-parse of the whole file. */
const WIDTH_STEP = 40;

export function PresentationRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const openUrl = content?.kind === "binary" ? content.openUrl : null;

  // Measure first, render second: handing the renderer a zero width produces a
  // deck of empty slides that never recovers, because it lays out once.
  useEffect(() => {
    const host = containerRef.current?.parentElement;
    if (!host) return;
    const measure = () => {
      const next = Math.min(Math.floor(host.clientWidth), MAX_SLIDE_WIDTH);
      // Snap DOWN to a step: down so a slide is never wider than the pane
      // holding it, and stepped so dragging the split pane does not re-parse
      // the file on every animation frame.
      setWidth(next > 0 ? Math.floor(next / WIDTH_STEP) * WIDTH_STEP : 0);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!openUrl || !containerRef.current || width <= 0) return;
    const documentUrl = openUrl;
    const controller = new AbortController();
    const container = containerRef.current;
    let previewer: SlideDeck | null = null;
    setLoading(true);
    setError(null);
    container.innerHTML = "";

    async function renderPresentation() {
      try {
        const response = await fetch(documentUrl, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(
            _t("ui.artifact.httpReadFailed", { status: response.status }),
          );
        }
        const buffer = await response.arrayBuffer();
        const { init } = await import("pptx-preview");
        if (controller.signal.aborted) return;
        previewer = init(container, {
          width,
          height: Math.round(width * SLIDE_ASPECT),
          // Every slide stacked and scrollable. The alternative ("slide") adds
          // the library's own pager, which would compete with the viewer's.
          mode: "list",
        });
        await previewer.preview(buffer);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof Error
            ? cause.message
            : _t("ui.artifact.presentationRenderError"),
        );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void renderPresentation();
    return () => {
      controller.abort();
      // ``destroy`` detaches the library's own listeners; clearing the node
      // alone would leak them across every resize.
      previewer?.destroy();
      container.innerHTML = "";
    };
  }, [openUrl, width]);

  if (!openUrl) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-16">
        <div className="max-w-[420px] rounded-lg border border-surface-border bg-surface-soft px-5 py-4">
          <div className="text-sm font-medium text-ink-heading">
            {t("ui.artifact.presentationReadFailed")}
          </div>
          <p className="mt-1 text-xs leading-5 text-ink-body">
            {artifact.name}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-0 overflow-auto bg-surface-base p-5">
      {loading ? (
        <LoadingState
          variant="page"
          label={t("ui.artifact.presentationRendering")}
        />
      ) : null}
      {error ? (
        <div className="flex h-full items-center justify-center px-6 py-16">
          <div
            className="max-w-[420px] rounded-xl border border-error-light bg-error-light px-5 py-4 text-error-text"
            role="alert"
          >
            <div className="text-sm font-medium">
              {t("ui.artifact.presentationPreviewFailed")}
            </div>
            <p className="mt-1 text-xs leading-5">{error}</p>
          </div>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="mx-auto flex min-h-full max-w-full flex-col items-center gap-4 [&>div]:!mx-auto [&>div]:rounded-sm [&>div]:shadow-sm"
        />
      )}
    </div>
  );
}
