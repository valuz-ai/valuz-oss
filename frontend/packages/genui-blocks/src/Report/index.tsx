"use client";

import { defineComponent } from "@openuidev/react-lang";

import { alignStyle, toneBorder, toneSurface, toneText } from "../lib/tone";
import { ReportPageFrame } from "./ReportPageFrame";
import { safeImageUrl } from "./safe-url";
import {
  ReportDocumentSchema,
  ReportFrontPageSchema,
  ReportHeadlineSchema,
  ReportImageSchema,
  ReportKeyStatementSchema,
  ReportPageSchema,
  ReportSectionSchema,
  ReportTableSchema,
  ReportTocPageSchema,
} from "./schema";

export {
  ReportCoverVariantSchema,
  ReportDocumentSchema,
  ReportFrontPageSchema,
  ReportHeadlineSchema,
  ReportImageSchema,
  ReportImageWidthSchema,
  ReportKeyStatementSchema,
  ReportPageSchema,
  ReportSectionSchema,
  ReportTableSchema,
  ReportTocItemSchema,
  ReportTocPageSchema,
} from "./schema";
export type { ReportCoverVariant, ReportImageWidth } from "./schema";
export { ReportPageFrame } from "./ReportPageFrame";
export type { ReportPageFrameProps } from "./ReportPageFrame";

/* ── Document ─────────────────────────────────────────────────────── */

export const ReportDocument = defineComponent({
  name: "ReportDocument",
  props: ReportDocumentSchema,
  description:
    "The outer shell of a multi-page report: a vertical stack of pages separated by page gaps, and the only correct root for long-form written deliverables (research notes, memos, briefings, anything the user may print or export). " +
    "children is an array of page blocks — usually ReportFrontPage first, then an optional ReportTocPage, then ReportPage for every content page. " +
    "title is an optional running name for the whole document; leave it out when the cover already carries the title. " +
    "Do not put prose or metrics directly in here — they belong inside a ReportPage.",
  component: ({ props, renderNode }) => (
    <div className="vgb-report-doc" data-slot="vgb-report-document">
      {props.title ? <div className="vgb-report-doc-title">{props.title}</div> : null}
      {renderNode(props.children)}
    </div>
  ),
});

/* ── Pages ────────────────────────────────────────────────────────── */

export const ReportPage = defineComponent({
  name: "ReportPage",
  props: ReportPageSchema,
  description:
    "One printed page inside a ReportDocument: a portrait A4 canvas with a padded text area, an optional running header and footer, and an optional page number. " +
    "children is the page's content — ReportSection for titled runs of prose, ReportHeadline, ReportKeyStatement, ReportTable, ReportImage, and the ordinary shared blocks (MiniCardBlock for a KPI strip, TextContent or MarkDownRenderer for body copy, charts for figures). There is no report-specific twin of those blocks; reuse them as they are. " +
    "header repeats the report or chapter name, footer the imprint line, pageNumber the folio. Break content across several ReportPage blocks rather than overfilling one.",
  component: ({ props, renderNode }) => (
    <ReportPageFrame
      header={props.header}
      footer={props.footer}
      pageNumber={props.pageNumber}
      slot="vgb-report-page"
    >
      {renderNode(props.children)}
    </ReportPageFrame>
  ),
});

