# DeepSeek Harness × Valuz `RuntimePort` — gap analysis

> Compares the kernel's runtime contract
> (`backend/kernel/src/core/runtime_port.py`) against what the dsh Python SDK
> (`0.1.0-rc.5`) offers today. Verified claims come from the runs in
> [examples/](examples/); see [python-sdk.md](python-sdk.md) for the wire facts.

## Proposed shape

One **runtime subprocess per kernel session**, owned by a new
`DeepSeekHarnessRuntime` adapter under `kernel/src/runtimes/deepseek_harness/`:

- `initialize(provider, model, maxTokens)` is **per-process** on the dsh wire,
  which matches our invariant exactly: the `(runtime, provider, model)` triple
  is locked at session creation and never changes mid-session.
- The subprocess maps onto the kernel's existing warm-runtime cache: keep it
  alive between turns (`prepare` = spawn + `initialize`, no model traffic;
  `close` = `shutdown`), evict = kill.
- `run()` = the SDK's activity-interval loop: `session/prompt` → stream
  `session.event` → map to kernel events → `session.status: idle` ends the
  turn. Use the low-level `HarnessClient` (or an asyncio port of it — the
  shipped client is synchronous/thread-based and would need `asyncio.to_thread`
  or a native-async reimplementation; the protocol is trivial NDJSON).
- Factory/registration touchpoints: `RuntimeProvider` literal + `factory.py`
  dispatch + `ALLOWED_PROTOCOLS_BY_RUNTIME` (dsh's DeepSeek adapter speaks an
  OpenAI-compatible chat-completions SSE endpoint — the smoke test drives it
  with a mock `chat/completions` server — so the natural constraint is
  `openai_completion`, with `DEEPSEEK_BASE_URL` as the gateway override) +
  `availability.py` (present only when a runtime binary/checkout is locatable)
  + api contract `RuntimeProvider` enum.
- Config injection: generate a per-session `cordis.yml` (or a patch overlay on
  the bundled default) at session-create time — this is where system prompt,
  skills roots, MCP servers, compaction thresholds, and tool policy land.
  `system_prompt_builder` output → agent-spine `persona`; project cwd →
  `DSH_CWD`; kernel-managed session dir → `DSH_SESSION_ROOT`.

## Contract mapping

| `RuntimePort` member | dsh SDK today | Verdict |
|---|---|---|
| `run(session, user_message)` | `session/prompt` + own the interval to `idle` | ✅ clean |
| `prepare(session)` | spawn subprocess + `initialize` (no model traffic) | ✅ clean, idempotent-safe |
| `close()` | `shutdown` (flush → dispose → exit 0) | ✅ clean |
| `update_sink(sink)` | adapter-internal | ✅ n/a |
| `supports_native_continuation` | **True while the subprocess lives** (live agent per sessionId, verified multi-turn recall); **False across restarts** (id collision on persisted logs, verified) | ⚠️ gap #2 |
| `interrupt()` | **no wire method** — only subprocess kill (loses the turn tail; JSONL persistence keeps checkpointed events) | ❌ gap #1 |
| `submit_action(...)` / `requires_action` | **no approval flow on the wire**; tools execute unattended. In-core approval seam + transport server→client requests both exist but are unused | ❌ gap #3 |
| `fork_session(...)` | `ctx.sessions.fork(source, boundary?)` exists in-core; not on the wire | ❌ gap #4 (`NotImplementedError` initially — the port explicitly allows this) |
| `consume_turn_anchor()` | event `seq` is a natural anchor (fork-by-boundary in-core takes one); nothing consumable on the wire yet | ⚠️ follows gap #4 |
| `run_task_coverage(no_op_tool)` | needs per-turn tool injection; dsh tools are cordis-composed, not per-request | ⚠️ needs design (an MCP-exposed no-op tool scoped to the coverage turn is the likely route) |
| `approval_rule_matcher` | exact-args fallback until approvals exist | ✅ default |

## Event mapping (kernel ← dsh)

| Kernel `OutboundEventType` | dsh source |
|---|---|
| `text_delta` | `assistant/chunk {type: text-delta}` |
| `thinking` / `thinking_delta` | `assistant/chunk` reasoning block-start / `reasoning-delta` |
| `assistant_message` | `assistant/message` (committed; carries usage + source model) |
| `tool_use` | `tool/call` (`arguments` is a JSON string — parse before emitting) |
| `tool_input_delta` | `assistant/chunk {type: tool-call-delta}.argumentsDelta` |
| `tool_result` | `tool/result` (`content[].type == "tool-result"`, `isError`) |
| `usage_update` | `assistant/chunk {type: usage}` (+ `request/context.contextWindow` for the denominator) |
| `session_idle` | `session.status == idle` (whole-agent, not per-turn — correct for our turn boundary) |
| `session_error` | `turn/end {reason.kind == error}` (carries provider message/code/status) |
| `compaction` | `compaction/start\|end\|summary\|prune` events (compaction plugin mounted) |
| `todo_update` | `todo/write` (tool-todo plugin mounted) |
| `turn_phase` | synthesize: `runtime_init` (spawn+initialize), `dispatch` (`session/prompt` → first chunk) |
| `mode_changed` / `plan_update` | `plan/mode` event exists; not explored |
| `requires_action` / `action_resolved` | — blocked on gap #3 |
| `bg_task_*` / `workflow_progress` | dsh jobs/workflow plugins exist (`job_*` tools, `tool-workflow/*` events); map later if composed |

