import { useCallback, useEffect, useRef, useState } from "react";
import type { TextQuoteSelectorV1 } from "@valuz/shared";

import type { DocumentLocation } from "./document-reader.types";
import {
  normalizeTextWithOffsets,
  selectBestNormalizedMatch,
} from "./text-quote";

const STYLE = `
  <style>
    :root { color-scheme: light; --citation-warning: #ef8b0c; }
    html { scroll-behavior: smooth; scrollbar-width: thin; scrollbar-color: rgba(137, 143, 156, 0.12) transparent; }
    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(137, 143, 156, 0.12); border-radius: 9999px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(137, 143, 156, 0.28); }
    body { box-sizing: border-box; position: relative; margin: 0 auto; max-width: 860px; padding: 24px; color: rgb(36 39 45); font: 14px/1.75 system-ui, sans-serif; }
    *, *::before, *::after { box-sizing: inherit; }
    img, video, canvas, svg, table { max-width: 100%; }
    [data-citation-highlight] { position: absolute; z-index: 20; pointer-events: none; background: color-mix(in oklab, var(--citation-warning) 35%, transparent); border-radius: 4px; box-shadow: 0 0 0 1px color-mix(in oklab, var(--citation-warning) 70%, transparent); mix-blend-mode: multiply; }
    [data-citation-block-highlight] { background: color-mix(in oklab, var(--citation-warning) 35%, transparent); border-radius: 4px; box-shadow: 0 0 0 1px color-mix(in oklab, var(--citation-warning) 70%, transparent); mix-blend-mode: multiply; }
    @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
  </style>
`;

function srcDoc(html: string): string {
  const csp =
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data: blob: https: http:; media-src blob: https: http:; style-src 'unsafe-inline'\">";
  if (/<head[\s>]/i.test(html)) {
    return html.replace(/<head([^>]*)>/i, `<head$1>${csp}${STYLE}`);
  }
  if (/<html[\s>]/i.test(html)) {
    return html.replace(
      /<html([^>]*)>/i,
      `<html$1><head>${csp}${STYLE}</head>`,
    );
  }
  return `<!doctype html><html><head>${csp}${STYLE}</head><body>${html}</body></html>`;
}

function syncCitationHighlightColor(doc: Document): void {
  const warning = window
    .getComputedStyle(document.documentElement)
    .getPropertyValue("--warning")
    .trim();
  if (warning) {
    doc.documentElement.style.setProperty("--citation-warning", warning);
  }
}

interface TextPosition {
  node: Text;
  offset: number;
}

const HTML_QUOTE_FALLBACK_MIN_CHARS = 32;
const HTML_QUOTE_FALLBACK_WINDOW_CHARS = 160;

function collectNormalizedText(root: Node): {
  text: string;
  positions: TextPosition[];
} {
  const doc = root.ownerDocument ?? (root as Document);
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (
        !node.textContent ||
        parent?.closest("script,style,noscript,[data-citation-highlight]")
      ) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let text = "";
  const positions: TextPosition[] = [];
  let pendingWhitespace: TextPosition | null = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.textContent ?? "";
    for (let offset = 0; offset < value.length; offset += 1) {
      const char = value[offset] ?? "";
      if (/\s/u.test(char)) {
        if (text && !text.endsWith(" ") && !pendingWhitespace) {
          pendingWhitespace = { node: node as Text, offset };
        }
        continue;
      }
      if (pendingWhitespace) {
        text += " ";
        positions.push(pendingWhitespace);
        pendingWhitespace = null;
      }
      text += char;
      positions.push({ node: node as Text, offset });
    }
  }
  return { text, positions };
}

function rangeFromPositions(
  root: Node,
  positions: TextPosition[],
  start: number,
  end: number,
): Range | null {
  const first = positions[start];
  const last = positions[end - 1];
  if (!first || !last) return null;
  const range = (root.ownerDocument ?? (root as Document)).createRange();
  range.setStart(first.node, first.offset);
  range.setEnd(last.node, last.offset + 1);
  return range;
}

