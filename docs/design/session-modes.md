# Session Modes — Design

> Kernel-level session working modes (`default` / `plan` / `goal`), one
> cross-runtime contract with per-runtime native lowering. Same lever
> family as `permission_mode` / `effort`.
>
> This document is the valuz-oss home of the design the kernel code has
> referenced since the session-modes port (`kernel/src/core/events.py`,
> `prompt_builder.py`, `claude_agent/runtime.py`, `app/schemas.py`,
> `event_sse_adapter.py` all cite it). §1–§5 describe the kernel
> contract as implemented; §6 documents the host + product surface added
> by the plan-mode feature; §7 records the per-runtime roadmap (codex
> native lowering, dsh plugin composition).

---

## 1. Contract

### `Session.mode`

```python
# kernel/src/core/types.py
mode: Literal["default", "plan", "goal"] = "default"
```

- **`plan`** — the runtime plans before touching anything. Claude lowers
  to the SDK's `permissionMode="plan"`; the model proposes a plan via the
  `ExitPlanMode` tool, which parks as an approval card.
- **`goal`** — the runtime loops until a goal condition is met. Used by
  the task subsystem for lead/member sessions (`agent_resolver` stamps
  `mode="goal"` at creation; `finalization` resets it).
- No `mode_payload`: the goal condition travels in the next user message
  (wrapped `/goal <text>` by the kernel), not in a separate field.

### Kernel API

- `POST {KERNEL_API_PREFIX}/v1/sessions/{id}/mode` — validates (400 for
  `deepagents` on any non-default mode, and for `deepseek_harness` on
  `goal` — dsh has plan since slice 3), writes, and
  emits `mode_changed{mode, by: "user"}` on a real transition
  (idempotent on same-mode re-set). Direct `plan ↔ goal` transitions are
  allowed — runtime reconcile composes independent exit + entry branches
  in one pass.
- `CreateSessionRequest.mode` exists on the kernel wire (the task path
  uses it) but is **not** exposed on the host create route — one
  kernel-validated write path (`POST /mode`) keeps the runtime check in
  one place.

### Events

- `mode_changed` `{mode, by: "user" | "runtime"}` — `by: "runtime"`
  covers runtime-initiated exits (approved plan, completed goal).
- `plan_update` `{plan: [{step, status}], explanation?}` — codex's
  structured TODO-checklist snapshots (`turn/plan/updated`), status
  normalized to snake_case. NOTE: this is the `update_plan` checklist
  tool, **not** the plan-mode proposal channel (§7 codex).

### Mid-turn concurrency rule

A turn holds the `Session` object by reference. Just before the
end-of-turn save, the orchestrator decides whose `session.mode` wins: if
the runtime emitted `mode_changed{by: "runtime"}` during the turn, the
in-memory value wins; otherwise the disk value wins (honors a concurrent
`POST /mode` from the user). Only `mode` is reconciled this way.

## 2. Per-runtime lowering (as implemented)

