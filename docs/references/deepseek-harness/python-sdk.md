# DeepSeek Harness Python SDK — interface notes

> Source: `~/agents/deepseek-harness/python/{sdk,sdk-runtime}` at `0.1.0-rc.5`,
> cross-checked against `packages/sdk/{protocol,server}` and verified by the
> scripts in [examples/](examples/) on 2026-08-13.

## Package split (mirrors codex server/SDK)

| Package | Dist / module | Role |
|---|---|---|
| `python/sdk` | `deepseek-harness-sdk` / `deepseek_harness` | High-level `DeepSeekHarness` turns API + low-level `HarnessClient` JSON-RPC client |
| `python/sdk-runtime` | `deepseek-harness-runtime-bin` / `deepseek_harness_runtime` | Locates the runtime binary and ships the default `cordis.yml` |

The SDK spawns the runtime as a **subprocess speaking newline-delimited
JSON-RPC 2.0 on stdio** (stdout is protocol-only; diagnostics go to stderr).
The runtime is a Node program (`dsh-jsonrpc-agent`) that boots whatever plugin
tree the config lists; the stdio JSON-RPC server is itself one plugin entry.

### Runtime carriers

- **exe (production)** — single-file Node executable
  `dsh-jsonrpc-agent-pkg-<platform>-<arch>`, injected into the wheel by
  `scripts/build-exe-for-python-sdk.ts`; not checked into git. macOS also needs
  a `-spawn-helper` sibling (node-pty).
- **node (dev-only)** — the built deploy closure under `runtime/node/`, run on
  system Node ≥ 22.19; opt-in via `DSH_RUNTIME_MODE=node`, never auto-selected.
- **source (repo checkout, what we used)** — no build needed:
  `launch_args_override=("node", "--import", "tsx", "<repo>/packages/examples/jsonrpc-demo/src/bin.ts")`
  with `runtime_cwd=<repo>`.

### Configuration model

The runtime **always requires an explicit config** (`DSH_CORDIS_CONFIG` env or
argv path) and exits loudly without one. "Zero-config" is client-side sugar:
when the launch resolves to the bundled runtime and no config channel is set,
`HarnessClient.start()` injects the checked-in default
`sdk-runtime/.../runtime/cordis.yml`. Any explicit `runtime_bin` /
`launch_args_override` disables that injection — pass `cordis=` yourself.

The bundled default composition: JSON-RPC server, agent spine, DeepSeek
adapter, JSONL(+zstd) session persistence + checkpoint policy, local
subprocess/bash, local fs provider. The richer
`examples/jsonrpc-agent/cordis.yml` adds: thinking (`reasoningEffort: max`),
subagent (spawn-in-process) + `subagent` tool, `todo_write` tool, fs tools,
token meter, compaction (`thresholdRatio: 0.8`), persona via
`DSH_SYSTEM_PROMPT`.

Environment knobs the compositions read: `DSH_CWD` (bash/fs cwd — the SDK sets
it from `cwd=`), `DSH_SESSION_ROOT` (persistence root, from `session_root=`),
`DSH_SYSTEM_PROMPT` (persona, examples composition only), `DEEPSEEK_API_KEY`,
`DEEPSEEK_BASE_URL`.

### Credentials

`~/.dsh/.credentials.yaml` is read by the `dsh-credentials-local` plugin, which
is part of the **web/headless profiles' base bundle — not the SDK default
config**. The SDK's bundled composition mounts no credentials provider, so the
DeepSeek adapter falls back to the launching **environment**:
`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` (or the SDK's `api_key=` / `base_url=`
kwargs, which just set those env vars). To reuse the dsh-managed key, read it
out of `~/.dsh/.credentials.yaml` yourself (see `examples/common.py`) or mount
`dsh-credentials-local` in a custom cordis. Precedence inside that provider:
process env > `$DSH_HOME/.credentials.yaml` > project `.env` > user `.env`.

## Wire protocol (complete method surface)