function withoutWhitespace(value?: string): string {
  return value
    ? normalizeTextWithOffsets(value).text.replace(/\s+/gu, "")
    : "";
}

function compactTextWithPositions(
  text: string,
  positions: TextPosition[],
): { text: string; positions: TextPosition[] } {
  let compactText = "";
  const compactPositions: TextPosition[] = [];
  for (let index = 0; index < text.length; index += 1) {
    if (/\s/u.test(text[index] ?? "")) continue;
    compactText += text[index];
    const position = positions[index];
    if (position) compactPositions.push(position);
  }
  return { text: compactText, positions: compactPositions };
}

/**
 * Report/PDF extractors can insert visual line-wrap whitespace in the middle
 * of CJK words while the HTML rendition keeps the same prose continuous.
 * Prefer the strict selector first; this fallback removes whitespace only for
 * long quotes and, if typography differs later in the quote, accepts one
 * unique long window. Short/ambiguous snippets never use the relaxed path.
 */
function findRelaxedHtmlQuoteRange(
  root: Node,
  text: string,
  positions: TextPosition[],
  selector: TextQuoteSelectorV1,
): Range | null {
  const exact = withoutWhitespace(selector.exact);
  if (exact.length < HTML_QUOTE_FALLBACK_MIN_CHARS) return null;
  const compact = compactTextWithPositions(text, positions);
  const fullMatch = selectBestNormalizedMatch(compact.text, {
    exact,
    ...(selector.prefix
      ? { prefix: withoutWhitespace(selector.prefix) }
      : {}),
    ...(selector.suffix
      ? { suffix: withoutWhitespace(selector.suffix) }
      : {}),
  });
  if (fullMatch) {
    return rangeFromPositions(
      root,
      compact.positions,
      fullMatch.start,
      fullMatch.end,
    );
  }

  const windowLength = Math.min(
    HTML_QUOTE_FALLBACK_WINDOW_CHARS,
    Math.max(
      HTML_QUOTE_FALLBACK_MIN_CHARS,
      Math.floor(exact.length * 0.6),
    ),
  );
  const lastStart = exact.length - windowLength;
  const candidateStarts = Array.from(
    new Set([0, Math.floor(lastStart / 2), lastStart]),
  );
  for (const candidateStart of candidateStarts) {
    const needle = exact.slice(candidateStart, candidateStart + windowLength);
    const start = compact.text.indexOf(needle);
    if (
      start < 0 ||
      compact.text.indexOf(needle, start + 1) !== -1
    ) {
      continue;
    }
    return rangeFromPositions(
      root,
      compact.positions,
      start,
      start + needle.length,
    );
  }
  return null;
}

export function findHtmlQuoteRange(
  root: Node,
  selector: TextQuoteSelectorV1,
): Range | null {
  const { text, positions } = collectNormalizedText(root);
  const match = selectBestNormalizedMatch(text, selector);
  if (match) {
    return rangeFromPositions(root, positions, match.start, match.end);
  }
  return findRelaxedHtmlQuoteRange(root, text, positions, selector);
}

function clearHighlights(doc: Document): void {
  for (const highlight of Array.from(
    doc.querySelectorAll<HTMLElement>("[data-citation-highlight]"),
  )) {
    if (highlight.tagName === "MARK") {
      const parent = highlight.parentNode;
      highlight.replaceWith(...Array.from(highlight.childNodes));
      parent?.normalize();
    } else {
      highlight.remove();
    }
  }
  for (const block of Array.from(
    doc.querySelectorAll<HTMLElement>("[data-citation-block-highlight]"),
  )) {
    block.removeAttribute("data-citation-block-highlight");
  }
}

function rangeBlock(range: Range): HTMLElement | null {
  const start =
    range.startContainer.nodeType === Node.ELEMENT_NODE
      ? (range.startContainer as Element)
      : range.startContainer.parentElement;
  return (
    start?.closest<HTMLElement>(
      "p,li,blockquote,pre,table,figure,section,article,div",
    ) ?? null
  );
}

