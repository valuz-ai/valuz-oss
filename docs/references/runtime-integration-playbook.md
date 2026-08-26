# Runtime Integration Playbook

> How to explore and integrate a new agent runtime into the kernel, distilled
> from the DeepSeek Harness integration (2026-08 — see
> [deepseek-harness/](deepseek-harness/README.md) for the worked example, and
> `feat/deepseek-harness-runtime` for the full commit trail). The checklists
> are the repeatable part; the **Field lessons** are the bugs that unit tests
> and upstream docs did not catch — read those before trusting anything.

The kernel's runtime contract is
`backend/kernel/src/core/runtime_port.py` (`RuntimePort`). Everything below is
in service of implementing that protocol honestly for a new engine.

---

## Phase 0 — Exploration (before any code)

Goal: know the target's **wire contract from evidence, not docs**. Upstream
docs describe intent; only captured frames describe behavior.

1. **Map the architecture**: find the server/SDK split (codex app-server ↔
   SDK; dsh `dsh-jsonrpc-agent` ↔ `deepseek-harness-sdk`), the process model
   (per-session subprocess? shared server?), and where session state lives.
2. **Run it for real** with a real API key. Write small scripts that capture
   the raw wire (every notification/event, verbatim JSONL). Sanitize and
   commit them under `docs/references/<runtime>/examples/` — they become the
   integration's ground truth and the test fixtures' template.
3. **Probe the hard edges empirically** — every one of these diverged from
   assumptions at least once during the dsh work:
   - multi-turn continuity in one process, and **across process restarts**
   - interrupt / cancel (does the wire have it at all?)
   - error paths (bad model, dead endpoint) and every stop-reason kind
   - streaming chunk vocabulary (text / thinking / tool-input deltas)
   - tool call/result payload shapes (string-encoded args? content lists?)
   - **usage semantics** — see Field lesson 6 before mapping any numbers
   - sub-agent lifecycle, if the engine has one
4. **Survey ALL surfaces, not just the official SDK.** dsh's SDK wire lacked
   cancel/resume/fork — but its ACP server and web-UI gateway had all three,
   which proved the capabilities were in-core and thin to expose, and gave us
   reference implementations to cite in upstream requests. DevTools capture
   of the vendor's own UI is a legitimate and productive exploration tool.
5. **Deliverable**: `docs/references/<runtime>/` with an overview, the
   SDK/wire notes (verified vocabulary, not paraphrased docs), a
   `RuntimePort` gap analysis, runnable scripts, and sanitized transcripts.

## Phase 1 — Gap analysis (the design document)

Write the mapping **member by member** against `RuntimePort` before coding:

- `run` / `prepare` / `close` — usually clean; note the cold-start boundary.
- `interrupt` — if the wire has no cancel, the v1 stance is process-kill +
  documented continuity cost.
- `supports_native_continuation` — answer it for BOTH "same live process" and
  "recreated runtime instance"; the honest answer gates task coverage.
- `fork_session` / `consume_turn_anchor` — `NotImplementedError` is sanctioned;
  still decide the native anchor now and stamp it (dsh: event `seq`).
- `submit_action` / `approval_rule_matcher` — if no approval wire, reject
  `auto_review` at session create and advertise no decisions.
- **Event mapping table** — dsh events → kernel `OutboundEventType`, with the
  exact `data` shapes the frontend renders
  (`tool_use {id, name, input}`, `tool_result {id, content, is_error}`,
  `todo_update {todos}`, `usage_update` flat fields + `model_usage`, …). Copy
  the shapes from an existing mapper (`runtimes/codex/event_mapper.py` is the
  cleanest template), never from memory.
- For every wire gap: record the **v1 workaround AND its retirement
  condition** (usually "upstream lands X" — Phase 5 files that X).

## Phase 2 — Integration checklist

The enum `RuntimeProvider` is duplicated across the stack with only comments
enforcing sync. The dsh integration found **eight** copies — and the eighth
(a store read-path coercer) was found only by a field failure. Sweep with
`grep -rn "claude_agent.*codex.*deepagents"` across backend + frontend AND
audit every `_validate_*` / defensive-default helper by hand.

Contract first (`api/openapi.yaml`): all runtime enums (agent create/patch,
member create, Session, RuntimeListItem, SystemStatus, SessionCreateRequest)
plus the per-runtime protocol table prose.

Kernel:

- `src/core/types.py` `RuntimeProvider` + `app/schemas.py` duplicate
- `src/runtimes/<name>/` adapter package — the shape that worked:
  `client` (transport) / `event_mapper` (pure, unit-testable) /
  `composition`-or-config builder / `runtime` (the port impl)
- `factory.py` dispatch + `ALLOWED_PROTOCOLS_BY_RUNTIME`
- `availability.py` probe (launchable? actionable reason string)
- `session_fork.py` anchor + thread-id key maps
- `prompt_builder.wrap_for_mode` literal + plan/goal stance
- `app/routes/sessions.py` guards: model/provider required? `auto_review`?
  non-default modes?
- **Alembic migration for `ck_sessions_runtime_provider`** (batch rebuild;
  verify upgrade AND downgrade on a real SQLite file) — and see Field
  lesson 1 for the durable mirror
- `src/adapters/sqlalchemy_store/converters.py` — now derived from the
  Literal via `get_args`, so this copy can no longer drift; keep it that way
- `runtimes/interruption.py` — register the new transport's death exception
  or mid-turn process loss is mis-stamped `execution_error`