| Cell | Enter | Exit |
|------|-------|------|
| Claude + plan | **Typed mutator** `client.set_permission_mode("plan")` in `_reconcile_session_levers`. The `/plan` slash is interactive-CLI-only — through the SDK it returns "isn't available in this environment" (spike-confirmed; see §5). | (a) user: `set_permission_mode(PERMISSION_MAP[session.permission_mode])`; (b) model: `ExitPlanMode {plan}` → approval subject `exit_plan_mode` (always parks, V1 verbs, no approve_for_session) → on approve `_on_exit_plan_mode_approved` lifts the SDK gate **itself** (the Allow does not), flips `session.mode`, emits `mode_changed{by:"runtime"}`, returns Allow so the model executes the plan in the same turn. |
| Claude + goal | Wrap: next non-slash user message becomes `/goal <text>` (`wrap_for_mode`). | `client.query("/goal clear")`; auto-exit detected by a bare `/goal` status probe after `ResultMessage` ("No goal set." → flip + `mode_changed{by:"runtime"}`). |
| Codex + plan | Wrap `/plan <text>` per turn (**prompt-level only** — the app-server turn input does not parse slashes; native lowering is §7). | Stop wrapping. |
| Codex + goal | Wrap `/goal <text>`; codex-core auto-continues the thread goal. | `thread/goal/clear` via the SDK's raw JSON-RPC escape hatch (camelCase params); auto-exit via the `thread/goal/cleared` notification. |
| dsh + plan | Composition mounts the plan plugin set; the `valuz-dsh-kernel-bridge` plugin converges dsh plan state to `session.mode` at the first pre-step (§7). Respawn-on-drift covers between-turn toggles. | (a) user: chip off → PATCH → respawn with `planActive: false`; (b) model: `exit_plan_mode {plan}` → `ctx.userQuestions` → HTTP bridge → approval subject `exit_plan_mode` (always parks, V1 verbs) → approve resolves the review with the intent's approve label, the tool returns `{approved: true}`, dsh flips `plan/mode` → `mode_changed{by:"runtime"}`, and execution continues the same turn. |
| dsh + goal | 400 at the kernel route (no goal lowering). | n/a |
| deepagents | 400 at the kernel route; `wrap_for_mode` skips it; UI hides the toggle. | n/a |

`wrap_for_mode(text, mode, runtime_provider)`
(`kernel/src/core/prompt_builder.py`) is the single wrap point: no wrap
for `default`, for slash-prefixed input, for deepagents/dsh, or for
Claude+plan (typed-mutator cell). The **wrapped** text is what persists
on the `Message` row.

## 3. Relationship to sibling levers

- `permission_mode` — during Claude plan the adapter overrides the SDK
  permissionMode to `"plan"`; PATCHes while in plan are cached and
  applied on exit. Codex/deepagents unaffected.
- `effort` — orthogonal.
- `interrupt` — cancels the turn, does **not** exit the mode.
- Approvals — Claude plan gates tool execution at the SDK, so ordinary
  approvals don't fire; `AskUserQuestion` (subject
  `clarifying_questions`) and `ExitPlanMode` (subject `exit_plan_mode`)
  still park, both exempt from `approve_for_session`.

## 4. Host + product surface (plan-mode feature)

### Host API (`api/openapi.yaml`)

- `SessionListItem.mode` (list + detail via allOf) — the composer chip
  hydrates without a second fetch.
- `PATCH /v1/sessions/{session_id}/mode` `{mode}` → thin façade over the
  kernel route (`SessionService.set_session_mode` →
  `kernel_client.set_mode`); kernel-shaped 400s re-surface verbatim.
- SSE: `mode_changed` → `session.mode_changed`, `plan_update` →
  `session.plan_update` (`adapters/event_sse_adapter.py`).

### Product UX (decided 2026-08)

- **Entry**: the composer's `+` menu gains a "Plan mode" toggle entry
  (below the attach group — mirrors the codex composer; Goal will join
  the same group later). Gated per-runtime by
  `supportsPlanMode(runtime)` (`@valuz/shared`, `PLAN_MODE_RUNTIMES`).
- **Active state**: an orange chip (warning tokens) after the
  permission-mode picker: `⍾ Plan ×` — click to turn off. Placeholder
  switches to "描述任务，先生成计划…".