| Direction | Method | Notes |
|---|---|---|
| C→S | `initialize {cwd, provider, model, maxTokens?}` | **Per-process** provider/model/cwd. `serverInfo.name` = `deepseek-harness-sdk-runtime`. Unknown provider fails here; unknown model fails later as a `turn/end` error. |
| C→S | `session/prompt {sessionId, contentBlocks}` | Enqueue one user message; returns `{messageId}` **immediately** (inbox receipt, not a result). The server creates an agent per new `sessionId`. |
| C→S | `shutdown` | Flushes response, disposes the plugin tree (sessions reach persistence quiescence), exits 0. |
| S→C | `session.event {sessionId, event}` | Every durable session event of **every** session in the runtime, unfiltered. |
| S→C | `session.status {sessionId, status}` | Whole-agent `running` / `idle` transitions. |
| S→C | `subagent.started {parentSessionId, childSessionId}` | Lifecycle edge; the SDK uses it to build the session ancestry tree. |
| S→C | `subagent.finished {provider, agentId, parentSessionId, childSessionId, status, stopReason, lastAssistantMessage}` | In-process runs only. |

There is **no** cancel, session-close, resume, fork, or approval method.
Server→client requests are supported by the transport but never sent
("dead capability" reserved for future approval flows — the Python client
already exposes `next_request()` / `respond()` for it).

## High-level API semantics

```py
with DeepSeekHarness(provider=..., model=..., cordis=..., env=...) as h:
    r = h.run("prompt", session_id="s1")      # RunResult
    s = h.start_session("s2"); s.run(...)     # same, explicit session handle
```

`Session.run()` owns an **activity interval**: it subscribes to the session's
notification tree, sends `session/prompt`, waits for the inbox-receipt event
(`agent/inbox/spliced` containing its `messageId`), then collects until
`session.status == idle`. `final_response` = last committed root-session
assistant text in the interval; `finish_reason` = `kind` of the last
root-session `turn/end` (`completed` | `max-tokens` | `error` | …), `None` if
no turn ended. Descendant (subagent) notifications are included in
`RunResult.notifications` but never in `RunResult.events` (root-only), so a
child's text cannot replace the root response.

`DeepSeekHarnessConfig` fields: `provider` (default `deepseek-official`),
`model` (default `deepseek-v4-flash`), `max_tokens`, `cwd`, `runtime_cwd`,
`session_root`, `cordis`, `env`, `runtime_bin`, `launch_args_override`,
`request_timeout_seconds`, `shutdown_timeout_seconds`, `base_url`, `api_key`.

## Verified event vocabulary (real-API runs)

Root-session stream for one no-tool turn, in order:

```
agent/inbox/spliced          # prompt admitted to inbox (contains messageId)
turn/start {turn}
agent/inbox/spliced          # inbox claim (removedCount)
step/start {turn, step}
user/message                 # the prompt (+ a second one when skills inject a <system-reminder>)
session/title                # fallback title on first turn
request/header               # resolved {provider, model, maxTokens, reasoningEffort} + full system prompt + tool schemas
request/context              # {provider, model, contextWindow}   (deepseek-v4-flash: 1,000,000)
assistant/chunk*             # streaming (see below)
assistant/message            # committed message {content, usage, sourceEventSeqs}
step/end
turn/end {reason: {kind}}
```

Tool steps add `tool/call {callId, name, arguments}` (`arguments` is a JSON
**string**) and `tool/result {message: {content: [{type: "tool-result", toolCallId,
content, isError}]}}`, then a new `step/start` for the follow-up request.

`assistant/chunk` `data.chunk` shapes (all verified):

```
{type: "block-start", index, blockType: "text" | "reasoning" | "tool-call"}
{type: "text-delta", index, text}
{type: "reasoning-delta", index, text}
{type: "tool-call-delta", index, id, name, argumentsDelta}
{type: "block-end", index, block: {type, text | ...}}
{type: "usage", usage: {inputTokens, outputTokens, cacheReadTokens, reasoningTokens}}
{type: "finish", reason: {kind: "stop" | "tool-calls"}}
```

