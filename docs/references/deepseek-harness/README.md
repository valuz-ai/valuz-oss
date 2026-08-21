# DeepSeek Harness (`dsh`) — runtime candidate reference

> Explored **2026-08-13** against a local checkout at `~/agents/deepseek-harness`,
> repo version **`0.1.0-rc.5`** (developer preview — upstream explicitly warns of
> compatibility-breaking changes). All "verified" claims below were reproduced
> against the real DeepSeek API with the scripts in [examples/](examples/).

## What it is

DeepSeek Harness is DeepSeek AI's open-source agent harness. Its defining
property is an **everything-is-a-plugin** architecture on the Cordis framework:
the model adapter, tool registry, session log, and even the agent loop itself
are plugins composed at boot from a `cordis.yml` tree. Any row can be replaced
by a patch layer, so a deployment's capability set (tools, persistence,
sandbox, approval policy, MCP servers) is pure configuration.

Repo layout that matters to us:

- `packages/core/` — session log, tools, agent loop (`turn → step → request → tool` flow)
- `packages/sdk/` — the JSON-RPC stdio protocol + server plugin + TS client
- `packages/acp/` — a separate automation server speaking the [Agent Client Protocol](https://agentclientprotocol.com)
- `packages/mcp/mcp-client` — MCP client plugin (stdio + streamable-http), tool names `mcp__<server>__<tool>`
- `python/sdk` + `python/sdk-runtime` — the Python subprocess SDK and its bundled runtime carrier (analogous to codex's server/SDK split)

Two facts shape any integration:

1. **The session log is the source of truth.** Anything model-visible must be
   reconstructable from the append-only `SessionEvent` log; the SDK streams
   these envelopes verbatim over the wire, so the event vocabulary *is* the
   wire contract.
2. **A turn is `turn/start → (step/start → model request → tool calls → step/end)* → turn/end`**,
   driven by an inbox: prompts are spliced in, and steering/injected context
   joins the next admitted request. There is no per-prompt result on the wire —
   clients own their own "activity interval" (prompt receipt → whole-agent idle).

## Why it matters to Valuz

A DeepSeek Harness runtime would be our 4th kernel runtime
(`claude_agent` / `codex` / `deepagents` / **`deepseek_harness`**), giving
first-class access to DeepSeek models (`deepseek-v4-pro`, `deepseek-v4-flash`,
1M context window verified) through DeepSeek's own agent loop — the same
positioning as running Claude models through the Claude Agent SDK.

## Documents

| File | Content |
|------|---------|
| [python-sdk.md](python-sdk.md) | The Python SDK surface, the JSON-RPC wire protocol, launch/runtime carriers, config injection, credentials — with verified event/chunk vocabularies |
| [runtime-gap-analysis.md](runtime-gap-analysis.md) | `RuntimePort` ↔ SDK mapping, the five hard gaps, and the recommended integration path |
| [examples/](examples/) | Runnable exploration scripts (real API) + sanitized wire transcripts |

## Headline findings (TL;DR)

Verified working: prompt → full event stream → idle; multi-turn continuity in
one runtime process; bash tool calls; subagent lifecycle with descendant event
streams; `max-tokens` / `error` / `completed` stop reasons; JSONL+zstd session
persistence; per-process `initialize(provider, model, maxTokens)`.

Hard gaps on today's SDK wire (details and mitigations in the gap analysis):

1. **No interrupt/cancel** — abandoning a turn means killing the subprocess.
2. **No cross-process session resume** — a persisted session id collides
   instead of rehydrating ("id collision", verified); continuation only works
   while the original subprocess lives.
3. **No approval flow** — the transport reserves server→client requests but the
   server never sends one; tools run unattended.
4. **No fork** — `ctx.sessions.fork` exists in-core but is not on the wire.
5. **No mid-turn steering surface in the high-level API** (low-level
   `session_prompt` can enqueue, semantics are inbox-splice).

All five are server-plugin gaps, not architecture gaps — the capabilities exist
in-core (agent cancel, persistence `load`/`prepare`, fork, approval seam), and
the plugin architecture + the separate ACP server (which already has
`session/cancel` and `session/request_permission`) prove the wire can carry
them. A thin custom server plugin (or upstream contribution) closes them.
