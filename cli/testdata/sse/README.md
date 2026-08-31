# SSE wire fixtures for the headless CLI (Slice 0b)

Each `.jsonl` file is one recorded-style session event stream. Every line is
the `data:` payload of one SSE frame — a flat `SessionEventFrame` JSON object
(`{seq, event_type, payload, timestamp, event_uid}`), exactly as documented in
`api/openapi.yaml` under `SessionEventFrame`.

Notes on the wire contract (see `backend/valuz_agent/adapters/event_sse_adapter.py`):

- `payload` values are all strings; structured fields are JSON-encoded strings.
- Live frames carry kernel-local `seq`; heartbeat frames carry the durable
  history cursor (the only value safe to persist for reconnect).
- Dedup across live/history replay uses `event_uid` only.
- Turn association is via `payload.message_id`; only events of the target
  `message_id` change run state.
- `run.failed` accumulates error state but does NOT end the run; the target
  turn's `session.idle` is the terminal event.

Files:

| file | scenario |
|---|---|
| `success.jsonl` | clean turn: user → assistant delta → thinking → tool calls → usage → idle |
| `error.jsonl` | error order: `run.failed` → `runtime.engine.usage` → `session.idle` |
| `interrupt.jsonl` | client interrupt: partial deltas → idle with `stop_reason=user_interrupt` → usage |
| `requires-action.jsonl` | `session.requires_action` with no human resolution |
| `heartbeat.jsonl` | heartbeat frames interleaved (durable cursor semantics) |
| `stale-idle.jsonl` | an idle for a *different* message_id that must not end the run |