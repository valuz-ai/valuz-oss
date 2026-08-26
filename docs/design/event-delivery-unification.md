# Event Delivery Unification — Kill the Polling, Keep the Behavior

[中文版](event-delivery-unification.zh-CN.md)

> Today the desktop client keeps real-time UI state fresh with **five
> independent polling mechanisms** layered on top of SSE. This document is the
> design for replacing all of them with **one pattern** — a durable, cursor-
> resumable, backfill-then-live event stream — applied at three scopes
> (**user / session / task**). The client ends up with **zero steady-state
> polling**: every realtime view becomes a pure projection over an event tape,
> and all missed-frame reconciliation moves server-side, inside the stream.
>
> **Hard requirement: no functional regression.** §6 is an exhaustive parity
> matrix — every behavior the current polls provide is mapped to the mechanism
> that preserves it. A change that drops any row in §6 is not done.
>
> Companion docs: [architecture.md](../architecture.md) (§7 real-time updates),
> [data-service-architecture.md](data-service-architecture.md) (the durable
> event store), [notifications.md](notifications.md),
> [task-attention-and-reliability.md](task-attention-and-reliability.md).

---

## 1. Principle

**One log, one cursor, one reducer — projected, never polled.**

The system already has the two primitives a root cure needs:

1. **A global monotonic cursor.** `events.id` is a single autoincrement PK,
   monotonic across *all* sessions of the durable store, and `events.user_id`
   is a first-class indexed column
   ([`models.py:118-140`](../../backend/kernel/src/adapters/sqlalchemy_store/models.py)).
   "Every event of user X after cursor N" is one efficient query — it just is
   not exposed yet.
2. **A proven backfill-then-live SSE loop.** `iter_events_sse`
   ([`event_sse_adapter.py:602-709`](../../backend/valuz_agent/adapters/event_sse_adapter.py))
   already does: backfill persisted events after a cursor, then follow the live
   bus, dedup the boundary by `seq`, throttle a gap re-read every 2 s, heartbeat
   every 15 s. The durable row id is stamped into the live frame by
   `PersistThenBroadcastSink`, so backfill and live share one cursor space.

The polls exist only because these primitives are bolted to a **single
`session_id`** and because the **`created → running` lifecycle edge is a DB row
mutation, not an event**. Lift the scope and put the lifecycle in the log, and
every poll becomes derivable from push.

---

## 2. Current State (the thing we must not regress)

Five pollers + two SSE paths + two *parallel* chat implementations. All are
real, all carry behavior, and the design below must preserve every one.

### 2.1 The five polls

