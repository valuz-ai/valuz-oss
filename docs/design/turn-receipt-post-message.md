# Turn receipt & idempotency for POST message (C9)

Status: design (contract-first, Slice 0b). Not yet implemented — CLI v1 is
single-turn on a fresh session and never auto-retries POST, so this contract
only gates session reuse (Slice 8).

## Problem

`POST /v1/sessions/{id}/messages` returns immediately with `SessionDetail`
and no turn receipt. A client cannot:

- know which `message_id` the dispatched turn will produce, until it sees
  `message.user` on the SSE stream;
- retry a POST whose response was lost (network cut between send and
  response) without risking a duplicated turn (409 does not distinguish
  "my previous POST won" from "someone else is running").

The SSE stream already carries `message_id` in every turn event's payload,
so turn *association* is solved. The gap is *acknowledgement* and
*idempotency* of the dispatch itself.

## Proposal (choose one)

### Option A: POST returns the message id (minimal, recommended first)

`POST /v1/sessions/{id}/messages` (and `messages/sync`) additionally return
`message_id` in the `SessionDetail` (via a `last_message_id` field, or a
small `TurnReceipt` wrapper). Client learns the turn id synchronously; no
SSE wait needed to anchor. Retry remains unsafe without a key.

### Option B: Idempotency-Key header (full)

Standard `Idempotency-Key` header on POST message: backend stores
(key → message_id) per session; replay returns the stored receipt instead
of dispatching again. 409-on-running stays for genuinely concurrent sends.

### Option C: A + B combined (target)

Return receipt immediately (A) and honor `Idempotency-Key` when present (B).
CLI sends a generated key on every POST so "response lost" retries are safe.
Both are backward-compatible: absent key = current behavior.

## CLI v1 constraint

CLI v1 (Slice 0–4) does NOT retry POST and does NOT reuse sessions:

- "POST response lost" → client continues to the open SSE stream, waits for
  `message.user`, anchors the turn, and proceeds; never re-POSTs.
- Session reuse (`--session`) stays closed until Option C lands.

## Gate

Slice 8 opens session reuse only after: (1) POST returns a receipt, (2)
idempotent replay verified, (3) stale-turn isolation tests (old idle must
not end the new run) pass.