- **Pre-session**: the toggle stages locally; the send path PATCHes
  `/mode` onto the freshly created session **before** the first message
  (awaited — the first turn's reconcile must see plan).
- **Live session**: toggle PATCHes directly. `session.mode_changed`
  frames (both `by` values) patch the session row in
  `useSessionSubscription`, which reconciles the chip — a
  runtime-driven exit (approved plan) flips it off without a refetch.
- **Approval verbs**: the `exit_plan_mode` card renders
  "批准并开始执行" / "继续规划" (product decision: one unified
  approve-and-run button across runtimes; reject = keep planning, the
  feedback text goes back to the model).

## 5. Evidence (upstream spikes)

Captured in the agent-harness reference tree
(`~/project/agent-harness/docs/references/`):

- `claude-plan-spike/` — `/plan` absent from the SDK `system/init`
  commands list (`goal` present); `set_permission_mode("plan")` works
  from every launch permission_mode; regression: the SDK does NOT lift
  plan permissionMode on ExitPlanMode-Allow — the host must call
  `set_permission_mode` itself.
- `claude-goal-spike/` — `/goal` is one opaque `query()` → 1
  ResultMessage; continuation marked by "Stop hook feedback:" user
  messages; bare `/goal` is a local status query (no tokens).
- `codex-goal-spike/` — `thread/goal/*` protocol, `goals_1.sqlite`
  persistence, model self-report via `update_goal`; vendored-SDK typed
  wrappers absent → raw `request()` with camelCase params.

## 6. Codex native plan (slice 2 — implemented)

Against codex-cli 0.144.4 (bundled) / `openai-codex` 0.144.4; wire
shapes verified with `codex app-server generate-json-schema
--experimental`:

- **Enable**: while `session.mode == "plan"` every `turn/start` carries
  `collaborationMode: {mode: "plan", settings: {model,
  reasoning_effort, developer_instructions: null}}` — sent as a **raw
  dict** merged after the typed params' `model_dump` (the generated
  `TurnStartParams` predates the experimental field and pydantic
  silently drops unknown kwargs; `test_codex_plan_mode` pins both the
  drop and the merge). `settings` keys are snake_case inside the
  camelCase protocol. `settings.model` is required — a codex plan turn
  without a session model fails with an actionable error.
  `developer_instructions: null` selects codex's built-in Plan Mode
  prompt (the behavioral contract), which REPLACES the thread's
  developer instructions — the session's own instructions are
  suspended during plan turns.
- **Sticky exit**: `collaborationMode` persists server-side, so the
  runtime records `metadata["codex_collab_plan_active"]` after a
  successful plan `turn/start` (metadata, not an instance flag —
  survives rebuild/restart) and the FIRST non-plan turn sends an
  explicit `mode: "default"` with `developer_instructions =
  session.instructions` (restoring the persona), then clears the
  marker.