| # | Poll | Where | Cadence | What it actually does |
|---|------|-------|---------|-----------------------|
| P1 | **Session status** | [`ConversationPage.tsx:5113-5260`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | 2 s | Bridges `created→running`: a session the client did *not* start (schedule-driven, freshly created, navigate-back) has no push signal for "it started", so it GETs `/v1/sessions/{id}` until `running` (→ subscribe SSE) or terminal (→ reconcile). Single-flight guarded; stops on running/terminal/handleSend/unmount. |
| P2 | **`events?after_seq` reconcile** | [`ConversationPage.tsx:5168-5183`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | one-shot | Recovers a turn that started **and** finished inside one poll window (instant provider fail, cached reply) whose terminal SSE frame landed in the gap between history-fetch and subscribe. One `listEvents(sid, maxSeq)`; if DB has rows past the cursor, open a replay subscribe. |
| P3 | **Desktop inline event poll** | [`ConversationPage.tsx:3761-3822`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | 500 ms | The desktop conversation runs a 500 ms `listEvents` poll **alongside** its own SSE stream ([`:3914-3943`](../../frontend/packages/app/src/pages/ConversationPage.tsx)) for gap-fill + idle-reconcile. (webui does not — see §2.3.) |
| P4 | **Running runs** | [`use-running-runs.ts:14,32`](../../frontend/packages/core/src/hooks/use-running-runs.ts) | 10 s | Module-singleton poll of `runsApi.list({status:"running"})` → `RunSummary[]`. Drives the sidebar running-count badge and the Activity page. Skips ticks while `document.hidden`; `refreshRunningRuns()` forces an immediate + 1.5 s nudge right after a session is minted (covers `created→running`). |
| P5 | **Finished runs** | [`ProjectLayoutBase.tsx:387-442`](../../frontend/packages/app/src/layout/ProjectLayoutBase.tsx) | 60 s + edges | `runsApi.list({status:"finished"})` → merged with live runs (dedup by `session_id`, newest first) into the per-project sidebar chat/run list. Fires on mount + 1.5 s retry keyed on `liveRunIds` transitions, plus a 60 s visibility-gated safety net. |

Adjacent streams in the same family (kept for context; P-list is the target):

- **Task events** ([`tasks.py:367-448`](../../backend/valuz_agent/api/routes/tasks.py)) — `/v1/tasks/{id}/events/stream?after_seq=`, but implemented as a **server-side 0.5 s DB poll with no broadcast bus**, 15 s heartbeat, 5 s terminal linger. Client [`use-task-events.ts`](../../frontend/packages/core/src/hooks/use-task-events.ts) reconnects at 500 ms, closes on `stream_end`, `keepAlive` keeps a finished task's stream open for `deliverable_updated`. There is also a **3 s `getTask` poll** in [`TaskDetailPage.tsx:462-468`](../../frontend/packages/app/src/pages/TaskDetailPage.tsx) for run/team/status metadata.
- **Notifications** ([`use-notifications.ts`](../../frontend/packages/core/src/hooks/use-notifications.ts)) — SSE (`/v1/notifications/stream`) + a **60 s REST backstop**.
- **Activity feed** ([`use-activity-feed.ts`](../../frontend/packages/core/src/hooks/use-activity-feed.ts)) — `/v1/activity`, **4 s head-poll** + keyset pagination for older pages.

### 2.2 The two SSE paths (the correctness floor)

- **Per-session stream.** `createSessionStreamController`
  ([`session-stream.ts:61-188`](../../frontend/packages/core/src/agent/session-stream.ts)):
  tracks `lastSeq` per envelope, resumes via `after_seq`, backoff
  `[1,2,4,8,16]s` then `error` needing manual `reconnect()`. Backed by
  `subscribeEvents` ([`sessions-api.ts:576-651`](../../frontend/packages/core/src/api/sessions-api.ts))
  → `GET /v1/sessions/{id}/events/stream?after_seq=N`.
- **Global stream.** `GET /v1/events/stream`
  ([`events.py:29-64`](../../backend/kernel/app/routes/events.py)) — a live-only
  in-process fan-out for host aggregators. **No `after_seq`, no user filter, no
  backfill.** Not a per-user replayable log today.

Why the polls coexist with SSE — **by design the live queues drop rather than
block** ([`event_stream.py`](../../backend/kernel/app/event_stream.py),
[`session_bus.py`](../../backend/kernel/src/core/session_bus.py): "a slow
subscriber must never stall the runtime's emit path… consumers that need a
complete record read the DB"). SSE is a best-effort latency optimization; the
`events` table is the system of record. The polls are the client-side
reconciliation against that record.

### 2.3 Two parallel chat implementations (important)

- **webui** uses the clean Zustand reducer
  [`chat-store.ts`](../../frontend/packages/core/src/store/chat-store.ts):
  single `_ingest`/`reduce` funnel (`:336-650`), attach = history replay + live
  SSE through the *same* reducer (`:140-194`). This is the template.
- **desktop** [`ConversationPage.tsx`](../../frontend/packages/app/src/pages/ConversationPage.tsx)
  (5000+ lines) does **not** use `chat-store` — it reimplements SSE, `appendEvent`,
  gap-fill, `buildTurns`, and all five/three polls with local `useState`.
  Loading UI (`Stop` button, `LogoShimmer`, "已处理 X 秒" timer) is
  `isBusy = deriveTurnActive(sending, status)`
  ([`conversation-loading.ts:45-48`](../../frontend/packages/app/src/pages/conversation-loading.ts))
  = `sending && !isTerminalSessionStatus(status)` — a local optimism flag
  AND-gated by the locally-mirrored session status.

The desktop fork is both the biggest source of polling and the biggest
simplification opportunity: converging it onto the reducer is part of the cure.

### 2.4 Run-lifecycle events: only half are in the log

The event vocabulary is a **closed enum**
([`events.py:10-59`](../../backend/kernel/src/core/events.py)):

- ✅ `user_message` — persisted, emitted at turn start
  ([`orchestrator.py:562-573`](../../backend/kernel/src/core/orchestrator.py)).
- ✅ `session_update{status}` — persisted, emitted at turn end
  ([`orchestrator.py:602-607`](../../backend/kernel/src/core/orchestrator.py)).
- ✅ `session_idle` / `session_error` — runtime terminal signals.
- ❌ **No `session_created`.** Session creation emits nothing.
- ❌ **No `run_started`.** `created → running` is `session.status="running";
  save_session()` — a **row mutation**
  ([`orchestrator.py:528-529`](../../backend/kernel/src/core/orchestrator.py)),
  not an event.

`GET /v1/runs` is therefore a **projection over `sessions.status`**
([`runs/service.py:157-243`](../../backend/valuz_agent/modules/runs/service.py):
`_RUNNING={running,paused}`, `_FINISHED={idle,completed,stopped,blocked,failed}`,
plus a task-status overlay in `_effective_status`), not an aggregation over the
log. **This gap is the whole reason lists are polled** — the list-relevant
transitions are not push-able because they are not events.

---

## 3. Root Cause

All five polls reduce to **two missing capabilities**, not five problems:

1. **No push before subscribe.** SSE is per-session and requires knowing a
   session is running to open it — but "it started" has no push channel. P1/P4
   exist purely to poll for that edge. (Chicken-and-egg.)
2. **Lifecycle is not in the log.** `created→running` and session creation are
   row mutations, so cross-session list state (P4/P5) and the fast-turn edge
   (P2) can only be recovered by re-reading the DB on a timer.

P3 is redundancy the desktop fork added on top of #1/#2.

Fix the two capabilities and the five polls have nothing left to do.

---

## 4. Target Architecture

**One pattern, three scopes, two logs, one client reducer.** SSE transport
(chosen: reuse the existing fetch-based SSE stack; no WebSocket).

### 4.1 The scopes — control plane vs data plane

A single user routinely has **many concurrent sessions** (parallel chats, and a
task **lead + N member sub-runs** all streaming at once). That fact forces the
split:

| Scope | Filter key | Log | Lifetime | Payload | Replaces |
|-------|-----------|-----|----------|---------|----------|
| **Control plane** | `user_id` | kernel `events` | **always-on, 1 conn** | lifecycle **only** (`session_created`, `run_started`, `session_update`/`idle`/`error`, todos summary) — **no token deltas** | P1, P4, P5 |
| **Conversation data plane** | `session_id` | kernel `events` | **on-demand** (viewed chat) | full transcript incl. `*_delta`, tool cards | P2, P3 |
| **Task data plane** | `task_id` (+ drill-in `session_id`) | host `valuz_task_event` | **on-demand** (viewed task) | plan/dispatch/review narrative (+ a member's transcript on drill-in) | task 0.5 s poll, `getTask` 3 s |

Why these boundaries:

- **Control plane must be user-level** because N concurrent runs cannot each be
  pre-subscribed (root cause #1). One user-scoped stream is the *only* thing
  that can see all of them, and it is what pushes the `created→running` edge for
  *any* session — killing P1's chicken-and-egg.
- **Delta-exclusion on the control plane is mandatory, not an optimization.**
  With task fan-out a user has `lead + M members` streaming simultaneously; a
  control plane carrying deltas would multiplex `M+1` token firehoses onto one
  connection for state (badges, lists) that never needs a single delta.
- **Task members are a third scope, and it already exists as a first-class
  `task_id` log** (`valuz_task_event`, indexed by `valuz_task_session` /
  `LiveMemberRegistry`). A task detail view watches the lead + all members — so
  "data plane = one session" was too narrow; it is `{session_id, task_id}`.
- **Control plane stays single-log, single-cursor** (kernel session lifecycle
  only). Task narrative lives in the on-demand task stream, so we never merge two
  autoincrement sequences into one control cursor. List rows that belong to a
  task are **collapsed under the task** in the client reduce using the existing
  `get_task_links_by_session_ids` join
  ([`datastore.py:399-420`](../../backend/valuz_agent/modules/tasks/datastore.py))
  — a projection concern, not a transport one.

```
  ┌──────────────────────── one always-on connection ─────────────────────────┐
  │  GET /v1/stream?after=<cursor>        (user_id scope, lifecycle-only)       │
  └───────────────────────────────────┬────────────────────────────────────────┘
                                       ▼
                         useEventLogStore  (single _ingest / reduce)
        ┌───────────────┬──────────────┼───────────────┬──────────────────┐
        ▼               ▼              ▼               ▼                  ▼
   runningRuns     finishedRuns    session status   notifications      activity
   (members collapsed under task via the session→task join)

  ┌── on-demand, only for what's on screen ─────────────────────────────────────┐
  │  GET /v1/sessions/{id}/events/stream?after_seq=N     (viewed chat)          │
  │  GET /v1/tasks/{id}/events/stream?after_seq=N        (viewed task)          │
  └───────────────────────────────────┬────────────────────────────────────────┘
                                       ▼  same _ingest, same reducer
                    per-session transcript · task plan/narrative
```

### 4.2 The one pattern all three share

Every scope is one instantiation of `iter_scoped_events_sse(scope_filter,
type_projection)`, mirroring today's `iter_events_sse`:

1. Backfill persisted rows after the client's cursor (`WHERE <scope> AND id >
   after ORDER BY id`).
2. Follow the live bus/tap for that scope.
3. Dedup the backfill↔live boundary by durable `seq`.
4. Throttled gap re-read (2 s) + heartbeat (15 s).

The client side is one `_ingest` funnel (the `chat-store` reducer generalized)
feeding view selectors. **From the client's perspective it is pure push; all
reconciliation is the server's throttled backfill inside the stream.**

---

## 5. Changes

### 5.1 Backend

1. **Emit the `run_started` lifecycle event** (closes root cause #2), persisted,
   added to the `EventType` enum + the SSE translation table
   ([`event_sse_adapter._translate_kernel_event`](../../backend/valuz_agent/adapters/event_sse_adapter.py))
   + `api/openapi.yaml`. Emitted at the `created→running` edge
   ([`orchestrator.py:528`](../../backend/kernel/src/core/orchestrator.py)),
   carrying `{session_id, title, project_id, task_id, current_todo, updated_at}`
   for the list projection. Covers schedule-driven runs the client never
   initiated. (`session_created` is intentionally **not** emitted — the session
   list stays REST + mutation driven; see §7 scope.)
2. **Cross-session read.** New `StorePort.get_events_after_for_user(user_id,
   after_seq, limit)` — the session-scoped
   [`get_events_after`](../../backend/kernel/src/adapters/sqlalchemy_store/store.py)
   with the `session_id` filter dropped. Add a **`(user_id, id)` composite
   index** on `events` (today only `user_id` alone + `(session_id, …)` exist) —
   reversible Alembic migration.
3. **User-scoped live tap.** Generalize `GlobalQueueTap`
   ([`events.py`](../../backend/kernel/app/event_stream.py)) to a per-user
   filtered fan-out (the tap already receives `(session_id, event)`; the event
   carries `user_id`).
4. **`iter_user_events_sse`** — the §4.2 pattern at user scope, lifecycle-only
   projection. Mount `GET /v1/stream?after=<cursor>` on the host, auth-scoped to
   the caller (fetch-based SSE carries `Authorization`).
5. **Converge the task stream onto the pattern.** Give `valuz_task_event` a
   broadcast bus/tap and replace the 0.5 s server-side DB poll
   ([`tasks.py:367-448`](../../backend/valuz_agent/api/routes/tasks.py)) with the
   same backfill-then-live loop. Fold the `getTask` 3 s metadata poll into
   task-lifecycle events on the same stream.
6. **Protect the durable-seq invariant.** The authoritative cursor is the
   **durable** store's `id`; never leak the `kernel.db` buffer seq onto the wire
   (WriteThroughStore `authority=durable` + `PersistThenBroadcastSink`). Add a
   contract test: user-stream frame `seq` == durable row `id`; backfill↔live
   dedup holds.

### 5.2 Frontend

1. **`UserStreamController`** — generalize `createSessionStreamController`
   ([`session-stream.ts`](../../frontend/packages/core/src/agent/session-stream.ts))
   to one app-level connection to `/v1/stream?after=<cursor>`, one global
   `lastSeq` persisted to `localStorage`, same `[1,2,4,8,16]s` backoff + resume.
2. **`useEventLogStore`** (Zustand) — one `_ingest` funnel, reducer copied from
   [`chat-store.reduce`](../../frontend/packages/core/src/store/chat-store.ts).
   Holds `bySession` (transcript/streaming/status/todos) + derived
   `runningRuns`/`finishedRuns` (members collapsed via the session→task join).
3. **Everything becomes a selector, polls deleted:**
   - `selectTurnActive(sessionId)` replaces `deriveTurnActive(sending, status)` —
     loading = "an unterminated `run_started` for this session in the tape",
     a pure projection. Breaks the `sending`↔SSE-open coupling at the root.
   - `selectRunningRuns()` / `selectFinishedRuns()` replace P4 + P5.
   - per-session transcript = `selectSession(id)`.
4. **Converge the desktop fork.** Desktop `ConversationPage` and webui `ChatPage`
   both consume the store; delete the desktop inline SSE + P3 500 ms poll + P1
   2 s status poll + P2 `reconcileFinishedTurn`. The 5000-line file collapses to
   selectors.

### 5.3 Cold-start hydration

Keep "REST snapshot for cold start, then follow the stream" — but only as
**cold start, not a steady poll**. The client persists its cursor; on reconnect
it sends `after=<cursor>` and the server replays the gap. First-ever open uses a
**bounded** backfill (recent window); deep per-session history stays
lazy-loaded on demand via the existing `listEvents(id, 0)` (the reducer already
ingests replay identically to live). The global tape carries lifecycle + recent
activity only, so it stays cheap even for a large history.

---

## 6. Functional Parity Matrix — nothing may be lost

Every behavior the current polls/streams provide, and the mechanism that
preserves it. **This table is the acceptance criterion.**

| # | Current behavior (source) | Preserved by | Notes |
|---|---------------------------|--------------|-------|
| **A. Conversation view** ||||
| A1 | Live token/thinking deltas, tool-call cards | Conversation data-plane stream (`session_id`, full payload) | Unchanged transport; deltas stay on the session stream, never on control plane |
| A2 | History replay on open (`listEvents after=0`) | Same call, ingested by the shared reducer | webui already does this; desktop converges to it |
| A3 | Resume mid-turn on refresh / navigate-back | Session stream opened with `after=maxSeq` | Same `after_seq` cursor; controller backoff retained |
| A4 | **Fast turn** (created→running→idle in one window) renders (P2) | `run_started` + terminal are persisted lifecycle events on the control plane; the session stream backfills from `after` on open | The gap P2 patched no longer exists — the stream is opened from a cursor and the server backfills; nothing to reconcile client-side |
| A5 | **Schedule-driven / externally started** session picked up (P1) | Control plane pushes `run_started` for any session → client opens the session stream (or the viewed one is already open) | Chicken-and-egg gone; no status GET loop |
| A6 | Loading UI (Stop / LogoShimmer / 已处理 X 秒) accurate, never stuck, never stranded on a missed terminal frame | `selectTurnActive` = unterminated run in the tape; terminal frame arrives on the (never-closed) session stream, else server backfill delivers it | Replaces `deriveTurnActive(sending,status)`; the AND-gate against status is subsumed because status itself is a projection |
| A7 | Todos live update | `session.todos.update` events (unchanged) + todos summary on control plane | |
| A8 | Interrupt/stop → `stop_reason` stamping | Reducer terminal handling (`chat-store.reduce` session.idle/run.failed) | Copied verbatim into `useEventLogStore` |
| A9 | Missed-frame correctness (drop-tolerant queue → DB reconcile) (P3) | Server-side throttled backfill **inside** every scoped stream (2 s gap re-read) | Reconciliation moves server-side; client stops polling but correctness floor is identical (same DB, same cursor) |
| A10 | Reconnect/backoff resilience | `UserStreamController` + session controller, same `[1,2,4,8,16]s` model | |
| A11 | Input-queue drain on turn end | Reducer's streaming true→false edge → `refreshQueue` (unchanged) | |
| **B. Sidebar / lists** ||||
| B1 | Running-count badge (P4 `count`) | `selectRunningRuns().length` over the tape | |
| B2 | Per-project chat/run list, running+finished merged, newest first (P5) | `selectRunningRuns` + `selectFinishedRuns`, same dedup-by-`session_id` merge | Merge logic moves from `ProjectLayoutBase` into a selector |
| B3 | `document.hidden` tick-skip (battery) | N/A — no ticks to skip; the stream is idle-cheap (heartbeat only). Optionally pause the stream when hidden | Strictly better: no periodic wakeups at all |
| B4 | Immediate nudge after minting a session (`refreshRunningRuns`) | `session_created`/`run_started` pushed on the control plane | The nudge existed to beat the 10 s cadence; there is no cadence now |
| B5 | `RunSummary` enrichment (title, current_todo, last_output, last_event, updated_at) | Carried on `run_started` + reduced from subsequent lifecycle/todos events; deep fields lazy-fetched on demand | Contract: `run_started` payload must carry the list-render fields (design pin §8) |
| **C. Activity page** ||||
| C1 | Running + finished + task leads interleaved | Same selectors + task-lifecycle on the control/task plane; task leads collapsed via the join | |
| C2 | Head-poll fresh + keyset pagination (older) | Live head from the stream; **keyset pagination for older pages stays REST** (`/v1/activity`) | Pagination of history is not a poll — it is on-demand paging; retained as-is |
| **D. Notifications** ||||
| D1 | Inbox stream (snapshot/added/updated/resolved) | Fold notification events into the control plane, or keep the dedicated stream | Already SSE; either converge or leave — no regression either way |
| D2 | Unread badge | Selector over notification entries (unchanged store) | |
| D3 | 60 s REST backstop | Replaced by the resumable control plane (no drop-gap to backstop) or kept as a cheap belt-and-suspenders | |
| **E. Task detail** ||||
| E1 | Plan DAG panel live | Task data-plane stream (`task_id`, `valuz_task_event`) backfill-then-live | Replaces the 0.5 s server DB poll with a bus |
| E2 | Dispatch/review narrative events | Same task stream (unchanged event set) | |
| E3 | Member progress (multiple member sessions) | Task-scoped stream carries all members' lifecycle; `LiveMemberRegistry` unchanged | The multi-member case handled by task scope, not the control firehose |
| E4 | Drill into a member/lead transcript | On-demand `session_id` data-plane stream for that member | Same session stream, reused |
| E5 | Completed-task follow-up (`keepAlive` → `deliverable_updated`) | Task stream stays open on a finished task (existing `keepAlive` semantics) | Preserved |
| E6 | `getTask` 3 s metadata (run/team/status) | Task-lifecycle events on the task stream | Metadata push replaces the 3 s poll |
| **F. Cross-cutting** ||||
| F1 | Multi-window independence | Each window holds its own control + data streams | Optional later: leader-elected shared stream (as `use-running-runs` singleton does today) |
| F2 | Ephemeral sandbox: dead sandbox still serves history (remote mode) | History reads route through `DataService`/`DataServiceReadClient`, unchanged; live deltas via the stream when alive | The scoped streams backfill from durable, which is exactly the sandbox-independent path today |
| F3 | Durable-seq authority (never leak `kernel.db` buffer seq) | Contract test + `authority=durable` invariant (§5.1.6) | |
| F4 | Auth on the stream | Fetch-based SSE with `Authorization` header (existing `fetchEventSource`) | Native `EventSource` still not used |

**Acceptance:** a build is "done" only when every row A1–F4 is demonstrably
preserved (browser-verified per [CLAUDE.md](../../CLAUDE.md) for UI rows, contract
test for backend rows).

---

## 7. Migration — incremental, one end state

### Confirmed scope (this iteration)

The committed target is **the five conversation + list polls (P1–P5)** on an
**OSS local-first** deployment. Locked decisions:

- **`run_started` payload** = `{session_id, title, project_id, task_id,
  current_todo, updated_at}` (the minimal set B5 renders). Pinned in
  `openapi.yaml` before the list selector is built.
- **`session_created` is not emitted.** The session *list* stays REST + mutation
  driven (low-rate: create/delete/rename); the control plane pushes only run
  lifecycle. One fewer new event type, a leaner tape.
- **`seq = durable events.id`** (verified globally monotonic per store). No
  dedicated `seq` column now — see the cursor contract (§8) for the future path.
- **SaaS relief valves (§9.4) are deferred**, not built: OSS is 1–3 connections.
  The only §9 item that stays a hard gate is the **no-DB-hold invariant** (§9.2).

Phases (no big-bang rewrite; each is shippable and converges on §4):

1. **Backend, no consumers yet.** `run_started` event;
   `get_events_after_for_user` + `(user_id,id)` index; `iter_user_events_sse` +
   `GET /v1/stream`; contract tests (F3 + no-DB-hold §9.2). Nothing changes for
   the client.
2. **Lists first (lowest risk).** `UserStreamController` + `useEventLogStore` +
   list selectors. Flip P4/P5 to selectors; **delete P4, P5**. Verify B1–B5, C1.
3. **Converge the conversation view.** Desktop `ConversationPage` + webui
   `ChatPage` consume per-session selectors; **delete P1, P2, P3**; `sending`
   becomes a selector. Verify A1–A11.

End state (this iteration): **one always-on control stream + on-demand
per-session data streams; one store; one reducer; zero client polling for the
conversation + list surfaces; all reconciliation server-side.**

### Deferred (not this iteration)

- **Task-stream convergence** (E1–E6): `valuz_task_event` bus + backfill-then-live
  to replace the 0.5 s task poll + 3 s `getTask` poll. The task data-plane scope
  (§4.1) already accommodates it; it is simply not in this cut.
- **Notifications / activity** convergence (D1/D3, C2) into the control plane.
- **SaaS scaling** (§9.4 relief valves, dedicated `seq` column, Redis fan-out).

---

## 8. Risks, Tradeoffs, Open Questions to Pin

- **Cold-start / backfill bound.** A large-history user's `after=0` replay must
  be windowed + lazy per-session, or it is a full-table filter — hence the
  `(user_id,id)` index is a hard requirement, and the control plane must exclude
  deltas.
- **`run_started` payload contract.** The list projection (B5) depends on
  `run_started` carrying the render fields (title/project/task/current_todo).
  Pin the exact payload in `openapi.yaml` before building the selector.
- **Member `user_id` (verified).** Member sub-runs are created under the task
  owner's id — `build_member_session(..., user_id=task_row.user_id)` then
  `kernel_client.create_session(user_id, member_session)`
  ([`dispatcher.py:197-250`](../../backend/valuz_agent/modules/tasks/dispatcher.py)).
  So members **do** flow into the user control plane, and the list reduce **must
  collapse** them under their task via the `get_task_links_by_session_ids` join —
  otherwise the sidebar sprouts one row per member. This is a load-bearing
  invariant of B2/C1, not an optimization.
- **Control plane single-cursor decision.** We deliberately keep the control
  plane on one log (kernel session lifecycle) so there is one cursor. The
  rejected alternative — merging session + task logs into one control stream —
  needs a dual-cursor envelope (`after_session=X&after_task=Y`); documented here
  as considered-and-rejected for simplicity.
- **Cursor contract (do not hardcode `events.id`).** The design depends on an
  abstract, resumable, per-durable-store **monotonic `seq`**, not on the PK being
  a global autoincrement int. Today `seq == durable events.id` and they coincide;
  the wire contract already uses `seq` (cross-store identity is `event_uid`, never
  `seq` — see [[valuz-event-seq-two-stores]]). Define this as a contract: *`seq`
  is a per-store monotonic value, DB-assigned at persist, stamped onto the live
  frame*. If a future durable backend (sharded/partitioned PG, UUID PK, snowflake
  id) makes the PK non-monotonic, add a dedicated `seq BIGINT` column — the blast
  radius is that column's population, **not** the architecture. Per-session
  data-plane streams are immune regardless (per-session monotonicity is trivially
  available); only the control plane's single-cursor leans on a store-global
  monotonic `seq`, and even that degrades gracefully to a dedicated low-rate
  lifecycle sequence or REST-reseed.
- **Live-only deltas (`seq: null`) never replay.** A reconnect can lose
  partial-token granularity but never a milestone (milestones are persisted).
  Unchanged from today; acceptable. *(Still true. Since 2026-07-26 the kernel
  additionally re-sends the accumulated STATE of an in-flight stream to a
  client that joins mid-turn — see
  [live-partial-snapshot.md](live-partial-snapshot.md). No delta is persisted
  or replayed, and no cursor is added; only the accumulated text is recovered,
  not the chunk boundaries.)*
- **Two chat implementations converge into one.** This is a large diff on a
  5000-line file; it is also the point — the desktop fork is where most of the
  polling and divergence live.

---

## 9. Deployment & Connection Scaling

An always-on user-level SSE connection replaces short, fast-returning polls with
a held-open connection. The natural worry — "does that create server connection
pressure on a SaaS deployment?" — is legitimate but mostly points the opposite
way from intuition. This section is the honest accounting.

### 9.1 "Fast-returning polls are lighter" is usually backwards

A poll is short per request but **pays the full request cost every tick,
whether or not anything changed**: TLS, auth middleware, routing, a DB query,
serialization. The desktop 500 ms poll alone is ~120 req/min per open
conversation of pure waste; stacked with the 2 s status + 10 s runs + 60 s
finished polls, one active user is a **constant QPS floor** proportional to
`users × poll frequency`, independent of activity.

An SSE connection on an **async ASGI** server (FastAPI/uvicorn) is a coroutine
**parked on I/O** when idle — a few KB of memory, no thread, no CPU, plus a 15 s
heartbeat write. Its cost scales with **actual events**, not with time. So the
trade is **higher steady connection count for much lower steady QPS / DB load** —
and on async infra, connection count is the cheap axis while QPS/DB is the
expensive one. For the common idle/waiting case the unified stream is
**dramatically cheaper**, not costlier, than today's polling.

### 9.2 Load-bearing invariant: a stream must never hold a pooled DB session

The failure mode that actually exhausts a SaaS backend is not "many
connections" — it is **each connection pinning a pooled DB session for its whole
lifetime** (`N` streams ⇒ `N` DB connections held ⇒ pool exhaustion). This is the
[[diagnosis-leak-vs-occupancy-lesson]] connection-pool whiteout family (an SSE
zombie leak).

Today's `iter_events_sse` does **not** step on this, and the new user-level
stream must inherit the same discipline:

- **Backfill is a discrete per-read call** — `list_events_after →
  _history_reader().get_events(...)`
  ([`event_sse_adapter.py:626`](../../backend/valuz_agent/adapters/event_sse_adapter.py)):
  open → read → close, once per backfill, never held between reads.
- **The live path is an in-memory queue tap** (`subscribe_session_events`), not a
  DB cursor.

So connection count never amplifies into DB-connection count. **Contract test:
an idle SSE connection holds zero pooled DB sessions.** This joins the F3
durable-seq test as a hard gate.

### 9.3 Where it actually bites: multi-tenant SaaS only

For the **OSS local-first target** (one user, one backend process) this is a
**non-issue** — it is 1–3 connections total, and the net server load is *lower*
than the polling it replaces. Connection pressure is real only for the
**commercial multi-tenant SaaS edition**, and there each pressure point has a
standard answer:

| Pressure point | Cause | Mitigation |
|----------------|-------|------------|
| **fd / socket limits** | `N users × (1 control + K data)` held-open sockets exhaust file descriptors / ephemeral ports / LB connection caps | Raise ulimit + LB max-conn; **idle reaping** (§9.4) caps concurrency to *active*, not *all*, users |
| **LB / proxy idle timeout + buffering** | nginx / ALB / Cloudflare kill idle connections at ~60 s; some CDNs buffer `text/event-stream` | 15 s heartbeat (already present) stays under the timeout; disable proxy buffering for `text/event-stream` |
| **In-process tap fan-out across replicas** | the live tap is in-process; in a multi-replica deployment a user's connection must reach the process holding their events | Sticky-by-`user_id` routing, or a **Redis pub/sub tap** for cross-replica fan-out — a SaaS-overlay concern; OSS single-tenant never hits it |
| **Slow-consumer memory** | a slow client backs events up server-side | Already covered by the drop-on-full + DB-reconcile backpressure (§2.2) |

### 9.4 Relief valves to build in

Three levers, all cheap because the stream is cursor-resumable:

1. **Idle reaping + cursor resume.** Close the control stream when the tab is
   hidden or after an inactivity window; reopen with `after=<cursor>` on focus
   (sub-second). This drops concurrent connections from "every logged-in user" to
   "every *active* user" — the single biggest SaaS lever.
2. **Per-user connection coalescing.** One control stream per user per browser
   (leader election / `BroadcastChannel`), not one per tab — the same singleton
   trick [`use-running-runs`](../../frontend/packages/core/src/hooks/use-running-runs.ts)
   already uses today.
3. **The no-DB-hold invariant + contract test** (§9.2) — keeps connection count
   from ever becoming DB-connection count.

### 9.5 Net

- **OSS:** strictly *lower* server load than the polling it replaces (QPS / DB
  drop sharply); connection count is single-digit. No concern.
- **SaaS:** connection count rises, but async infra is built for exactly this
  (C10K); the real work is fd limits, LB timeouts, and cross-replica fan-out —
  standard ops items, not architectural faults. As long as §9.2 holds, more
  connections do **not** mean resource exhaustion.
