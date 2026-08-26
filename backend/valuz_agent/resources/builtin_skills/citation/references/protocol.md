# Citation protocol details

## Binding scope

Citations project Evidence already used by the Agent. Typical source-bearing
results include document chunks, web or connector records, structured datasets,
and calculation Evidence. Greetings, ordinary conversation, code just created
in the current workspace, and clearly marked original reasoning do not require
a fabricated citation.

## Examples

```markdown
The policy took effect on 1 July [source](evidence://ev_policy_date).
```

```markdown
The two filings report different totals
[source](evidence://ev_q1_total)
[source](evidence://ev_q2_total).
```

For a structured result with a Collection hint:

```markdown
Operating revenue was CNY 174.1 billion
[source](evidence://evc_income_example#/data/0/operating_revenue).
```

Use the exact returned `collectionHandle`, `contentRoot`, and JSON pointer. The
runtime materializes only the used address into canonical Evidence. Keep claim
text and values outside the Evidence link.

## Derived values

When the Runtime has registered calculation Evidence, bind its handle to the
derived claim. Input Evidence can be cited on the input facts, but an input
handle must not be presented as proof of a calculation result. Never fabricate
a calculation handle.

## Failure handling

- No registered Evidence: preserve useful reasoning; describe the source
  limitation only when it matters to the request.
- Direct handle and Collection hint both absent: do not invent either.
- Conflicting registered Evidence: bind the relevant sources and explain the
  conflict without averaging it away.
- Tool, Audit, or source unavailable: keep Runtime-authored content visible;
  Citation processing must not hide, replace, or block it.

Do not use a raw URL or fabricated identifier as a substitute for the
`evidence://` protocol.
