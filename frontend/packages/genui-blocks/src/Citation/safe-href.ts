/**
 * URL sanitisation for model-authored links.
 *
 * Every URL in this family arrives inside LLM output, which means it is
 * attacker-influenced whenever the model has read attacker-controlled text
 * (a fetched page, a pasted document, a tool result). Putting such a string
 * straight into `href` or `src` is the classic `javascript:` /
 * `data:text/html` injection: the click happens in the host's origin, with the
 * host's cookies and storage.
 *
 * The rule here is an allow-list, not a deny-list. Anything that is not an
 * absolute `http:` or `https:` URL returns `undefined`, and the caller then
 * renders plain text instead of a link. Deny-lists lose to
 * `java\tscript:`, `JavaScript:`, `%6a%61vascript:` and friends; the URL
 * parser normalises all of those before we ever see the protocol.
 */

const SAFE_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * Returns a normalised absolute http(s) URL, or `undefined` for anything else
 * — relative paths, `javascript:`, `data:`, `blob:`, `file:`, `mailto:`,
 * non-strings and unparseable junk.
 *
 * The value returned is `URL.href`, i.e. the parser's own serialisation, so
 * what reaches the DOM is exactly what was validated. Returning the raw input
 * would reopen the hole for inputs whose parse differs from their spelling.
 */
export function safeHref(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const trimmed = raw.trim();
  if (trimmed === "") return undefined;

  let parsed: URL;
  try {
    // No base URL on purpose: a relative reference must not resolve against
    // the host document's origin, it must simply fail.
    parsed = new URL(trimmed);
  } catch {
    return undefined;
  }

  if (!SAFE_PROTOCOLS.has(parsed.protocol)) return undefined;
  return parsed.href;
}

/**
 * The bare hostname of a safe URL (`www.` stripped), for use as the site
 * label when a source carries no `siteName`. Returns `undefined` whenever
 * `safeHref` would.
 */
export function hostLabel(raw: unknown): string | undefined {
  const safe = safeHref(raw);
  if (!safe) return undefined;
  const host = new URL(safe).hostname.replace(/^www\./i, "");
  return host === "" ? undefined : host;
}

/**
 * First character of the first non-empty candidate, upper-cased — the letter
 * avatar shown when a source has no favicon. Purely decorative: callers must
 * render it inside an `aria-hidden` element so it is never announced.
 */
export function initialFor(...candidates: (string | undefined)[]): string {
  for (const candidate of candidates) {
    const trimmed = candidate?.trim();
    if (trimmed) return trimmed.slice(0, 1).toUpperCase();
  }
  return "#";
}
