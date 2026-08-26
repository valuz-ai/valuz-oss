export type PdfZoomMode = "fit-width" | "fit-page" | "custom";

export interface PdfViewportSize {
  width: number;
  height: number;
}

export const PDF_MIN_CUSTOM_SCALE = 0.25;
export const PDF_MAX_SCALE = 4;

export function clampPdfCustomScale(value: number): number {
  return Math.min(Math.max(value, PDF_MIN_CUSTOM_SCALE), PDF_MAX_SCALE);
}

export function calculatePdfScale({
  mode,
  page,
  viewport,
  customScale,
}: {
  mode: PdfZoomMode;
  page: PdfViewportSize | null;
  viewport: PdfViewportSize;
  customScale: number;
}): number {
  if (
    mode === "custom" ||
    !page ||
    !Number.isFinite(page.width) ||
    !Number.isFinite(page.height) ||
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    page.width <= 0 ||
    page.height <= 0 ||
    viewport.width <= 0 ||
    viewport.height <= 0
  ) {
    return clampPdfCustomScale(customScale);
  }

  const widthScale = viewport.width / page.width;
  const fittedScale =
    mode === "fit-page"
      ? Math.min(widthScale, viewport.height / page.height)
      : widthScale;

  // Keep pathological documents from allocating an unbounded canvas while
  // still allowing narrow panes to fit pages below the manual zoom minimum.
  return Math.min(Math.max(fittedScale, 0.1), PDF_MAX_SCALE);
}