The full durable vocabulary this build understands (from the generated
`known-event-types.ts`) additionally includes `approval/asked|decided|policy`,
`compaction/start|end|prune|summary`, `todo/write`, `plan/mode`,
`sandbox/mode`, `llm/retry`, `hook/invoked|result`, `subagent/descriptor`,
`tool-workflow/*`, `goal/change`, `command/run|done`, `session/end-seed`.
Unknown event types make the **read path refuse the log** unless the event is
marked `ignorable` — the vocabulary is versioned by `SESSION_FORMAT_VERSION`
(still `0`, no compatibility promise).

## Verified behaviors and edges

- **Multi-turn continuity** works while the subprocess lives: the server keeps
  one live agent per `sessionId`; a second `run()` on the same session sees
  prior turns (verified: recalled file content from turn 1).
- **Cross-process resume does NOT work**: same `session_root` + same
  `session_id` in a new subprocess → first turn ends
  `error: session "<id>" already has a persisted log on disk that does not
  match this live session (id collision)`. Persistence backends have
  `load`/`prepare` (rehydration) — the SDK server just never calls them.
- **Bad model id** → `turn/end {kind: "error", error: {message, code:
  "INVALID_REQUEST", status: 400}}` (API-level error surfaces as a turn error,
  not a JSON-RPC error). Supported API models today: `deepseek-v4-pro`,
  `deepseek-v4-flash`.
- **`max_tokens=16`** → `finish_reason: "max-tokens"`; the truncated text was
  NOT committed as an `assistant/message` (final_response empty).
- **Skills leak in**: the bundled default composition enables filesystem skill
  discovery with project **and user** roots, so user-level skills (e.g.
  `~/.claude/skills/*`) get injected as a `<system-reminder>` user message.
  Disable via `skills: {enabled: false}` in the agent-spine config (the
  examples composition does this) or compose explicit roots.
- **Persistence layout**: `<session_root>/<sanitized-cwd-slug>/<session-id>/session.jsonl.zstd`
  (zstd frames; `compression: none` for debugging).
- **Subagents**: `subagent.started`/`.finished` + the child's full
  `session.event` stream (child `sessionId` = a fresh UUID). The SDK's
  session-tree filter keeps ancestry for the lifetime of the process.
- **stderr** is captured by the SDK (last 400 lines) and attached to timeout /
  transport-closed diagnostics.

## The two alternative surfaces

`packages/acp` is a second automation server (Agent Client Protocol over stdio
JSON-RPC) with a **different trade-off**: it has `session/new` (explicit cwd),
`session/cancel` (per-agent cancel that settles the prompt as `cancelled`) and
`session/request_permission` (one-shot approval requests) — three things the
SDK wire lacks — but it only emits committed assistant text
(`agent_message_chunk` per committed message; **no raw deltas, no tool events**),
advertises no MCP capability, and rejects non-empty `mcpServers`. For Valuz,
that event fidelity loss disqualifies it as the primary wire, but it proves the
in-core cancel/approval capabilities are wire-exposable.

The third surface is the **Web BFF wire** (`packages/host/apiproxy`, the
Typert RPC gateway the `dsh web` UI speaks: `client-request` frames with
`rpcId`, POST `/api/session.*`). Verified in a live web session
(DevTools capture, 2026-08-13) and in source: `session.cancel` →
`agent.cancel({kind:'user'}, {keepInbox: true})` (inbox survives, so a
follow-up "continue" prompt resumes cleanly); `session.prompt` carries
`mode: "queue" | "steer"` (`followup()` / `steer()`); `session.fork {atSeq}`
forks at an event-seq anchor; cold persisted sessions rehydrate through
`createApiRemoteAgentResolver` (load header + events → recompose the agent).
This wire is browser-internal (identity, attachments, projections ride along)
so we do not drive it directly — but it is the reference implementation for
every method the SDK server is missing: each one is a thin wrapper over the
same in-core calls.
