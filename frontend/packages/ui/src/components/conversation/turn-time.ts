/**
 * Turn timestamp formatting, shared by the transcript's per-message time
 * label (``ConversationTurnList``) and the message index rail's hover
 * card. Extracted verbatim from ``ConversationTurnList`` — the two must
 * read the same or the rail would date a turn differently from the turn
 * itself.
 */

/** ``HH:MM`` for a turn sent today, ``MM-DD HH:MM`` otherwise. Empty
 * string for a missing or unparseable timestamp. */
export const formatTurnTime = (ms: number | undefined): string => {
  if (!ms) return "";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return `${hh}:${mi}`;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
};

/**
 * The full moment, down to the second — ``YYYY-MM-DD HH:MM:SS``.
 *
 * The compact form above stops at the minute, which is enough to place a
 * message but not enough to tell a turn's finish time from the question that
 * started it: a reply that lands in twenty seconds prints the same ``HH:MM``
 * as the prompt above it. This is what the finish-time label says on hover.
 */
export const formatTurnTimeExact = (ms: number | undefined): string => {
  if (!ms) return "";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  );
};
