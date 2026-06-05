---
name: valuz-project-docs
description: Search and reason over the project's bound knowledge base documents. Auto-loaded by the host for every project session. Use this when the user asks about facts, files, or context that should already exist inside the project's knowledge base. The current per-turn KB scope (which knowledge bases / folders / documents are bound) is announced inside the user message's `<additional-context>` block — consult it before guessing what's available.
origin-label: valuz · project knowledge base
icon: 📚
tags: [valuz, builtin, knowledge-base, docs]
---

# Project Knowledge Base

Every project session in Valuz can be bound to one or more knowledge bases.
When bound, those documents (and their indexed previews) are the canonical
source of truth for "what does this project know?" — prefer them over the
open web, your own recollection, or guesses.

The session's current binding state is delivered per-turn inside the user
message's `<additional-context>` block. If that block names a KB, folder,
or document, treat it as the authoritative scope for this turn. If no KB
scope is announced, the project has not been bound to any documents yet —
the search tools will return empty results.

## When to use

Reach for project knowledge any time the user's request implies prior
context that lives in the project, e.g.

- "Summarize what we've learned about X."
- "Pull together a brief on Y from the docs we already have."
- "What did the PRD say about Z?"
- "Find the latest research note on …"
- Any answer whose authority would come from already-uploaded material
  rather than your training data.

Use it _silently in the background_: don't ask the user "should I check the
docs?". Search first, summarize what you found, cite document titles back
to the user.

## How to search

Two host-provided MCP tools are available when this skill is loaded.
The kernel exposes them under the `valuz_docs` MCP namespace, so the
literal tool names you'll see at runtime are:

- `mcp__valuz_docs__list_doc_scope` — enumerate the document tree that's
  bound to this project. Pass no arguments for the root, or `folder_id`
  to drill into a subfolder. Use this first when you're unsure what's
  available.
- `mcp__valuz_docs__doc_search` — keyword search over bound documents.
  Required arg `query`. Optional `folder_ids` / `document_ids` narrow
  scope; `top_k` defaults to 5. Returns ranked snippets with the
  document id and filename.

Typical loop:

1. `list_doc_scope` to learn the rough taxonomy.
2. One or two `doc_search` calls with tight queries pulled from the user's
   ask.
3. Read the most relevant snippets, then cite the source document titles
   back to the user.

If both tools are absent at runtime (the host hasn't enabled them for this
session), fall back to asking the user which document or folder you should
focus on.

## Conventions

- Cite by document **title**, not raw id. The id is for tool calls; the
  user thinks in titles.
- Prefer multiple short searches over one mega-query — keyword matching
  rewards specificity.
- Never invent a document or quote. If a search returns nothing relevant,
  say so plainly.
- The KB is _scoped to this project_. Do not assume access to documents
  bound to a different project.
