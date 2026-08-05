"use client";

import { defineComponent } from "@openuidev/react-lang";

import { hostLabel, initialFor, safeHref } from "./safe-href";
import {
  CitationSchema,
  CondensedSourcesSchema,
  SourceItemSchema,
  SourceListSchema,
} from "./schema";

export {
  CitationSchema,
  CondensedSourcesSchema,
  SourceItemSchema,
  SourceListSchema,
} from "./schema";

export const Citation = defineComponent({
  name: "Citation",
  props: CitationSchema,
  description:
    "Inline superscript reference marker. Place it in flowing prose immediately after the clause it supports — never on its own line, never as a list. " +
    "index is the source's number and must match the SourceItem carrying the same index; title is the source's headline and url its address (http/https only — anything else renders as plain text, not a link). " +
    "Pair every Citation with a SourceList or CondensedSources further down the answer, so the reader can resolve the number.",
  component: ({ props }) => {
    const href = safeHref(props.url);
    const host = hostLabel(props.url);
    const label = props.title ?? host;
    const name = label ? `Source ${props.index}: ${label}` : `Source ${props.index}`;

    /*
     * Hover card. Absolutely positioned, so it never enters the line box, and
     * driven purely by `:hover` / `:focus-visible` in CSS — a block must
     * render from props alone, which rules out open/closed state. The `title`
     * attribute below is the belt to this braces: if a host ancestor clips
     * overflow, the native tooltip still resolves the reference.
     */
    const card = label ? (
      <span className="vgb-cite-card" aria-hidden="true">
        <span className="vgb-cite-card-title">{label}</span>
        {props.title && host ? <span className="vgb-cite-card-host">{host}</span> : null}
      </span>
    ) : null;

    if (href) {
      return (
        <a
          className="vgb-cite"
          data-slot="vgb-citation"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={name}
          aria-label={name}
        >
          <span className="vgb-cite-index">{props.index}</span>
          {card}
        </a>
      );
    }

    /*
     * No usable URL: not a link, so `aria-label` on a bare span would be
     * ignored by most screen readers. The accessible name comes from real
     * text content instead, with the digit hidden from the accessibility tree
     * so the marker is announced once, as prose.
     */
    return (
      <span className="vgb-cite" data-slot="vgb-citation" title={name}>
        <span className="vgb-cite-index" aria-hidden="true">
          {props.index}
        </span>
        <span className="vgb-cite-sr">{name}</span>
        {card}
      </span>
    );
  },
});

export const SourceItem = defineComponent({
  name: "SourceItem",
  props: SourceItemSchema,
  description:
    "One cited source: favicon, number, headline, site, and an optional quoted passage. " +
    "index must match the Citation markers that point at it; title is the document or page headline; url its address; snippet a short verbatim extract (one or two sentences) that shows why the source was used; siteName the publisher (\"Reuters\", \"SEC EDGAR\") and faviconUrl its icon — omit faviconUrl and a letter avatar is drawn from siteName. " +
    "Always place SourceItems inside SourceList or CondensedSources.",
  component: ({ props }) => {
    const href = safeHref(props.url);
    const favicon = safeHref(props.faviconUrl);
    const host = hostLabel(props.url);
    const site = props.siteName ?? host;

    return (
      <li className="vgb-source" data-slot="vgb-source-item">
        <span className="vgb-source-avatar">
          {favicon ? (
            /*
             * Decorative: the headline beside it already names the source, so
             * an empty alt keeps the row from being read twice. `no-referrer`
             * stops the host's URL leaking to the icon's origin.
             */
            <img
              className="vgb-source-favicon"
              src={favicon}
              alt=""
              width={16}
              height={16}
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
            />
          ) : (
            <span className="vgb-source-initial" aria-hidden="true">
              {initialFor(props.siteName, host, props.title)}
            </span>
          )}
        </span>

        <span className="vgb-source-main">
          <span className="vgb-source-head">
            <span className="vgb-source-index">{props.index}</span>
            {href ? (
              <a
                className="vgb-source-title"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {props.title}
              </a>
            ) : (
              <span className="vgb-source-title">{props.title}</span>
            )}
          </span>
          {site ? <span className="vgb-source-site">{site}</span> : null}
          {props.snippet ? <span className="vgb-source-snippet">{props.snippet}</span> : null}
        </span>
      </li>
    );
  },
});

export const SourceList = defineComponent({
  name: "SourceList",
  props: SourceListSchema,
  description:
    "The expanded, always-visible list of sources behind an answer. children is an array of SourceItem, in the same order as their index. " +
    "Use this when the sources are part of the argument and should be read; when they are supporting material the reader may want to skip, use CondensedSources instead.",
  component: ({ props, renderNode }) => (
    <ol className="vgb-source-list" data-slot="vgb-source-list">
      {renderNode(props.children)}
    </ol>
  ),
});

export const CondensedSources = defineComponent({
  name: "CondensedSources",
  props: CondensedSourcesSchema,
  description:
    "Collapsed source list: a single clickable line (\"12 sources\") that expands to the full set. Reach for this at the end of a researched answer, so the citations are available without burying the conclusion. " +
    "children is an array of SourceItem; the count is taken from them. label overrides the summary line — set it only to say something the count cannot (\"12 sources · 3 filings\"), and localise it to the language of the answer.",
  component: ({ props, renderNode }) => {
    const count = props.children.length;
    const label = props.label ?? `${count} ${count === 1 ? "source" : "sources"}`;
    return (
      /*
       * <details> rather than a state hook: it carries its own open/closed
       * state, its own keyboard behaviour (Enter/Space on the summary), and
       * its own accessibility semantics. Nothing here overrides them.
       */
      <details className="vgb-sources" data-slot="vgb-condensed-sources">
        <summary className="vgb-sources-summary">
          <span className="vgb-sources-summary-text">{label}</span>
        </summary>
        <ol className="vgb-source-list vgb-sources-body">{renderNode(props.children)}</ol>
      </details>
    );
  },
});