- **Hard read-only**: plan turns force `sandbox_policy: readOnly`
  (codex's no-mutation rule is prompt-level only).
- **Plan output**: `item/completed` with `item.type == "plan"`
  (`{id, text}`, authoritative) → kernel `plan_proposed{plan}` → SSE
  `session.plan_proposed` → the frontend's `PlanProposalCard`. The
  duplicate `<proposed_plan>` block is stripped from the sibling
  `agentMessage` (a message that was only the block emits nothing);
  `item/plan/delta` is folded (snapshot rendering). `turn/plan/updated`
  (TODO checklist) is unrelated and hard-blocked inside plan mode.
- **Approval**: no protocol round-trip exists. "批准并开始执行" is
  product-level — the card's button PATCHes mode → default and sends an
  execution turn; "继续规划" is simply typing feedback (the next plan
  turn revises). The button rides only the NEWEST proposal while the
  session is still in plan mode.
- **`request_user_input`**: plan mode is the only mode enabling this
  tool and the built-in prompt pushes it hard. The codex approval
  bridge maps `item/tool/requestUserInput` → subject
  `clarifying_questions` (the same card Claude's `AskUserQuestion`
  renders), ALWAYS parks (the `full_access` short-circuit must not
  fabricate a malformed `{"decision"}` reply), and answers return as
  the `ToolRequestUserInputResponse` envelope
  `{"answers": {<question id>: {"answers": [...]}}}` — remapped from
  the card's question-text-keyed answers. Reject/timeout/interrupt
  reply with the empty envelope.
- `wrap_for_mode` no longer prefixes `/plan ` for codex (plan lowering
  is protocol-level on every runtime that has it).

## 7. dsh native plan (slice 3 — implemented)

dsh ships plan as a plugin (`@deepseek-ai/dsh-plan-mode`, pinned
`0.1.0-rc.6`; the review flow is byte-identical at `0.1.1-rc.2`): a
mandatory `section` prompt (`plan:policy`, order 50, fail-fast on
empty/unknown config) + an always-registered `exit_plan_mode` tool that
reviews through `ctx.userQuestions.ask()` with
`intent {kind: "plan-review", approve: "Approve"}`. Nothing crosses the
SDK JSON-RPC wire (still exactly `session/prompt` / `session/created` /
`session/event` / `agent/status` / `subagent/end` at `0.1.1-rc.2`) —
both plan state and user questions are in-process plugin seams, which
the dsh Web host wires in-process too. Our lowering:

- **Composition** (`deepseek_harness/composition.py`): on a
  plan-capable closure (probed at launch resolution — all four plugins
  present in `node_modules`, so a composition never references a bare
  plugin it can't boot), ALWAYS compose `dsh-user-questions`,
  `dsh-tool-ask-user`, `dsh-plan-mode` (`section` =
  `PLAN_MODE_SECTION`, the dsh spelling of `PLAN_MODE_DISCIPLINE`) and
  the Valuz-owned **`valuz-dsh-kernel-bridge`** plugin
  (`backend/vendor/dsh-runtime/valuz-plugins/kernel-bridge`, a `file:`
  dep installed as a real copy via `.npmrc install-links=true` so the
  staged desktop closure stays self-contained). Never gate the rows on
  `session.mode` — the tool catalog stays stable and plan state is the
  per-session durable `plan/mode` log value (default inactive).
- **Entry/exit**: the bridge plugin converges dsh plan state to its
  baked `planActive` (from `session.mode`) ONCE per session on the
  first `agent/pre-step` — the plan-mode controller applies the pending
  selection in that same pre-step, so the first request already
  carries the section. Converge-once is load-bearing: an approved
  `exit_plan_mode` flips dsh state mid-turn and a per-step converge
  would re-enter plan. The runtime tracks the dsh-side state from wire
  `plan/mode` events and respawns only on real drift (user toggled the
  chip between turns; there is no live wire flip) — an approved exit
  flips both sides, so no respawn there.
- **Review = same card as Claude**: the bridge plugin registers the
  `ctx.userQuestions` provider, forwarding `ask()` over HTTP to
  `{KERNEL_API_PREFIX}/v1/dsh/user-questions/{token}` (per-spawn random
  token IS the credential, PTC's model; bearer middleware exempts the
  path). The kernel parks it as `requires_action`: `plan-review` intent
  → subject `exit_plan_mode` (payload `{plan}` from the question's
  `detail`, V1 verbs), anything else → `clarifying_questions` with the
  synthetic `AskUserQuestion` anchor pair (codex pattern; the raw
  `ask_user_question` tool pair is mapper-suppressed). `submit_action`
  translates the verb into dsh's answers envelope
  (`approve` → select the intent's approve label → `{approved: true}`
  → **same-turn native execution**; `reject` + feedback → `custom`
  (single-select: custom overrides selected) → the tool fails with
  "their feedback: ..." and the model revises; clarifying `answer` →
  per-question `{id, selected, custom?}` remap) and the plugin's
  long-poll (25 s GETs, 30 s server ceiling) releases the tool call.
- **Event mapping**: `plan/mode {active}` → `mode_changed{by:
  "runtime"}` → the orchestrator's write-through persists it — kernel
  `session.mode` stays authoritative (dsh logs the event lazily at the
  next accepted pre-step and can lose it on process death).
- **Carriers**: vendored + packaged-entry closures are plan-capable;
  the `VALUZ_DSH_RUNTIME_BIN` single-file and `VALUZ_DSH_ROOT` source
  carriers are not (no `valuz-dsh-kernel-bridge` in their plugin
  resolution) and degrade to no-plan compositions.

Upstream asks that would simplify this: a `session/plan` JSON-RPC
method, and a wire-level userQuestions channel.

## 8. Explicit non-goals

- deepagents plan/goal polyfill (prompt-level emulation) — only on real
  user demand.
- Goal-mode UI in the composer — the task subsystem drives goal today;
  the `+` menu is designed to host a Goal entry later.
- Exposing `permission_mode="plan"` as a standalone permission tier.
