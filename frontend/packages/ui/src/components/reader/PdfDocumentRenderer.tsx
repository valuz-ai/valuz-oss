import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Minus,
  Plus,
  RotateCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  PDFPageProxy,
} from "pdfjs-dist";
import type {
  NormalizedRectV1,
  TextQuoteSelectorV1,
} from "@valuz/shared";
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";

import { useI18n } from "../../hooks/use-i18n";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import type { DocumentLocation } from "./document-reader.types";
import { findBestTextQuote } from "./text-quote";
import {
  calculatePdfScale,
  clampPdfCustomScale,
  type PdfViewportSize,
  type PdfZoomMode,
} from "./pdf-zoom";
import "./PdfDocumentRenderer.css";

type LocateStatus =
  | "located-exact"
  | "located-fallback"
  | "page-only"
  | "not-found";
type PdfTextContent = Awaited<ReturnType<PDFPageProxy["getTextContent"]>>;

const DEFAULT_PDF_SCALE = 1.25;
const PDF_SCALE_STEP = 0.25;
const PDF_SCALE_PRESETS = [
  0.25,
  0.5,
  0.75,
  1,
  1.25,
  1.5,
  2,
  3,
  4,
] as const;

interface PixelRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

const PDF_HIGHLIGHT_HORIZONTAL_PADDING = 4;
const PDF_HIGHLIGHT_VERTICAL_PADDING = 3;

export function mapNormalizedPdfRects(
  rects: NormalizedRectV1[] | undefined,
  width: number,
  height: number,
): PixelRect[] {
  if (!rects || width <= 0 || height <= 0) return [];
  return rects.flatMap((rect) => {
    const values = [rect.x, rect.y, rect.width, rect.height];
    if (
      values.some((value) => !Number.isFinite(value)) ||
      rect.x < 0 ||
      rect.y < 0 ||
      rect.width <= 0 ||
      rect.height <= 0 ||
      rect.x + rect.width > 1.000001 ||
      rect.y + rect.height > 1.000001
    ) {
      return [];
    }
    return [
      {
        left: rect.x * width,
        top: rect.y * height,
        width: rect.width * width,
        height: rect.height * height,
      },
    ];
  });
}

export function padPdfHighlightRects(
  rects: PixelRect[],
  pageWidth: number,
  pageHeight: number,
): PixelRect[] {
  if (pageWidth <= 0 || pageHeight <= 0) return [];
  return rects.flatMap((rect) => {
    const left = Math.max(
      0,
      rect.left - PDF_HIGHLIGHT_HORIZONTAL_PADDING,
    );
    const top = Math.max(0, rect.top - PDF_HIGHLIGHT_VERTICAL_PADDING);
    const right = Math.min(
      pageWidth,
      rect.left + rect.width + PDF_HIGHLIGHT_HORIZONTAL_PADDING,
    );
    const bottom = Math.min(
      pageHeight,
      rect.top + rect.height + PDF_HIGHLIGHT_VERTICAL_PADDING,
    );
    return right > left && bottom > top
      ? [{ left, top, width: right - left, height: bottom - top }]
      : [];
  });
}

export function locatePdfTextItemIndexes(
  items: PdfTextContent["items"],
  selector: TextQuoteSelectorV1,
): number[] {
  let raw = "";
  const rawItemIndexes: number[] = [];
  let textDivIndex = 0;
  items.forEach((item) => {
    if (!("str" in item)) return;
    if (raw) {
      raw += " ";
      rawItemIndexes.push(Math.max(0, textDivIndex - 1));
    }
    raw += item.str;
    rawItemIndexes.push(
      ...Array.from({ length: item.str.length }, () => textDivIndex),
    );
    textDivIndex += 1;
  });
  const match = findBestTextQuote(raw, selector);
  if (!match) return [];
  return Array.from(
    new Set(rawItemIndexes.slice(match.start, match.end)),
  ).filter((index) => index >= 0);
}

export function canUseNormalizedPdfRects(input: {
  rects?: NormalizedRectV1[];
  locatorPageRotation?: number;
  documentPageRotation: number;
  viewerRotation: number;
}): boolean {
  return (
    Boolean(input.rects?.length) &&
    input.viewerRotation === 0 &&
    (input.locatorPageRotation === undefined
      ? input.documentPageRotation === 0
      : input.locatorPageRotation === input.documentPageRotation)
  );
}

