import type { TextQuoteSelectorV1 } from "@valuz/shared";

export interface NormalizedText {
  text: string;
  /** Normalized character index -> original UTF-16 offset. */
  offsets: number[];
}

export interface TextQuoteMatch {
  start: number;
  end: number;
  score: number;
}

export function normalizeTextWithOffsets(value: string): NormalizedText {
  let text = "";
  const offsets: number[] = [];
  let pendingWhitespaceOffset: number | null = null;

  for (let offset = 0; offset < value.length; offset += 1) {
    const char = value[offset] ?? "";
    if (/\s/u.test(char)) {
      if (text && !text.endsWith(" ") && pendingWhitespaceOffset === null) {
        pendingWhitespaceOffset = offset;
      }
      continue;
    }
    if (pendingWhitespaceOffset !== null) {
      text += " ";
      offsets.push(pendingWhitespaceOffset);
      pendingWhitespaceOffset = null;
    }
    text += char;
    offsets.push(offset);
  }
  return { text, offsets };
}

export function selectBestNormalizedMatch(
  normalizedText: string,
  selector: TextQuoteSelectorV1,
): TextQuoteMatch | null {
  const exact = normalizeTextWithOffsets(selector.exact).text;
  if (!exact) return null;
  const prefix = selector.prefix
    ? normalizeTextWithOffsets(selector.prefix).text
    : "";
  const suffix = selector.suffix
    ? normalizeTextWithOffsets(selector.suffix).text
    : "";

  let best: TextQuoteMatch | null = null;
  let cursor = 0;
  while (cursor <= normalizedText.length - exact.length) {
    const start = normalizedText.indexOf(exact, cursor);
    if (start === -1) break;
    const end = start + exact.length;
    let score = 0;
    if (prefix) {
      const before = normalizedText.slice(0, start).trimEnd();
      if (before.endsWith(prefix)) score += prefix.length;
    }
    if (suffix) {
      const after = normalizedText.slice(end).trimStart();
      if (after.startsWith(suffix)) score += suffix.length;
    }
    if (!best || score > best.score) best = { start, end, score };
    cursor = start + Math.max(1, exact.length);
  }
  return best;
}

export function findBestTextQuote(
  value: string,
  selector: TextQuoteSelectorV1,
): TextQuoteMatch | null {
  const normalized = normalizeTextWithOffsets(value);
  const match = selectBestNormalizedMatch(normalized.text, selector);
  if (!match) return null;
  const firstOffset = normalized.offsets[match.start];
  const lastOffset = normalized.offsets[match.end - 1];
  if (firstOffset === undefined || lastOffset === undefined) return null;
  return {
    start: firstOffset,
    end: lastOffset + 1,
    score: match.score,
  };
}
