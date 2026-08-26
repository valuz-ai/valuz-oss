# Live Partial Snapshot — recovering the in-flight turn without a replay log

> **Status: Implemented (2026-07-26).** Shipped whole; no phased rollout.
>
> One-line direction: **a reconnecting client needs the accumulated *state*
> of the in-flight turn, not the delta *sequence* that produced it** — so
> the kernel keeps that state and re-sends it, and no cursor, sequence,
> generation, or gap frame is needed anywhere.
>
> Companion docs: [session-stream-lifetime.md](session-stream-lifetime.md),
> [event-delivery-unification.md](event-delivery-unification.md).

---

## 1. The hole

Delta events are deliberately never persisted
(`DatabaseEventSink._NON_PERSISTED_TYPES`). Durable history can therefore
only ever replay **sealed** state.

So a client that drops mid-turn — tab hidden, session switch, network
blip — reconnects, backfills from `after_seq`, and gets nothing for the
message that is currently streaming. The transcript shows an empty
assistant block until the turn ends. The run is healthy; the UI looks
hung.

The hidden-tab case is not incidental. `session-stream-lifetime.md` §2.2b
releases the session stream on `visibilitychange: hidden` on purpose —
held SSE streams count against Chromium's ~6-connections-per-origin cap,
and exhausting the pool is the verified white-screen incident class
(#508). That guard is load-bearing and stays. It also means the drop is
routine, not exceptional.

## 2. Why state, not a replay log

The obvious fix is a bounded in-kernel ring buffer of live-only frames
with a `(generation, seq)` replay cursor. It was considered and rejected.

A replay log has to answer, on the wire, every question a log raises:
where is the cursor, is this sequence from the same producer or a
restarted one, how long is the retention window, what happens on
overflow, and what frame says "I could not serve you". That is six new
wire concepts, each with a failure mode, to reconstruct something the
client does not actually want. **State answers none of them.** Applying
it twice equals applying it once; a frame from a brand-new producer is
just the truth; nothing has to be sized against how long the client was
away.

Two consequences are worth stating outright:

- **Replaying deltas cannot restore the typewriter *timing* anyway.**
  Thirty seconds of buffered chunks flushed at reconnect renders exactly
  like assigning the text once. The sequence buys nothing the state
  doesn't already give.
- **A bounded buffer cannot bound the problem.** To actually cover
  "switch away and come back", the retention window would have to cover
  an arbitrary absence. A snapshot has no such parameter.

**Nothing about the non-persistence decision changes.** No delta reaches
the database, nothing survives the process, and
`event-delivery-unification.md` §8 still holds verbatim: a reconnect can
lose partial-token granularity but never a milestone. What is recovered
is the accumulated text, not the chunk boundaries that carried it.

Three closed decisions are left untouched, deliberately:

| Prior decision | Status here |
|---|---|
| §8 — live-only deltas never replay; losing token granularity is acceptable | Unchanged. No delta is replayed; state is re-sent. |
| §4 — reuse the fetch-based SSE stack, no WebSocket | Unchanged. No transport change. |
| §8 — single cursor; a dual-cursor envelope was rejected for simplicity | Unchanged. `after_seq` remains the only cursor. |

## 3. Mechanism

```
runtime
  └─ DeltaCoalescingSink            ~30ms batches, keyed per flow
      └─ PersistThenBroadcastSink
          ├─ DatabaseEventSink      canonical rows only
          └─ SessionEventBus  ──────────────────────────────┐
                │                                            │
                │  emit(): fold into LivePartialState        │
                │          BEFORE fanout                     │
                │                                            ▼
                └─ add_tap(sink, live_partial=True)      subscribers
                       └─ replay frames, then snapshot(), then live tail
                          — all under the one bus lock
```

`LivePartialState` (`kernel/src/core/live_partial.py`) accumulates, per
session, the text of each open stream, keyed by `(type, flow)` — the same
identity `DeltaCoalescingSink` batches on, so a subagent's text never
merges into its lead's.

**The invariant that makes it safe: it holds only what is not yet
durable.** Every canonical event drops the stream it sealed
(`assistant_message` → that flow's text; `thinking` → that flow's
thinking), and any turn boundary drops everything. A snapshot and a
durable backfill therefore can never describe the same bytes. Sizing
follows from the invariant rather than from a policy: at most one
unsealed segment per open stream, bounded by the model's own output
limit.

It lives inside `SessionEventBus` rather than beside it because the bus
already serializes replay-then-live under its lock. A snapshot taken
inside that lock cannot race the live tail it precedes — no second live
path to merge, no ordering to define.

## 4. Wire

A snapshot reuses the ordinary delta type with an absolute payload and
one marker:

```json
{ "event_type": "message.assistant.text_delta",
  "payload": { "text": "<everything streamed so far>",
               "message_id": "msg_1",
               "live_snapshot": "true" } }
```

No new event type, no new cursor, no new field on the envelope. The only
rule a consumer learns: **a marked frame replaces the open block's text
instead of appending to it, and does not seal it.**

The marker is spelled `live_snapshot` on both sides and duplicated rather
than imported — the module boundary forbids the host from reaching into
`src.core`. `test_live_snapshot_flag_matches_kernel` pins the two.

## 5. Staleness

One ordering case is real and is handled rather than prevented. The
server takes the snapshot when the tap attaches, which happens *before*
it reads history (tapping second would lose events landing in between).
A canonical event landing in that window therefore reaches the client
first, and the snapshot arrives stale.

The existing sealed-redelivery guard in `appendDelta` is exactly the test
that catches it: a snapshot whose text the sealed canonical already
contains is dropped; one carrying new content is a continuation segment
and opens a new block. That path is already load-bearing for runtimes
that seal mid-turn (provider-native search), so snapshots inherit proven
behavior instead of adding a parallel rule.

## 6. Scope

**In:** assistant text and thinking, on both the in-process and HTTP
kernel transports, for the desktop `buildTurns` reducer and the webui
`chat-store` reducer.

**Out, for now:** tool input/output streams and `workflow_progress`.
Tool streams are sealed within seconds by `tool_use` / `tool_result`, and
their card reconciliation already has three canonical writers — adding a
fourth ordering case there earns its own change and its own browser-QA
pass. The accumulator extends by one row in `_TRACKED_TYPES` plus its
seal rule when that happens.

**Not addressed:** the cloud-sandbox pump-lease staleness in
`session-stream-lifetime.md` §1.4 — a host pump pinned to a
dead-but-heartbeating instance during the idle grace period. That is a
host-side `peek` bug; nothing kernel-side helps it, and it stays open.

## 7. Failure semantics

| Situation | Behavior |
|---|---|
| Tab hidden, then back | Snapshot restores the streamed prefix |
| Session switch, any duration | Same — no retention window to expire |
| Two reconnects in a row | Idempotent; state is absolute |
| Canonical landed during reconnect | Stale snapshot dropped against the sealed text |
| Runtime sealed mid-turn | Snapshot carries the new segment only |
| Kernel restarted / no live kernel | No snapshot; durable history only, as today |
| Stream over `MAX_CHARS_PER_STREAM` | Stream dropped, not truncated — visibly behind beats confidently wrong |

## 8. Verification

Backend `pytest` at parity with `upstream/main` (same 40 pre-existing
failures, +21 new passing), `mypy` unchanged at 278, `ruff` unchanged at
1 pre-existing. Frontend `typecheck` and `lint` clean; the 35 touched
test files pass.

**Browser QA still required before release.** `session-stream-lifetime.md`
§6 rule: changes touching the conversation stream do not ship on unit
tests alone. Scenarios: hide the tab mid-turn and return; switch sessions
mid-turn and return; reconnect while a subagent is streaming; reconnect
in the window right around a canonical seal.