function highlightRangeBox(range: Range): HTMLElement | null {
  const doc = range.startContainer.ownerDocument;
  if (!doc || typeof range.getBoundingClientRect !== "function") return null;
  const rect = range.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const bodyRect = doc.body.getBoundingClientRect();
  const horizontalPadding = 4;
  const verticalPadding = 3;
  const highlight = doc.createElement("span");
  highlight.setAttribute("data-citation-highlight", "range");
  highlight.setAttribute("aria-hidden", "true");
  Object.assign(highlight.style, {
    left: `${rect.left - bodyRect.left - horizontalPadding}px`,
    top: `${rect.top - bodyRect.top - verticalPadding}px`,
    width: `${rect.width + horizontalPadding * 2}px`,
    height: `${rect.height + verticalPadding * 2}px`,
  });
  doc.body.appendChild(highlight);
  return highlight;
}

export function highlightHtmlDocument(
  doc: Document,
  location?: DocumentLocation,
): {
  status: "idle" | "located-exact" | "located-fallback" | "not-found";
  target: HTMLElement | null;
} {
  clearHighlights(doc);
  if (!location) return { status: "idle", target: null };
  let anchor: HTMLElement | null = null;
  if (location.chunkId) {
    // Do not interpolate an upstream chunk id into selector syntax.  Besides
    // making quotes/newlines awkward to escape correctly, a malformed trusted
    // locator could otherwise make querySelector throw and take down the
    // whole reader. Attribute equality is both exact and syntax-independent.
    anchor =
      Array.from(
        doc.querySelectorAll<HTMLElement>("[data-chunk-id]"),
      ).find(
        (element) =>
          element.getAttribute("data-chunk-id") === location.chunkId,
      ) ?? null;
  }
  if (!anchor && location.elementId) {
    anchor = doc.getElementById(location.elementId);
  }
  if (!anchor && location.cssSelector) {
    try {
      anchor = doc.querySelector(location.cssSelector);
    } catch {
      anchor = null;
    }
  }

  if (location.quote) {
    const exactRange = findHtmlQuoteRange(anchor ?? doc.body, location.quote);
    if (exactRange) {
      const highlight = highlightRangeBox(exactRange);
      const fallbackBlock = highlight ? null : anchor ?? rangeBlock(exactRange);
      fallbackBlock?.setAttribute("data-citation-block-highlight", "true");
      return {
        status: anchor ? "located-exact" : "located-fallback",
        target: highlight ?? fallbackBlock,
      };
    }
    if (anchor) {
      anchor.setAttribute("data-citation-block-highlight", "true");
      return { status: "located-exact", target: anchor };
    }
    return { status: "not-found", target: null };
  }
  if (anchor) {
    anchor.setAttribute("data-citation-block-highlight", "true");
    return { status: "located-exact", target: anchor };
  }
  return { status: "not-found", target: null };
}

export function HtmlDocumentRenderer({
  html,
  title,
  location,
}: {
  html: string;
  title: string;
  location?: DocumentLocation;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [status, setStatus] = useState("idle");

  const locate = useCallback(() => {
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    syncCitationHighlightColor(doc);
    const result = highlightHtmlDocument(doc, location);
    setStatus(result.status);
    if (result.target) {
      const reduced = window.matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      result.target.scrollIntoView({
        block: "center",
        behavior: reduced ? "auto" : "smooth",
      });
    }
  }, [location]);

  useEffect(() => {
    locate();
  }, [html, locate]);

  useEffect(
    () => () => {
      const doc = iframeRef.current?.contentDocument;
      if (doc) clearHighlights(doc);
    },
    [],
  );

  return (
    <iframe
      ref={iframeRef}
      srcDoc={srcDoc(html)}
      title={title}
      sandbox="allow-same-origin"
      data-locate-status={status}
      onLoad={locate}
      className="h-full w-full border-0 bg-white"
    />
  );
}