Host: `adapters/runtime_registry.py`, `adapters/provider_resolver.py` (three
spots — Literal, valid set, default-protocol map; a partial update KeyErrors),
`modules/settings/model_options.py` derivation rule (decide WHICH channels
offer the runtime — dsh derives only for the `deepseek` provider kind),
`modules/settings/preferences.py`, `modules/system/service.py`,
`api/routes/agents.py`, `integrations/tools_agent_proposal.py` (model-visible
prompt text!), `modules/sessions/service.py` permission guards.

Frontend: `shared/types/system.ts`, `core/api/runtime-protocols.ts`,
`core/hooks/use-composer-providers.ts`, `core/api/runtime-compat.ts`
(display name Record is exhaustive — typecheck catches it), Composer
`auto_review` gate, `useTitleActions.ts` `FORKABLE_RUNTIMES` (only when fork
is real). i18n: any runtime-specific string a new runtime now also triggers
must become runtime-neutral.

Tests — the pattern that carried the whole integration: a **fake server
speaking the real wire protocol** (`tests/runtimes/dsh_fake_server.py`),
driven through the actual adapter via a launch-spec override. Cover: full
turn, error turn, kill-interrupt, replay-continuation, oversized frames,
capability-drift respawn. Then update the pinned enum tests
(runtime_registry, runtimes_router, model_options, agent_runtime_validation,
availability) and add store round-trip + factory dispatch cases.

## Phase 3 — Field verification ladder

Unit tests prove the adapter; only the ladder below proves the integration.
Climb it in order — every dsh field bug lived between two rungs:

1. Fake-server unit suite (fast, in CI).
2. Adapter-level E2E against the real API (direct `RuntimePort` calls).
3. **Product-API repro backend**: scratch `VALUZ_DATA_DIR`, real provider row,
   real `POST /v1/sessions` + `/messages` — and set `VALUZ_BACKEND_BASE_URL`
   to the actual port or every internal MCP URL silently points at :8000.
4. Desktop/UI test by the maintainer (surfaces env truths no repro has —
   e.g. the user's exported `VALUZ_DATA_DIR`).
5. Task lead/member orchestration end-to-end (exercises toolkit MCP, parallel
   dispatch, review loop).

Verify quality gates **by delta** against the known-RED baseline (main is not
green); a migration additionally gets a manual upgrade/downgrade run against
a copy of a real data file.

### Field lessons (each cost a debugging session; none was in any doc)

1. **The durable mirror does not migrate.** Kernel alembic touches only the
   execution store; the DataService durable copy is provisioned by
   `create_all(checkfirst)`, which never ALTERs. A widened CHECK constraint
   must also be reconciled on the durable (`_ensure_durable_schema`) or
   mirror writes are silently dropped and every host read of the row 404s.
2. **Read-path coercers eat new enum members.** A defensive
   `value not in KNOWN → default` helper rewrote `deepseek_harness` to
   `deepagents` on every load — column AND embedded snapshot — while the
   create response was honest. Derive such sets from the canonical Literal.
3. **Size stream readers in megabytes.** asyncio's 64 KiB `readline` default
   killed the reader on a normal `request/header` frame (full prompt + tool
   schemas) and was misdiagnosed as the subprocess dying.
4. **Bake nothing that drifts.** Anything read once at spawn (MCP credentials,
   persona, effort) goes stale against per-turn re-stamps. Fingerprint the
   baked state and respawn on drift — and read live values from the
   `Session`, not from constructor snapshots.
5. **Fast paths expose races slow paths mask.** Source-mode tsx boot (~8s)
   always won the async MCP tool-registration race; the packaged carrier
   (~1s) lost it deterministically — first turn had zero `mcp__*` tools, no
   error anywhere. When there is no readiness signal, add a bounded grace
   and file the upstream request.
6. **Usage token semantics differ per engine.** codex: cached input is a
   *subset* of input. dsh: input and cache-read are *disjoint* (input is
   already the uncached part). Applying the wrong model produced
   "0 input / 100% cache hit" in the UI. Read the vendor's `mapUsage`-style
   source, then verify with a warm-turn real-API run before shipping numbers.
7. **A silent engine is a misconfigured engine.** If the engine's stdout is
   the protocol and its logging can't target stderr, plugin failures vanish.
   Check the diagnostics story in Phase 0 and budget for flying blind.

## Phase 4 — Distribution

Decide the carrier ladder early; it shapes availability UX:

`explicit env override` → `packaged/staged artifact` → `dev auto-detect` →
`source checkout (contributor)` — each tier probed by `availability.py` with
an actionable reason string.

For Node-based runtimes the proven pattern is the **vendored npm closure**
(`backend/vendor/<runtime>/{package.json,package-lock.json}`, `npm ci` at
build, staged into libexec, run under Electron-as-node — the
chrome-devtools-mcp / dsh-runtime pattern). Owning the deploy-root manifest
means owning the plugin set — that is how dsh got `dsh-mcp-client` before
upstream shipped it. Pin one coherent release wave; pre-release registries
drift (dsh's npm `latest` tag pointed at a wave with a renamed-away peer).
Watch pinned floors: Electron 36's embedded Node is 22.19.0, exactly dsh's
minimum.

## Phase 5 — Close the loop upstream

Every workaround gets an upstream thread, and the gap analysis links each
workaround to the thread whose resolution retires it (see the "Upstream
threads" section in
[deepseek-harness/runtime-gap-analysis.md](deepseek-harness/runtime-gap-analysis.md)).
Write them from field evidence: exact repro, the in-tree precedent that
proves the ask is small (dsh's cancel existed on two other transports), and
what the consumer deletes when it lands. Check the vendor's actual feedback
channel first — dsh's public repo takes Discussions only — and search for
existing threads before filing (our resume gap was already reported; a
comment beats a duplicate).