export const ReportFrontPage = defineComponent({
  name: "ReportFrontPage",
  props: ReportFrontPageSchema,
  description:
    "The cover page of a ReportDocument. Always the first child. title is the report's name, subtitle a one-line framing, author and date the imprint. " +
    "variant picks the treatment: standard (title beside or above a picture), minimal (typography only — use it when there is no good image), dramatic (the picture runs full-bleed behind the title). " +
    "imagePosition applies to standard only and takes top, right or bottom. imageUrl must be a real http(s) URL; if you have no image, use variant=minimal instead of inventing one.",
  component: ({ props }) => {
    const variant = props.variant ?? "standard";
    const source = safeImageUrl(props.imageUrl);
    const alt = props.imageAlt ?? "";
    const position = props.imagePosition ?? "top";

    const text = (
      <div className="vgb-report-cover-text">
        <h1 className="vgb-report-cover-title">{props.title}</h1>
        {props.subtitle ? <p className="vgb-report-cover-subtitle">{props.subtitle}</p> : null}
        {props.author || props.date ? (
          <p className="vgb-report-cover-meta">
            {props.author ? <span>{props.author}</span> : null}
            {props.author && props.date ? <span aria-hidden="true">·</span> : null}
            {props.date ? <span>{props.date}</span> : null}
          </p>
        ) : null}
      </div>
    );

    if (variant === "dramatic") {
      return (
        <ReportPageFrame
          slot="vgb-report-front-page"
          className="vgb-report-cover vgb-report-cover-dramatic"
          bodyClassName="vgb-report-cover-body"
          bleed={source ? <img className="vgb-report-cover-bleed" src={source} alt={alt} /> : null}
        >
          {text}
        </ReportPageFrame>
      );
    }

    if (variant === "minimal") {
      return (
        <ReportPageFrame
          slot="vgb-report-front-page"
          className="vgb-report-cover vgb-report-cover-minimal"
          bodyClassName="vgb-report-cover-body"
        >
          {text}
          <div className="vgb-report-cover-rule" />
        </ReportPageFrame>
      );
    }

    return (
      <ReportPageFrame
        slot="vgb-report-front-page"
        className="vgb-report-cover vgb-report-cover-standard"
        bodyClassName="vgb-report-cover-body"
      >
        <div className="vgb-report-cover-layout" data-position={position}>
          {text}
          {source ? (
            <div className="vgb-report-cover-media">
              <img src={source} alt={alt} />
            </div>
          ) : null}
        </div>
      </ReportPageFrame>
    );
  },
});

export const ReportTocPage = defineComponent({
  name: "ReportTocPage",
  props: ReportTocPageSchema,
  description:
    "A table-of-contents page, placed straight after ReportFrontPage in reports long enough to need one (roughly five pages or more). " +
    "items is the list of entries: label is the section title exactly as it appears on its page, page the folio it starts on. Leader dots are drawn for you. " +
    "title defaults to \"Contents\" — override it only to match the document's language.",
  component: ({ props }) => (
    <ReportPageFrame slot="vgb-report-toc-page" className="vgb-report-toc">
      <h2 className="vgb-report-toc-title">{props.title ?? "Contents"}</h2>
      <ol className="vgb-report-toc-list">
        {props.items.map((item, index) => (
          <li className="vgb-report-toc-row" key={`${item.label}-${index}`}>
            <span className="vgb-report-toc-label">{item.label}</span>
            <span className="vgb-report-toc-leader" aria-hidden="true" />
            {typeof item.page === "number" ? (
              <span className="vgb-report-toc-page">{item.page}</span>
            ) : null}
          </li>
        ))}
      </ol>
    </ReportPageFrame>
  ),
});

/* ── Page furniture ───────────────────────────────────────────────── */

export const ReportSection = defineComponent({
  name: "ReportSection",
  props: ReportSectionSchema,
  description:
    "A titled section inside a ReportPage — the unit a table of contents points at, and the unit that is kept whole when the document is printed. " +
    "title is the section heading, eyebrow an optional short label above it (a number like \"02\", a phase, a category). " +
    "children is the section's content: prose, ReportKeyStatement, ReportTable, ReportImage, or shared blocks such as MiniCardBlock.",
  component: ({ props, renderNode }) => (
    <section className="vgb-report-section" data-slot="vgb-report-section">
      {props.eyebrow ? <div className="vgb-eyebrow">{props.eyebrow}</div> : null}
      <h2 className="vgb-report-section-title">{props.title}</h2>
      <div className="vgb-report-section-body">{renderNode(props.children)}</div>
    </section>
  ),
});

