import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

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
 * Every slide is stacked and scrollable, with a pager on top — the same
 * arrangement (and the same scroll-spy) the PDF reader uses, so paging and
 * scrolling agree with each other instead of being two ways to be lost. The
 * library ships its own pager under ``mode: "slide"``, but it renders one slide
 * at a time with unstyled buttons that would compete with the viewer's chrome.
 */

/**
 * The library's own declarations are reachable (``dist/index.d.ts``) but it
 * re-exports no type names, so the previewer's type is taken off ``init``.
 */
type SlideDeck = ReturnType<typeof import("pptx-preview").init>;

/**
 * Provisional aspect for the first layout. The real one is only known after
 * the file is parsed, and re-parsing to learn it would double the work — so
 * the boxes are corrected from ``pptx.width/height`` once, after render.
 */
const SLIDE_ASPECT = 9 / 16;
const MAX_SLIDE_WIDTH = 1280;
/** Resize granularity — each step costs a re-parse of the whole file. */
const WIDTH_STEP = 40;
/** The library's per-slide box, whose height it takes from our options. */
const SLIDE_BOX_SELECTOR = ".pptx-preview-slide-wrapper";
/** Stamped on each box so the pager and the scroll-spy can address it. */
const SLIDE_INDEX_ATTR = "data-slide-index";

export function PresentationRenderer({
  artifact,
  content,
}: ArtifactRendererProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const [slideCount, setSlideCount] = useState(0);
  const [currentSlide, setCurrentSlide] = useState(1);
  const openUrl = content?.kind === "binary" ? content.openUrl : null;

  // Measure first, render second: handing the renderer a zero width produces a
  // deck of empty slides that never recovers, because it lays out once.
  // Measured on the mount node, not its padded parent — the parent's width
  // includes the padding the slides must fit inside.
  useEffect(() => {
    const host = containerRef.current;
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
          // Every slide stacked and scrollable. "slide" mode would show one at
          // a time behind the library's own unstyled pager.
          mode: "list",
        });
        await previewer.preview(buffer);
        if (controller.signal.aborted) return;

        // The library sizes every slide box from the options above, so a deck
        // that is not 16:9 gets letterboxed (its wrapper is ``overflow:
        // hidden``, so a too-short box also clips). It knows its real
        // dimensions only after parsing; correct the boxes from them rather
        // than read the file twice.
        const deck = previewer.pptx;
        const boxes =
          container.querySelectorAll<HTMLElement>(SLIDE_BOX_SELECTOR);
        const slideHeight =
          deck && deck.width > 0 && deck.height > 0
            ? Math.round((width * deck.height) / deck.width)
            : null;
        boxes.forEach((box, index) => {
          if (slideHeight !== null) box.style.height = `${slideHeight}px`;
          box.setAttribute(SLIDE_INDEX_ATTR, String(index + 1));
        });
        setSlideCount(boxes.length);
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

  const goToSlide = useCallback(
    (next: number) => {
      if (slideCount === 0) return;
      const slide = Math.min(slideCount, Math.max(1, next));
      setCurrentSlide(slide);
      scrollRef.current
        ?.querySelector<HTMLElement>(`[${SLIDE_INDEX_ATTR}="${slide}"]`)
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
    },
    [slideCount],
  );

  /**
   * Keep the counter honest while the user scrolls, so paging always resumes
   * from what is on screen. Throttled to a frame — scroll fires far faster
   * than this needs to run.
   */
  const syncSlideFromScroll = useCallback(() => {
    if (scrollFrameRef.current !== null) return;
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const root = scrollRef.current;
      if (!root) return;
      const viewportCenter =
        root.getBoundingClientRect().top + root.clientHeight / 2;
      let closest = 1;
      let closestDistance = Number.POSITIVE_INFINITY;
      root
        .querySelectorAll<HTMLElement>(`[${SLIDE_INDEX_ATTR}]`)
        .forEach((node) => {
          const index = Number(node.getAttribute(SLIDE_INDEX_ATTR));
          if (!Number.isInteger(index)) return;
          const box = node.getBoundingClientRect();
          const distance =
            viewportCenter < box.top
              ? box.top - viewportCenter
              : viewportCenter > box.bottom
                ? viewportCenter - box.bottom
                : 0;
          if (distance < closestDistance) {
            closest = index;
            closestDistance = distance;
          }
        });
      setCurrentSlide((slide) => (slide === closest ? slide : closest));
    });
  }, []);

  useEffect(
    () => () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
    },
    [],
  );

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

  if (error) {
    return (
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
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-base">
      {slideCount > 1 ? (
        <div className="flex h-10 shrink-0 items-center justify-center gap-1 border-b border-surface-border bg-surface px-2">
          <button
            type="button"
            aria-label={t("ui.artifact.previousSlide")}
            disabled={currentSlide <= 1}
            onClick={() => goToSlide(currentSlide - 1)}
            className="rounded p-1.5 text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <span
            className="min-w-16 text-center text-xs tabular-nums text-ink-body"
            role="status"
          >
            {currentSlide} / {slideCount}
          </span>
          <button
            type="button"
            aria-label={t("ui.artifact.nextSlide")}
            disabled={currentSlide >= slideCount}
            onClick={() => goToSlide(currentSlide + 1)}
            className="rounded p-1.5 text-ink-body transition hover:bg-surface-muted hover:text-ink-heading disabled:opacity-40"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
      <div
        ref={scrollRef}
        onScroll={syncSlideFromScroll}
        onKeyDown={(event) => {
          if (slideCount <= 1) return;
          if (event.key === "ArrowRight" || event.key === "PageDown") {
            event.preventDefault();
            goToSlide(currentSlide + 1);
          } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
            event.preventDefault();
            goToSlide(currentSlide - 1);
          }
        }}
        // Focusable so the arrow keys have somewhere to land; the shell's own
        // shortcuts still work because this only claims the paging keys.
        tabIndex={0}
        className="relative min-h-0 flex-1 overflow-auto p-5 outline-none"
      >
        {loading ? (
          <LoadingState
            variant="page"
            label={t("ui.artifact.presentationRendering")}
          />
        ) : null}
        <div
          ref={containerRef}
          // The library hardcodes a black wrapper with a fixed height and
          // ``overflow-y: auto``. Combined with the 10px it puts under every
          // slide, that black shows through as a strip below the deck and in
          // the scrollbar gutter beside it. Let the wrapper size to its content
          // on our own background instead — the slides carry their own.
          className="mx-auto max-w-full [&_.pptx-preview-wrapper]:!h-auto [&_.pptx-preview-wrapper]:!overflow-visible [&_.pptx-preview-wrapper]:!bg-transparent [&_.pptx-preview-slide-wrapper]:!mb-4 [&_.pptx-preview-slide-wrapper]:rounded-sm [&_.pptx-preview-slide-wrapper]:shadow-sm [&_.pptx-preview-slide-wrapper:last-child]:!mb-0"
        />
      </div>
    </div>
  );
}
