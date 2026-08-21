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
