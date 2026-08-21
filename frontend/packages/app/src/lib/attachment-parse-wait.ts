import { sessionsApi } from "@valuz/core";

/** How long a turn waits for its attachments to finish parsing.
 *
 *  Generous because the point is to wait, and bounded because a parse that
 *  never settles must not swallow the message: past this the turn goes out
 *  with the raw ``source_path``, which is what it did anyway before this wait
 *  existed. */
export const ATTACHMENT_PARSE_WAIT_MS = 60_000;
export const ATTACHMENT_PARSE_POLL_MS = 1_000;

/**
 * Resolve once no attachment on ``sessionId`` is still parsing.
 *
 * **Who waits, and where.** A turn sent mid-parse still works — the kernel
 * falls back to the raw ``source_path`` — but the agent loses the markdown
 * extract, which for a scanned PDF is the whole reason the file was attached.
 * That is what the "submit anyway?" confirm bought, by stopping the *person*.
 * Stopping the person is the expensive way to buy it: they are looking at a
 * composer with nothing happening, and the only answer they can give is "yes".
 * Holding the turn buys the same thing while they read their own message on
 * the conversation page.
 *
 * Polls the server rather than reading the composer's hook: this runs after
 * the sending page has navigated away, so that hook's own poll is already torn
 * down and its last local snapshot is exactly the stale one.
 *
 * Never rejects. A failed read is "cannot tell", and the loop keeps going to
 * the deadline — a degraded turn is worth strictly more than a lost message.
 */
export async function waitForAttachmentsToSettle(
  sessionId: string,
  {
    timeoutMs = ATTACHMENT_PARSE_WAIT_MS,
    pollMs = ATTACHMENT_PARSE_POLL_MS,
  } = {},
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const { items } = await sessionsApi.listAttachments(sessionId);
      // ``consumed_at`` rows belong to turns already sent; a new turn must not
      // wait on someone else's history.
      const parsing = items.some(
        (a) => !a.consumed_at && a.parse_status === "parsing",
      );
      if (!parsing) return;
    } catch {
      /* cannot tell — fall through to the deadline check and retry */
    }
    if (Date.now() >= deadline) return;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}
