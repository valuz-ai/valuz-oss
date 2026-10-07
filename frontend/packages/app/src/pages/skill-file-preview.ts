/**
 * Whether a skill file's contents can be shown as text.
 *
 * The backend decodes tolerantly (utf-8 -> gb18030 -> replace) and always
 * hands back a string, so the question is never "is this ASCII" but "did the
 * decode produce text". The previous heuristic scored the share of characters
 * in the printable ASCII range, which called every Chinese SKILL.md a binary
 * -- a skill written in any non-Latin script simply could not be previewed.
 */

/** The Unicode replacement character, i.e. a byte the decode gave up on. */
const REPLACEMENT_CHARS = /\uFFFD/g;

/** Above this share of undecodable characters the file is bytes, not text. A
 *  stray replacement character in otherwise fine prose is not a binary. */
const UNDECODABLE_LIMIT = 0.1;

export function isBinaryContent(content: string): boolean {
  if (!content) return false;
  if (content.includes("\u0000")) return true;
  const replacements = content.match(REPLACEMENT_CHARS)?.length ?? 0;
  // Count C0 controls except prose whitespace (tab, LF and CR).
  let controls = 0;
  for (let index = 0; index < content.length; index++) {
    const code = content.charCodeAt(index);
    if (code < 32 && code !== 9 && code !== 10 && code !== 13) controls++;
  }
  return (replacements + controls) / content.length > UNDECODABLE_LIMIT;
}
