/**
 * Whether the continuation of a send may still write PAGE state.
 *
 * The conversation page is ONE component instance for every
 * ``/conversation/*`` id (``layout/outlet-key.ts`` pins the outlet key), so a
 * send's awaits — ``sessionsApi.create`` for a draft, ``sendMessage`` for the
 * turn — resolve into whatever conversation the page shows by then. On a
 * cloud backend those round-trips take seconds (``send_message`` finalizes
 * the session through the sandbox kernel before it returns), which is plenty
 * of time to open another conversation from the sidebar.
 *
 * A continuation owns the page when either:
 *   - the page shows the session the send belongs to — a follow-up on an open
 *     session, or a draft that has been promoted to the id it minted; or
 *   - the page has not moved since the send started — the draft the message
 *     was typed into is still open (the promote navigation itself is what
 *     moves it, and it lands on the case above).
 *
 * Otherwise the user opened another conversation meanwhile. The send still
 * goes out and the sidebar still learns about the session, but nothing
 * page-scoped may follow it: not the selection (the header, status pill and
 * live stream all hang off it), not the transcript, not the attachments
 * panel, not the busy flag. Adopting the session anyway used to flip the
 * page onto it while ``events`` still held the other conversation's turns —
 * the "second session's reply appeared inside the first one" report.
 */
export const sendStillOwnsPage = (args: {
  /** What the page shows now (``useConversationRouting``'s ``routeIdRef``). */
  routeId: string;
  /** ``useConversationRouting``'s ``routeEpochRef`` now… */
  routeEpoch: number;
  /** …and as sampled synchronously when the send started. */
  epochAtSend: number;
  /** The session the send belongs to. */
  sessionId: string;
}): boolean =>
  args.routeId === args.sessionId || args.routeEpoch === args.epochAtSend;