Subagent lifecycles (`subagent.started`/`.finished` + full descendant event
streams) have **no kernel equivalent today** — Claude/codex hide sub-agent
internals. Cheapest v1: fold descendant activity into tool progress on the
parent (`tool_output_delta` on the `subagent` tool call), keep the lifecycle
in event metadata.

## The gaps, ranked by integration cost

1. **Interrupt (gap #1) — must solve for v1.** Valuz interrupts sessions
   routinely (user stop, egress switch, task rework). Workaround: kill the
   subprocess and mark the turn interrupted (deepagents-style hard stop);
   persisted checkpoints keep everything up to the last committed event.
   Proper fix: a `session/cancel` method on the JSON-RPC server — both the ACP
   server and the Web BFF wire already implement it against the same in-core
   handle (the web one is literally
   `agent.cancel({kind: 'user'}, {keepInbox: true})`, verified live: cancel +
   a queued "continue" prompt resumes the same turn context), so this is a
   small plugin change (upstreamable).
2. **Cross-process resume (gap #2) — must solve for v1.** Kernel restarts and
   runtime-cache eviction are normal here. Workarounds, in order of fidelity:
   (a) fresh native session id per process + history replay in the first
   prompt (what deepagents-style fallback continuation does — we already have
   `build_user_prompt` conventions for it); (b) custom server plugin that
   `load()`s the persisted log before agent creation — the web wire's
   `createApiRemoteAgentResolver` (`packages/api/remotes`) already does
   exactly this (load header + events → recompose the agent) and is the
   reference to reuse. (b) is the real fix and is upstream-friendly.
3. **Approvals (gap #3) — acceptable to defer.** v1 ships with
   `available_decisions = ()` and unattended tools, gated by the session's tool
   policy (dsh has sandbox/approval-policy plugins for defense in depth; the
   deployment composes which tools exist at all). The transport already
   supports server→client requests and the Python client has the responder
   surface — wiring `ctx.approval` to it is the designed-for extension.
4. **Fork (gap #4) — defer.** `fork_session` raises `NotImplementedError`
   (the port sanctions this); event `seq` is the anchor — the web wire's
   `session.fork {atSeq}` confirms it as the native fork boundary.
5. **Steering — defer.** The web wire's `session.prompt` distinguishes
   `mode: "queue" | "steer"` (`agent.followup()` vs `agent.steer()`); the SDK
   wire's `session/prompt` is queue-only. Our orchestrator doesn't steer
   mid-turn today.

## Equipment path (tools/skills/MCP)

- **Valuz harness tools** (dispatch / orchestration / memory / toolkit MCP at
  `/_internal/mcp/toolkit/{base,lead}`): dsh's `dsh-mcp-client` plugin speaks
  **streamable-http with headers** and registers tools as
  `mcp__<server>__<tool>` — the exact shape our other runtimes consume. One
  cordis row per MCP server, generated into the per-session composition by the
  adapter (from `session.mcp_servers`).
- **Skills**: dsh discovers filesystem skills from project/user/custom roots.
  Point the custom roots at our per-session materialized skills dir
  (`skills_materialize.py` output), disable user-root discovery to avoid
  leaking `~/.claude/skills` (verified leak in the default composition).
- **System prompt**: agent-spine `persona` config (env `DSH_SYSTEM_PROMPT` in
  the examples composition) ← `system_prompt_builder`.
- **max_input_tokens / compaction**: dsh `compaction-basic`
  (`thresholdRatio` / `retainRatio` / `maxTokens`) — derive from
  `ModelSettings.max_input_tokens` like the other three runtimes do.

## Distribution — solved: vendored npm closure

**Landed** (`backend/vendor/dsh-runtime/`): Valuz owns its own deploy-root
manifest — the dsh plugin packages are published on npm (pin the coherent
`0.1.0-rc.x` wave; the `latest` dist-tag points at a stale wave whose
`dsh-bash-env` peer was renamed away, so never resolve by tag). Only pins +
lockfile are committed; `npm ci` fetches the tree at build
(`scripts/vendor-dsh-runtime.sh`), `build-desktop.sh` stages it into
`libexec/dsh-runtime`, and the packaged app runs `packaged-bin.js` under its
own Electron binary as plain Node (`VALUZ_DSH_RUNTIME_ENTRY` +
`VALUZ_NODE_PATH` + `VALUZ_NODE_IS_ELECTRON=1` — the chrome-devtools-mcp
pattern; Electron 36's embedded Node 22.19.0 exactly meets dsh's `^22.19`
floor, watch that coupling on Electron upgrades). Because we own the
manifest, the closure includes `dsh-mcp-client` (which the upstream
runtime-bin closure lacks) — `packaged-bin` resolves bare plugins from its
own installed tree, so the composition file lives in a temp dir.

Launch resolution: `VALUZ_DSH_RUNTIME_BIN` (exe override) →
`VALUZ_DSH_RUNTIME_ENTRY` (staged closure) → dev auto-detect of
`backend/vendor/dsh-runtime` → `VALUZ_DSH_ROOT` (source checkout,
contributor carrier).

One behavior the fast carrier exposed: dsh registers MCP tools
asynchronously with no wire readiness signal, so a prompt fired right after
spawn assembles its schemas before `tools/list` lands — the first turn
lacks every `mcp__*` tool. The adapter waits a bounded cold-start grace
(`VALUZ_DSH_MCP_READY_GRACE_SEC`, default 3s, only when the session carries
MCP servers); a wire-level readiness signal is upstream work.

The upstream `deepseek-harness-runtime-bin` wheel (PyPI, macOS arm64 +
linux x64/arm64, no mac x64 / Windows) remains a later zero-maintenance
alternative once its closure gains `dsh-mcp-client`.

## Upstream threads (filed 2026-08-14)

The upstream repo accepts feedback via GitHub Discussions only (issues/PRs
closed on the public mirror). Filed for each wire/closure gap:

- [#1238](https://github.com/deepseek-ai/deepseek-harness/discussions/1238) —
  `session/cancel` on the SDK wire (deletes our kill-interrupt workaround)
- [#1239](https://github.com/deepseek-ai/deepseek-harness/discussions/1239) —
  MCP tool-registration readiness signal (deletes the cold-start grace)
- [#1240](https://github.com/deepseek-ai/deepseek-harness/discussions/1240) —
  add `dsh-mcp-client` to the runtime-bin deploy root (makes official
  wheels usable; our vendored closure becomes optional)
- [#1241](https://github.com/deepseek-ai/deepseek-harness/discussions/1241) —
  stderr log exporter for stdout-is-protocol compositions
- [#1242](https://github.com/deepseek-ai/deepseek-harness/discussions/1242) —
  npm `latest` dist-tag points at the broken pre-rename wave
- [#712](https://github.com/deepseek-ai/deepseek-harness/discussions/712)
  (existing, commented) — persisted-session resume / id collision (deletes
  the transcript-sidecar replay)
- [#1007](https://github.com/deepseek-ai/deepseek-harness/discussions/1007)
  (existing, commented) — `dsh-plugin-langfuse`, a community
  session-telemetry backend exporting the ledger as OTel **trace** trees to
  Langfuse (the official backend is OTLP logs, which Langfuse cannot
  ingest). Same `0.1.0-rc.6` wave as our vendored closure — one manifest
  line to evaluate. Our comment asks for host-trace correlation /
  parent-context nesting and +1s the multi-sink seam evolution; this is the
  research path for full-fidelity dsh telemetry in Langfuse alongside the
  kernel's turn-skeleton traces (`EVENT_TRACED_RUNTIMES`).

## Recommended sequence

1. **Spike** (no product surface): async port of `HarnessClient` +
   `DeepSeekHarnessRuntime` with run/prepare/close + event mapping; kill-based
   interrupt; fresh-session-per-process with replay continuation; unattended
   tools via per-session cordis + toolkit MCP rows. This is shippable behind
   an availability gate.
   **Landed** (branch `feat/deepseek-harness-runtime`): kernel adapter under
   `backend/kernel/src/runtimes/deepseek_harness/` (asyncio NDJSON client +
   event mapper + composition generator + RuntimePort impl), factory /
   availability / fork-map / route-guard wiring, kernel migration `0004`
   (CHECK constraint), host registry/resolver/model-options plumbing
   (`deepseek_harness` initially derived only for the `deepseek` provider
   kind; a follow-up widened it to protocol-scoped — any non-subscription
   channel speaking chat-completions, mirroring how codex derives on the
   Responses wire. The dsh adapter posts plain
   `${base_url}/chat/completions` and honors the channel's endpoint via
   `DEEPSEEK_BASE_URL`; the host materializes each kind's default endpoint
   for dsh sessions and the kernel factory rejects an empty `base_url`,
   because dsh's own empty-endpoint fallback is DeepSeek's public API),
   frontend enum/label/auto-review-gate updates, and a fake-server test
   suite (`backend/tests/runtimes/test_dsh_*`). Dev availability:
   `VALUZ_DSH_ROOT=<checkout>` (source mode) or `VALUZ_DSH_RUNTIME_BIN=<exe>`.
2. **Server plugin work** (upstream PRs or a small carried bundle):
   `session/cancel`, persisted-log resume on first prompt, then approvals over
   server→client requests. Each one deletes a workaround from step 1.
3. **Parity extras**: fork by event seq, plan-mode mapping, jobs/workflow
   surfaces.
