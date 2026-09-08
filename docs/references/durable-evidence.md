# Durable Evidence extension interface

`valuz_agent.facade.durable_evidence` owns domain-neutral snapshots, pending
canonical source seals and append-only provenance. Domain bindings and taxonomy
belong to installed extensions. CitationBundle V1 and the read-only
`MessageEvidenceLibrary` wire contract remain unchanged.

## Promotion boundary

Reading an answer or a citation does not persist evidence here. An authorized
durable consumer explicitly adopts canonical evidence:

1. Its command validates the owner, target, permissions and expected revision.
2. It reads canonical message evidence, or verifies a trusted originating
   Runtime's sealed source proof. Browser/model supplied source JSON is not proof.
3. `seal_pending` stores the exact proof with the actual Operation ID; exact
   repeats retain their original status. Conflicting payloads are rejected.
4. Confirmation revalidates the source/proof, calls `capture`, appends provenance,
   writes the domain binding/object and consumes the pending seal.
5. The command commits these writes together with the Operation success receipt.

`DurableEvidenceLibrary(db)` never commits. All methods require an explicit
non-empty owner. Capture returns `(EvidenceSnapshot, reused)`; views are detached
from persistence. The API offers no snapshot body edit or provenance update.
Snapshot status changes require the expected status. Forbidden or missing
snapshots cannot be retrieved through the content API, including reuse capture.
Status is not a substitute for the caller's current source ACL validation.

Identical canonical source, version, locator, evidence body and temporal/unit
semantics reuse a snapshot for the same owner. A different message citation ID
does not produce a new durable identity. Derived captures also include declared
method and input-version context. Different owners never share a private row.
Original non-derived fingerprints are compatible with prior extension captures.

## References and reads

`SourceRef`, `Locator`, `EvidenceRef` and `EntityRef` are shared references.
Snapshot persistence retains the raw canonical Citation locator and evidence
JSON, not a rewritten Citation wire. Provenance records carry typed
`context_refs`; core does not define an industry's scope IDs or classifications.

`append_provenance` validates referenced snapshot and parent provenance ownership.
It appends a new record; changing an existing research object does not rewrite
its earlier evidence or lineage. Referenced source JSON remains opaque metadata,
not a credential or an authority grant.

`list_provenance` uses descending `(created_at, id)` keyset pagination, optional
exact subject version and as-of filters, and at most 100 items per page. Pending
seals are bounded to 200 per operation: overflow or expiry fails explicitly,
never promotes an unnoticed partial set. Consuming a seal checks owner,
operation, exact citation hash, expiry, pending status and the history fence.

Exported `*Storage` mappings are a stable SQL projection/migration seam for
bounded joins. Consumers use the library for normal writes, not mutable ORM
objects. They must apply owner, permission and coverage constraints to joins.

## Schema and import

Host migration `0047` adds `valuz_evidence_snapshot`,
`valuz_pending_evidence_seal` and `valuz_provenance_record`. It does not rename
an extension table or migrate user data automatically. Downgrade refuses to
drop any nonempty shared table.

`import_evidence_records(connection, owner_user_id=..., kind=..., records=...)`
accepts at most 100 preflighted records per atomic batch, within the caller's
outer transaction. The extension maps its own legacy context fields and checks
the full authorized reference closure before import. The port preserves IDs,
hashes, timestamps and states, verifies exact replay and rejects collisions
without overwriting. Imported pending-control records acquire an independent
`historical_only` fence: their old status is preserved but cannot authorize a
new execution. Caller rollback also rolls back the first SQLite savepoint.

This is a schema-migration API, not an untrusted HTTP import surface. Installing
the shared schema alone does not retire an extension's old writable store; the
extension must switch readers/writers and complete its own checked migration.
