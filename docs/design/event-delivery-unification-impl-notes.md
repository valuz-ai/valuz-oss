# Impl Notes — Phase 1 (backend user-stream skeleton)

Companion to [event-delivery-unification.md](event-delivery-unification.md). Records
decisions taken during implementation that refine the design.

## Decision 1 — no new `run_started` kernel event (supersedes §2.4 / §5.1)

During implementation the emit paths were traced and **every run is already
bracketed by persisted, seq-stamped events**:

- **Start:** `user_message` — emitted at `run_turn` entry
  ([`orchestrator.py:575`](../../backend/kernel/src/core/orchestrator.py)), right
  after `session.status="running"` is persisted (`:541`). Persisted (not in
  `DatabaseEventSink._NON_PERSISTED_TYPES`), so it carries a durable seq.
- **End:** `session_idle` (success) or `session_error` (failure) — emitted by the
  runtimes through the same `PersistThenBroadcastSink` pipeline; both persisted
  and seq-stamped. (`session_update{status}` is an extra success-path status
  marker at `:615`.)

So the control-plane running/finished projection is derivable from **existing**
events — `user_message` → running, `session_idle`/`session_error` → finished — with
no new event type, no `EventType` enum change, and no kernel emit change. The
control-plane host adapter forwards these as text-free lifecycle frames (it does
not carry `user_message`'s prompt text onto the user-wide stream).

Net: the design's goal (lifecycle is push-able) is met by reusing the log's
existing content. The `run_started` event is dropped; §2.4's "lifecycle is not in
the log" is refined to "the *dedicated named* event is absent, but the transition
IS bracketed by persisted events."

## Phase 1 scope (this branch: `claude/event-stream-backend-p1`)

Backend skeleton, **no client consumers** — client behavior unchanged.

1. **Store read** — `StorePort.get_events_after_for_user(user_id, *, after_seq,
   types=None, limit)` (cross-session cursor read), implemented in every
   StorePort implementer + the `/rpc` surface + host read clients:
   - `sqlalchemy_store/store.py`, `write_through_store.py`, `remote_store.py`
   - `app/data_service.py` (`POST /rpc/get_events_after_for_user`)
   - host `adapters/data_service_client.py`, `adapters/data_service_local.py`
   - contract test `test_data_service_contract.py`
2. **Index** — `(user_id, id)` composite on `events`; reversible kernel Alembic
   migration.
3. **User-scoped live tap** — user-filtered global tap via the KernelClient seam.
4. **Host adapter** — `iter_user_events_sse` (backfill-then-live, lifecycle-type
   projection, text-stripped).
5. **Route** — `GET /v1/stream?after=<cursor>`, auth-scoped.
6. **openapi.yaml** + contract tests (F3 seq==durable id; §9.2 no-DB-hold).

Deferred to later phases: client `UserStreamController` + `useEventLogStore` +
selectors (Phase 2/3), task-stream convergence, SaaS scaling.
