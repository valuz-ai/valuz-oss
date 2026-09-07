# Playbook facade pagination

`valuz_agent.facade.playbooks.PlaybookLibrary` provides bounded read methods for
host and edition projections. They use existing generic Playbook tables and do
not change ownership, storage location, or the HTTP API.

```python
await library.list_project_page(
    user_id, project_id, limit=100, cursor=None, as_of_ms=None,
)
await library.list_runs_page(
    user_id, project_id, limit=100, cursor=None,
    research_scope_id=None, include_unscoped=False, as_of_ms=None,
)
```

Both return a frozen `PlaybookPage` with tuple `items` and nullable
`next_cursor`. Definition items are `(PlaybookDefinitionRef, PlaybookVersionRef)`
pairs; run items are `PlaybookRunRef`. No ORM objects are returned. Page size must
be an integer from 1 through `MAX_PLAYBOOK_PAGE_SIZE` (500); invalid arguments or
cursors raise `ValueError` before executing a read.

All owner and project filters run in SQL. A run query with a research scope
matches that scope exactly; `include_unscoped=True` additionally includes runs
whose scope is NULL. Omitting the research scope reads all scopes in that
project. Scope IDs are generic placement references: callers remain responsible
for checking Project and scope access before invoking the facade.

Each page uses one joined or direct SELECT with `LIMIT limit + 1`, ordered by
the definition/run's `(created_at, id)` ascending. Definitions without an
owner-matched selected version are excluded before pagination. Current reads
select exactly `current_version`, even if another version row has a greater
number. With `as_of_ms`, definitions and versions must have been created at or
before the cutoff, and the highest eligible version is selected in SQL. No
per-definition SELECT runs in Python. Run cutoff uses the run's `created_at`.

Pass `next_cursor` back unchanged until it is NULL. The cursor binds the method,
owner, project, research scope, unscoped option and cutoff; changing the page
size between requests is allowed. It stores a keyset position rather than an
offset, so deleting the preceding row does not invalidate continuation.

Cursors are opaque **unsigned positions**, not credentials. Query binding
detects accidental reuse, not malicious forgery; every query independently
reapplies its caller-provided owner/project/scope filters. A caller must obtain
`user_id` from a trusted request/session owner, never from a cursor. Pagination
does not hold a database snapshot across requests: concurrent updates to
mutable definition/run metadata can be observed on later pages. Historical
version selection does not reconstruct mutable definition or run status.

The original `list_project` and `list_runs` methods keep their existing complete
list behavior. Consumers that need bounded reads must opt into the page methods
and preserve incomplete-coverage information when they stop before the final
page. A row-count limit does not cap the byte size of one Playbook body or JSON
payload; consumers with a response-size budget must enforce that separately.