function textLayerRects(
  itemIndexes: number[],
  textDivs: HTMLElement[],
  pageElement: HTMLElement,
): PixelRect[] {
  const pageBox = pageElement.getBoundingClientRect();
  return itemIndexes.flatMap((index) => {
    const div = textDivs[index];
    if (!div) return [];
    const box = div.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return [];
    return [
      {
        left: box.left - pageBox.left,
        top: box.top - pageBox.top,
        width: box.width,
        height: box.height,
      },
    ];
  });
}

function HighlightLayer({
  rects,
  status,
}: {
  rects: PixelRect[];
  status: LocateStatus;
}) {
  return (
    <div
      className="pointer-events-none absolute inset-0 z-20"
      data-citation-pdf-highlight={status}
    >
      {rects.map((rect, index) => (
        <span
          // Geometry and order together are stable for one location render.
          key={`${rect.left}:${rect.top}:${index}`}
          className="absolute rounded-sm bg-warning/35 ring-1 ring-warning/70 mix-blend-multiply"
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        />
      ))}
    </div>
  );
}

function PdfPage({
  pdf,
  pageNumber,
  scale,
  rotation,
  location,
  scrollRootRef,
  estimatedSize,
  eager,
  onLocated,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  rotation: number;
  location?: DocumentLocation;
  scrollRootRef: RefObject<HTMLDivElement | null>;
  estimatedSize: PdfViewportSize;
  eager: boolean;
  onLocated: (page: number, status: LocateStatus) => void;
}) {
  const { t } = useI18n();
  const pageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [page, setPage] = useState<PDFPageProxy | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [highlightRects, setHighlightRects] = useState<PixelRect[]>([]);
  const [locateStatus, setLocateStatus] =
    useState<LocateStatus>("page-only");
  const [nearViewport, setNearViewport] = useState(false);
  const shouldRender = eager || nearViewport;

  useEffect(() => {
    setSize({ width: 0, height: 0 });
  }, [pdf, rotation, scale]);

  useEffect(() => {
    const element = pageRef.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(Boolean(entry?.isIntersecting)),
      {
        root: scrollRootRef.current,
        // Keep roughly one desktop viewport rendered above and below the
        // visible page without painting the whole document into memory.
        rootMargin: "1200px 0px",
      },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [scrollRootRef]);

  useEffect(() => {
    if (!shouldRender) {
      setPage(null);
      return;
    }
    let cancelled = false;
    void pdf.getPage(pageNumber).then((next) => {
      if (!cancelled) setPage(next);
    });
    return () => {
      cancelled = true;
      setPage(null);
    };
  }, [pageNumber, pdf, shouldRender]);

  useEffect(() => {
    if (!page || !canvasRef.current || !textLayerRef.current) return;
    const canvas = canvasRef.current;
    const textContainer = textLayerRef.current;
    let disposed = false;
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
    let textLayer: { cancel(): void } | null = null;

    const render = async () => {
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const viewport = page.getViewport({
        scale,
        rotation: (page.rotate + rotation) % 360,
      });
      if (disposed) return;
      setSize({ width: viewport.width, height: viewport.height });

      const outputScale = Math.max(window.devicePixelRatio || 1, 1);
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("canvas_context_unavailable");
      renderTask = page.render({
        canvas,
        viewport,
        transform:
          outputScale === 1
            ? undefined
            : [outputScale, 0, 0, outputScale, 0, 0],
      });

      const textContent = await page.getTextContent();
      if (disposed) return;
      textContainer.replaceChildren();
      textContainer.style.setProperty(
        "--total-scale-factor",
        String(viewport.scale),
      );
      const layer = new pdfjs.TextLayer({
        textContentSource: textContent,
        container: textContainer,
        viewport,
      });
      textLayer = layer;
      await Promise.all([renderTask.promise, layer.render()]);
      if (disposed || !pageRef.current) return;

      let rects: PixelRect[] = [];
      let status: LocateStatus = "page-only";
      if (
        canUseNormalizedPdfRects({
          rects: location?.rects,
          locatorPageRotation: location?.pageRotation,
          documentPageRotation: page.rotate,
          viewerRotation: rotation,
        })
      ) {
        rects = mapNormalizedPdfRects(
          location?.rects,
          viewport.width,
          viewport.height,
        );
      }
      if (rects.length) {
        status = "located-exact";
      } else if (location?.quote) {
        const indexes = locatePdfTextItemIndexes(
          textContent.items,
          location.quote,
        );
        rects = textLayerRects(indexes, layer.textDivs, pageRef.current);
        status = rects.length ? "located-fallback" : "page-only";
      }
      setHighlightRects(
        padPdfHighlightRects(rects, viewport.width, viewport.height),
      );
      setLocateStatus(status);
      if (location) onLocated(pageNumber, status);
    };

    void render().catch(() => {
      if (!disposed) {
        setHighlightRects([]);
        setLocateStatus("page-only");
        if (location) onLocated(pageNumber, "page-only");
      }
    });
    return () => {
      disposed = true;
      renderTask?.cancel();
      textLayer?.cancel();
      canvas.width = 0;
      canvas.height = 0;
      textContainer.replaceChildren();
      setHighlightRects([]);
    };
  }, [location, onLocated, page, pageNumber, rotation, scale]);

  return (
    <section
      ref={pageRef}
      data-pdf-page={pageNumber}
      data-locate-status={locateStatus}
      className="relative mx-auto shrink-0 overflow-hidden bg-white"
      style={{
        width: size.width || estimatedSize.width,
        height: size.height || estimatedSize.height,
      }}
    >
      {shouldRender && !page ? (
        <div className="absolute inset-0 flex items-center justify-center text-ink-meta">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : null}
      <canvas ref={canvasRef} className="absolute inset-0" />
      <div
        ref={textLayerRef}
        className="valuz-pdf-text-layer z-10"
        aria-label={t("ui.reader.pdfPageText", { page: pageNumber })}
      />
      <HighlightLayer rects={highlightRects} status={locateStatus} />
    </section>
  );
}

export function PdfDocumentRenderer({
  url,
  title,
  location,
  onReload,
  onLoadError,
}: {
  url: string;
  title: string;
  location?: DocumentLocation;
  onReload?: () => void;
  onLoadError?: () => void;
}) {
  const { t } = useI18n();
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoomMode, setZoomMode] = useState<PdfZoomMode>("fit-width");
  const [customScale, setCustomScale] = useState(DEFAULT_PDF_SCALE);
  const [pageSize, setPageSize] = useState<PdfViewportSize | null>(null);
  const [viewportSize, setViewportSize] = useState<PdfViewportSize>({
    width: 0,
    height: 0,
  });
  const [rotation, setRotation] = useState(0);
  const [currentPage, setCurrentPage] = useState(
    Math.max(1, location?.page ?? 1),
  );
  const [pageInput, setPageInput] = useState(
    String(Math.max(1, location?.page ?? 1)),
  );
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);

  const scale = useMemo(
    () =>
      calculatePdfScale({
        mode: zoomMode,
        page: pageSize,
        viewport: viewportSize,
        customScale,
      }),
    [customScale, pageSize, viewportSize, zoomMode],
  );
  const zoomTriggerLabel =
    zoomMode === "fit-width"
      ? t("ui.reader.fitWidth")
      : zoomMode === "fit-page"
        ? t("ui.reader.fitPage")
        : `${Math.round(scale * 100)}%`;
  const zoomMenuValue =
    zoomMode === "custom" ? `scale-${customScale}` : zoomMode;

  useEffect(() => {
    setCurrentPage(Math.max(1, location?.page ?? 1));
  }, [location?.page]);

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

  useEffect(() => {
    let task: PDFDocumentLoadingTask | null = null;
    let cancelled = false;
    setPdf(null);
    setError(null);
    setZoomMode("fit-width");
    setCustomScale(DEFAULT_PDF_SCALE);
    setRotation(0);
    // The generic PDF.js 6 worker requires newer typed-array APIs than the
    // Electron Chromium runtime guarantees. The official legacy build carries
    // those worker-side compatibility shims while keeping the same public API.
    void import("pdfjs-dist/legacy/build/pdf.mjs")
      .then((pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        task = pdfjs.getDocument({ url });
        return task.promise;
      })
      .then((document) => {
        if (cancelled) return;
        setPdf(document);
        setCurrentPage((page) => Math.min(page, document.numPages));
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "pdf_load_failed");
        }
      });
    return () => {
      cancelled = true;
      void task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;

    const measure = () => {
      const styles = window.getComputedStyle(viewport);
      const horizontalPadding =
        (Number.parseFloat(styles.paddingLeft) || 0) +
        (Number.parseFloat(styles.paddingRight) || 0);
      const verticalPadding =
        (Number.parseFloat(styles.paddingTop) || 0) +
        (Number.parseFloat(styles.paddingBottom) || 0);
      const next = {
        width: Math.max(0, viewport.clientWidth - horizontalPadding),
        height: Math.max(0, viewport.clientHeight - verticalPadding),
      };
      setViewportSize((current) =>
        current.width === next.width && current.height === next.height
          ? current
          : next,
      );
    };

    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pdf) {
      setPageSize(null);
      return;
    }
    let cancelled = false;
    setPageSize(null);
    void pdf.getPage(currentPage).then((page) => {
      if (cancelled) return;
      const viewport = page.getViewport({
        scale: 1,
        rotation: (page.rotate + rotation) % 360,
      });
      setPageSize({ width: viewport.width, height: viewport.height });
    });
    return () => {
      cancelled = true;
    };
  }, [currentPage, pdf, rotation]);

  useEffect(() => {
    if (error) onLoadError?.();
  }, [error, onLoadError]);

  const onLocated = useCallback(
    (page: number) => {
      if (page !== currentPage) return;
      const target = scrollRef.current?.querySelector<HTMLElement>(
        `[data-pdf-page="${page}"]`,
      );
      if (!target) return;
      const reduced = window.matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      target.scrollIntoView({
        block: "center",
        behavior: reduced ? "auto" : "smooth",
      });
    },
    [currentPage],
  );

  const pages = useMemo(
    () =>
      pdf
        ? Array.from({ length: pdf.numPages }, (_, index) => index + 1)
        : [],
    [pdf],
  );
  const estimatedPageSize = useMemo(
    () => ({
      width: (pageSize?.width ?? 612) * scale,
      height: (pageSize?.height ?? 792) * scale,
    }),
    [pageSize, scale],
  );

  const syncCurrentPageFromScroll = useCallback(() => {
    if (scrollFrameRef.current !== null) return;
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const root = scrollRef.current;
      if (!root) return;
      const rootBox = root.getBoundingClientRect();
      const viewportCenter = rootBox.top + root.clientHeight / 2;
      let closestPage = currentPage;
      let closestDistance = Number.POSITIVE_INFINITY;
      root.querySelectorAll<HTMLElement>("[data-pdf-page]").forEach((node) => {
        const page = Number(node.dataset.pdfPage);
        if (!Number.isInteger(page)) return;
        const box = node.getBoundingClientRect();
        const distance =
          viewportCenter < box.top
            ? box.top - viewportCenter
            : viewportCenter > box.bottom
              ? viewportCenter - box.bottom
              : 0;
        if (distance < closestDistance) {
          closestPage = page;
          closestDistance = distance;
        }
      });
      setCurrentPage((page) => (page === closestPage ? page : closestPage));
    });
  }, [currentPage]);

  useEffect(
    () => () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
    },
    [],
  );

  const navigateToPage = useCallback(
    (nextPage: number) => {
      if (!pdf) return;
      const page = Math.min(pdf.numPages, Math.max(1, nextPage));
      setCurrentPage(page);
      setPageInput(String(page));
      scrollRef.current
        ?.querySelector<HTMLElement>(`[data-pdf-page="${page}"]`)
        ?.scrollIntoView({ block: "start" });
    },
    [pdf],
  );

  const commitPageInput = useCallback(() => {
    if (!pdf || !/^\d+$/.test(pageInput)) {
      setPageInput(String(currentPage));
      return;
    }
    navigateToPage(Number(pageInput));
  }, [currentPage, navigateToPage, pageInput, pdf]);

  const adjustCustomScale = (delta: number) => {
    setCustomScale(clampPdfCustomScale(scale + delta));
    setZoomMode("custom");
  };

  if (error) {
    return (
      <div
        className="flex h-full min-h-0 items-center justify-center px-6 text-center"
        data-pdfjs-document
      >
        <div role="alert">
          <p className="text-sm text-ink-body">
            {t("ui.artifact.pdfLoadFailed")}
          </p>
          {onReload ? (
            <button
              type="button"
              onClick={onReload}
              className="mt-3 inline-flex h-8 items-center rounded-md border border-surface-border px-3 text-xs font-medium text-ink-heading transition hover:bg-surface-muted"
            >
              {t("common.retry")}
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-surface"
      data-pdfjs-document
      data-pdf-zoom-mode={zoomMode}
      data-pdf-scale={scale.toFixed(4)}
      aria-label={title}
    >
      <div className="flex h-10 shrink-0 items-center justify-center gap-1 bg-surface px-2">
        <button
          type="button"
          aria-label={t("ui.reader.previousPage")}
          disabled={!pdf || currentPage <= 1}
          onClick={() => navigateToPage(currentPage - 1)}
          className="rounded p-1.5 hover:bg-surface-muted disabled:opacity-40"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <div className="flex min-w-16 items-center justify-center gap-1 text-xs tabular-nums text-ink-body">
          <input
            type="text"
            inputMode="numeric"
            aria-label={t("ui.reader.pageNumber")}
            disabled={!pdf}
            value={pageInput}
            onChange={(event) => {
              if (/^\d*$/.test(event.target.value)) {
                setPageInput(event.target.value);
              }
            }}
            onFocus={(event) => event.currentTarget.select()}
            onBlur={commitPageInput}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === "Enter") {
                commitPageInput();
                event.currentTarget.select();
              } else if (event.key === "Escape") {
                setPageInput(String(currentPage));
                event.currentTarget.select();
              }
            }}
            className="h-6 w-9 rounded border border-surface-border bg-surface px-1 text-center text-xs tabular-nums text-ink-heading outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/20 disabled:opacity-50"
          />
          <span>/ {pdf?.numPages ?? "…"}</span>
        </div>
        <button
          type="button"
          aria-label={t("ui.reader.nextPage")}
          disabled={!pdf || currentPage >= pdf.numPages}
          onClick={() => navigateToPage(currentPage + 1)}
          className="rounded p-1.5 hover:bg-surface-muted disabled:opacity-40"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <span className="mx-1" />
        <button
          type="button"
          aria-label={t("ui.reader.zoomOut")}
          onClick={() => adjustCustomScale(-PDF_SCALE_STEP)}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={`${t("menu.zoom")} ${zoomTriggerLabel}`}
              title={`${t("menu.zoom")}: ${zoomTriggerLabel}`}
              className="inline-flex h-7 min-w-[76px] items-center justify-center gap-0.5 rounded-md px-1.5 text-xs tabular-nums text-ink-meta transition hover:bg-surface-muted hover:text-ink-heading data-[state=open]:bg-surface-muted data-[state=open]:text-ink-heading"
            >
              <span>{zoomTriggerLabel}</span>
              <ChevronDown className="h-3 w-3" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" sideOffset={6} className="min-w-32">
            <DropdownMenuRadioGroup
              value={zoomMenuValue}
              onValueChange={(value) => {
                if (value === "fit-width" || value === "fit-page") {
                  setZoomMode(value);
                  return;
                }
                if (value.startsWith("scale-")) {
                  const preset = Number(value.slice("scale-".length));
                  if (Number.isFinite(preset)) {
                    setCustomScale(clampPdfCustomScale(preset));
                    setZoomMode("custom");
                  }
                }
              }}
            >
              <DropdownMenuRadioItem value="fit-width" indicator="check">
                {t("ui.reader.fitWidth")}
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="fit-page" indicator="check">
                {t("ui.reader.fitPage")}
              </DropdownMenuRadioItem>
              <DropdownMenuSeparator />
              {PDF_SCALE_PRESETS.map((preset) => (
                <DropdownMenuRadioItem
                  key={preset}
                  value={`scale-${preset}`}
                  indicator="check"
                >
                  {Math.round(preset * 100)}%
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <button
          type="button"
          aria-label={t("ui.reader.zoomIn")}
          onClick={() => adjustCustomScale(PDF_SCALE_STEP)}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          aria-label={t("ui.reader.rotateClockwise")}
          onClick={() => setRotation((value) => (value + 90) % 360)}
          className="rounded p-1.5 hover:bg-surface-muted"
        >
          <RotateCw className="h-3.5 w-3.5" />
        </button>
      </div>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-4 overflow-auto p-4"
        onScroll={syncCurrentPageFromScroll}
        onKeyDown={(event) => {
          if (event.key === "PageDown" || event.key === "ArrowRight") {
            navigateToPage(currentPage + 1);
          } else if (
            event.key === "PageUp" ||
            event.key === "ArrowLeft"
          ) {
            navigateToPage(currentPage - 1);
          }
        }}
        tabIndex={0}
      >
        {!pdf ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-meta">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            {t("ui.reader.loadingPdf")}
          </div>
        ) : (
          pages.map((pageNumber) => (
            <PdfPage
              key={pageNumber}
              pdf={pdf}
              pageNumber={pageNumber}
              scale={scale}
              rotation={rotation}
              scrollRootRef={scrollRef}
              estimatedSize={estimatedPageSize}
              eager={pageNumber === currentPage || pageNumber === location?.page}
              location={
                pageNumber === location?.page ? location : undefined
              }
              onLocated={onLocated}
            />
          ))
        )}
      </div>
    </div>
  );
}
