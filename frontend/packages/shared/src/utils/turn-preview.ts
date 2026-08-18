/**
 * Plain-text preview of a conversation turn's user message.
 *
 * The message index rail (and anything else that needs a one-line gist of
 * what the user asked) can't render markdown: ``MarkdownContent`` drags in
 * streamdown + katex + mermaid + citation rewriting, none of which belongs
 * in a hover card. This strips the markup down to readable prose instead.
 *
 * Kept next to ``segment-summary`` so every host (desktop / webui / tui)
 * shows the same preview for the same turn.
 */

/** Leading ``/skill`` invocations. Mirrors the backend's session-name
 * derivation (``_SKILL_PREFIX_RE`` in ``modules/sessions/run_orchestrator``)
 * so a skill-prefixed turn previews as its actual prompt, not the slash
 * command. A ``/`` inside the body is left alone. */
const SKILL_PREFIX_RE = /^\s*(?:\/[a-zA-Z0-9_-]+\s+)+/;

/** Fenced code blocks — dropped whole. A preview of ``def main():`` tells
 * the reader nothing about which turn this was. */
const FENCED_CODE_RE = /```[\s\S]*?(?:```|$)/g;

const IMAGE_RE = /!\[[^\]]*\]\([^)]*\)/g;
const LINK_RE = /\[([^\]]*)\]\([^)]*\)/g;
/** Leading block markers: heading hashes, blockquote carets, list bullets
 * and ordered-list numbers, and table pipes. */
const BLOCK_MARKER_RE = /^[ \t]*(?:[>#|]+|[-*+]|\d+[.)])[ \t]*/gm;
/** Emphasis / inline-code runs. Only the delimiters go; the text stays. */
const INLINE_MARK_RE = /[`*_~]+/g;

/**
 * Collapse a turn's raw ``userText`` into a single line of plain text,
 * truncated to ``maxChars`` (with an ellipsis when it had to cut).
 *
 * Returns ``""`` for an empty / whitespace-only / markup-only message —
 * callers decide what to show instead (an attachment-only turn has no
 * text at all).
 */
export function turnPreviewText(
  userText: string | undefined | null,
  maxChars = 140,
): string {
  if (!userText) return "";
  const stripped = userText
    .replace(SKILL_PREFIX_RE, "")
    .replace(FENCED_CODE_RE, " ")
    .replace(IMAGE_RE, " ")
    .replace(LINK_RE, "$1")
    .replace(BLOCK_MARKER_RE, "")
    .replace(INLINE_MARK_RE, "")
    .replace(/\s+/g, " ")
    .trim();
  if (stripped.length <= maxChars) return stripped;
  // Trim a dangling space so we never render "word …".
  return `${stripped.slice(0, maxChars).trimEnd()}…`;
}
