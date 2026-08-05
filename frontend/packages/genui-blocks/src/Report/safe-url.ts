/**
 * Image URLs in a report come out of model output, so they are untrusted input
 * that this package hands straight to the DOM. Only `http:` and `https:` may
 * ever reach an `src`: `javascript:` executes, `data:` smuggles arbitrary
 * markup past the host CSP, and `file:`/`blob:` read from the machine the
 * desktop app runs on.
 *
 * Everything else resolves to `undefined`, and the blocks render a caption-only
 * placeholder instead of a broken image.
 */

const HTTP_URL = /^https?:\/\/[^/]/i;

/**
 * Drop ASCII control characters and spaces, exactly as a browser does while
 * parsing a URL. Without this, `java\nscript:alert(1)` survives a prefix test
 * here and is then reassembled into a working `javascript:` URL by the DOM.
 * Written as a loop rather than a regex: a character class holding literal
 * control characters is itself a lint error, and escaping around that reads
 * worse than this.
 */
function stripUrlNoise(value: string): string {
  let out = "";
  for (const char of value) {
    const code = char.codePointAt(0) ?? 0;
    if (code <= 0x20 || code === 0x7f) continue;
    out += char;
  }
  return out;
}

export function safeImageUrl(url: string | undefined): string | undefined {
  if (typeof url !== "string") return undefined;

  const cleaned = stripUrlNoise(url);
  if (!HTTP_URL.test(cleaned)) return undefined;

  return cleaned;
}
