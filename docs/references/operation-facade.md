# Operation extension facade

`valuz_agent.facade.operations` is the public interface for Edition adapters.
It delegates to the existing Operation/Decision engine and owns no separate
state, database, or HTTP route. Existing operation and Citation wire contracts
are unchanged.

## Registration and execution

Register `OperationRegistration` with `register_operation` during application
composition. A registration selects a handler by operation type and version,
with optional cancellation cleanup, default proposal TTL and Pydantic
`input_schema`. Schemas validate input at proposal and again at confirmation;
they do not rewrite the approved payload or apply new defaults to old proposals.
Use strict fields and `extra="forbid"` when appropriate for the command.

The handler receives `OperationContext`, including:

- `db`: the caller's SQLAlchemy session, inside the engine's savepoint;
- `projects` and explicit `user_id`: the existing dependency and operation owner;
- `decision`: the confirming user's choices, not authoritative identity;
- `operation`: persisted operation ID/type/version/hash, creation context,
  actor/origin references, targets and expected revisions.
- `services`: optional request-scoped execution dependencies supplied by trusted
  server composition through `OperationLibrary(..., services=...)`. The mapping
  is copied and read-only; service objects themselves are not copied. They are
  never serialized, read from proposal/decision JSON, or used to select an owner.
  Construct a new library for each request/UOW; a later confirmation may inject
  a fresh source resolver and must recheck current authorization.

All engine invocations provide `operation`. It is optional only to preserve
existing direct handler/test construction. JSON in the context and handler
payload is detached from the stored proposal. An input field named
`operation_id` cannot replace the engine-selected identity. Handlers use that
identity for lineage, evidence seals and canonical results.

Handlers must check current domain authorization, expected revisions, policy
and source validity. A registered schema, owner label or approved card is not
itself authorization to write a target. Registration absence never falls back
to arbitrary mutation. No domain-specific models belong in this facade.

## Transactional library

Construct `OperationLibrary(db, projects)` in the caller's unit of work. It
offers `propose`, `get`, `confirm`, `cancel`, `request_changes` and `status`.
Every call requires an explicit, non-empty owner; status accepts at most 100 IDs
and excludes records belonging to other owners. Returned `OperationView`
values are detached snapshots, including the latest decision and an
`attempt_count` derived from approved decisions, not mutable ORM
objects. Mutating a returned view does not alter the approved proposal.

Adapters can recover an exact owner-scoped proposal with `find_by_idempotency`.
`list_page` accepts optional operation types (at most 32), project and origin
session filters, and a page size from 1 through 100. It returns `OperationPage`
with detached `items` and a nullable `next_before` position. Pass that position
with the same filters for the next page. Results use descending `(created_at,
id)` keyset order; every query reapplies the explicit owner. The position is
not an access grant. A page fetches at most `limit + 1` operation rows and only
the latest decision and aggregate attempt count for each returned operation,
not the full decision history.

The library never commits. Proposal records and any associated pending source
seals must be created in the same caller-owned transaction. On confirmation,
domain writes on `context.db` run in the engine savepoint: failure rolls them
back while preserving a failed operation/decision in the outer transaction.
The caller must commit that transaction to retain the failure receipt. A caller
rollback discards both the receipt and domain writes. Repeating a successful
confirmation returns the original canonical result without invoking the handler.

Existing expiry, supersede, cancellation and retry rules are unchanged. Evidence
storage implementations and domain-specific adapters remain separate consumers;
this facade does not migrate historical domain tables or reauthorize old records.
