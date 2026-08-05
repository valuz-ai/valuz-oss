"use client";

import type { ReactNode } from "react";

/**
 * The paper. Every page-shaped block in this family renders through this one
 * frame — `ReportPage`, `ReportFrontPage` and `ReportTocPage` differ in what
 * they put *on* the page, never in the page itself. That is what keeps the A4
 * proportion, the padded content area, the running header/footer and the print
 * rules in a single place.
 *
 * A plain React component on purpose: it is composition machinery, not a block
 * the model can emit.
 */
export interface ReportPageFrameProps {
  /** Running header, repeated on every content page (report or chapter title). */
  header?: string;
  /** Running footer — the imprint line, not the page number. */
  footer?: string;
  /** Folio, rendered at the outer edge of the footer rule. */
  pageNumber?: number;
  /** Full-bleed layer painted behind the padded content area (cover artwork). */
  bleed?: ReactNode;
  /** `data-slot` value, so each block keeps its own stable test hook. */
  slot?: string;
  /** Extra class on the page root — the per-block modifier. */
  className?: string;
  /** Extra class on the padded content area. */
  bodyClassName?: string;
  children?: ReactNode;
}

function classes(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function ReportPageFrame({
  header,
  footer,
  pageNumber,
  bleed,
  slot = "vgb-report-page",
  className,
  bodyClassName,
  children,
}: ReportPageFrameProps) {
  const hasFooter = Boolean(footer) || typeof pageNumber === "number";

  return (
    <section className={classes("vgb-report-page", className)} data-slot={slot}>
      {bleed}
      {header ? <div className="vgb-report-running vgb-report-running-head">{header}</div> : null}
      <div className={classes("vgb-report-page-body", bodyClassName)}>{children}</div>
      {hasFooter ? (
        <div className="vgb-report-running vgb-report-running-foot">
          <span className="vgb-report-running-text">{footer}</span>
          {typeof pageNumber === "number" ? (
            <span className="vgb-report-folio">{pageNumber}</span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