export const ReportHeadline = defineComponent({
  name: "ReportHeadline",
  props: ReportHeadlineSchema,
  description:
    "A standalone headline set at print scale, for opening a page or a major turn in the argument. text is the headline itself — keep it to one line — and kicker an optional short line above it. " +
    "Use ReportSection instead when the heading owns a body of content; use this only when the headline stands alone.",
  component: ({ props }) => (
    <div className="vgb-report-headline" data-slot="vgb-report-headline">
      {props.kicker ? <div className="vgb-eyebrow">{props.kicker}</div> : null}
      <h2 className="vgb-report-headline-text">{props.text}</h2>
    </div>
  ),
});

export const ReportKeyStatement = defineComponent({
  name: "ReportKeyStatement",
  props: ReportKeyStatementSchema,
  description:
    "One sentence lifted out of the body text and set apart — the finding a reader should carry away from the page. Use at most one or two per page; more and none of them read as important. " +
    "text is the statement, attribution the optional source or speaker. tone colours the rule beside it: use warning or danger for a risk, success for a confirmed result, and leave it unset otherwise.",
  component: ({ props }) => (
    <blockquote
      className="vgb-report-statement"
      data-slot="vgb-report-key-statement"
      style={{ borderInlineStartColor: toneBorder(props.tone), backgroundColor: toneSurface(props.tone) }}
    >
      <p className="vgb-report-statement-text" style={{ color: toneText(props.tone) }}>
        {props.text}
      </p>
      {props.attribution ? (
        <footer className="vgb-report-statement-attribution">{props.attribution}</footer>
      ) : null}
    </blockquote>
  ),
});

/* ── Page content that only exists in a report ────────────────────── */

export const ReportTable = defineComponent({
  name: "ReportTable",
  props: ReportTableSchema,
  description:
    "A table typeset for a report page: column labels over a rule, no zebra stripes, no card around it. Reach for it for the data exhibits inside a ReportPage. " +
    "columns is the header row, rows an array of rows, each an array of already-formatted strings in the same order (include units and signs: \"$4.2M\", \"-3.1%\"). " +
    "align is an optional per-column array of left/center/right — set right for numeric columns. caption is a short note printed under the table. Keep it to about six columns; wide tables scroll inside the page rather than widening it.",
  component: ({ props }) => {
    const width = props.columns.length;
    return (
      <figure className="vgb-report-table-figure" data-slot="vgb-report-table">
        <div className="vgb-scroll-x">
          <table className="vgb-report-table">
            <thead>
              <tr>
                {props.columns.map((column, index) => (
                  <th key={`${column}-${index}`} scope="col" style={alignStyle(props.align?.[index])}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {props.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {Array.from({ length: width }, (_unused, cellIndex) => (
                    <td key={cellIndex} style={alignStyle(props.align?.[cellIndex])}>
                      {row[cellIndex] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {props.caption ? <figcaption className="vgb-report-caption">{props.caption}</figcaption> : null}
      </figure>
    );
  },
});

export const ReportImage = defineComponent({
  name: "ReportImage",
  props: ReportImageSchema,
  description:
    "A figure inside a ReportPage: the image plus its caption, kept together when the page breaks. " +
    "url must be a real http(s) URL you were given — never invent one; anything else renders as a caption-only placeholder. alt describes the picture for a reader who cannot see it, caption is the printed figure note. " +
    "width is full (the whole text column, the default) or half (a column-width figure prose can sit beside).",
  component: ({ props }) => {
    const source = safeImageUrl(props.url);
    const width = props.width ?? "full";
    return (
      <figure
        className={`vgb-report-figure vgb-report-figure-${width}`}
        data-slot="vgb-report-image"
      >
        {source ? (
          <img className="vgb-report-figure-image" src={source} alt={props.alt ?? ""} />
        ) : (
          <div className="vgb-report-figure-placeholder">{props.alt ?? props.caption ?? ""}</div>
        )}
        {props.caption ? <figcaption className="vgb-report-caption">{props.caption}</figcaption> : null}
      </figure>
    );
  },
});
