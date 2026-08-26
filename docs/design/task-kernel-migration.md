# Task → Kernel Migration

[中文版](task-kernel-migration.zh-CN.md)

> **Status: DEFERRED (2026-07-20).** The Task subsystem stays **host-resident
> for now** — this migration is not being executed, and the host task module
> should be treated as an end state, not a transitional one. The governing
> decisions (§2) remain the locked reference if the migration is ever revived.
> The pre-migration seams that already landed stand on their own merit and are
> kept regardless: `tasks/resolution.py` (host-knowledge session resolution,
> §5.1-shaped), `tasks/events.finalize_task` (composed terminal write), and
> `tasks/tools/gate.py` (pure tool-gate policy). What is **no longer planned**:
> moving the `valuz_task*` tables, running actors kernel-side, and serving the
> task MCP tools from the kernel.
>
> **Addendum 2026-07-28:** the module inventory below reflects the
> PRE-refactor tree. The host module has since evolved: `queries.py`
> merged into `service.py`, `health_monitor.py` into `recovery.py`;
> `launcher.py` (the single actor-launch primitive), `plan_commands.py`
> (the single authorized plan-write door) and `member_state.py` /
> `outcome.py` (pure domain) were added. If this migration is ever
> revived, inventory against the live tree, not this table.

> The Task subsystem moves **wholesale into the kernel**. Its tables become
> kernel-owned (unprefixed, `user_id` retained) and are persisted through the
> **DataService** exactly like `sessions`/`messages`/`events`. Its **actors**
> (lead + members, mailboxes, the live registry, recovery, the watchdog) run
> and are recovered **entirely kernel-side** — in-process or inside a sandbox.
> Its **built-in MCP tools** are served kernel-side. All coupling to the host's
> agent library / projects / providers is severed and replaced by **injected
> abstractions**, never an in-task query of a host table.
>
> This refactor is the **foundation** for the commercial goal: **session-granular
> sandbox start/stop/control for SaaS**, and the **actual** resolution of the
> host-process **actor-bloat** problem.
>
> Companion docs: [architecture.md](../architecture.md) (system topology),
> [data-service-architecture.md](data-service-architecture.md) (the data layer
> this reuses), [kernel-sandbox-deployment.md](kernel-sandbox-deployment.md)
> (sandbox provisioning), [task-attention-and-reliability.md](task-attention-and-reliability.md)
> (the reliability gaps this migration must not regress).

---

## 1. Goal & non-goals

### Why

Today the Task subsystem lives in the **host** process (`backend/valuz_agent/modules/tasks/`).
Every lead and every member is an `asyncio.create_task(run_actor_loop(...))` on
the **host's** single event loop, coordinated through in-memory singletons
(`LiveMemberRegistry`, `MailboxRegistry`, `TaskHealthMonitor`) and re-hydrated by
a host-boot sweep (`recover_active_tasks`).

In a multi-tenant SaaS host, **all tenants' task actors share one process**. That
state is memory-resident and non-shardable: it pins each user's live tasks to a
specific host instance, blocks horizontal scale-out, and lets one tenant's task
fan-out starve others. This is the **actor-bloat** problem.

The commercial target is **session-granular sandboxing**: each user's agent loop
(and its task actors) runs in its own start/stop-able sandbox, while the host
stays a stateless control + data plane. That is impossible while the task actors
live *in* the host. **Moving the Task subsystem into the kernel is the geometric
prerequisite** — it puts the actors on the sandbox side of the seam.

### In scope

- Task tables → kernel-owned, persisted via DataService (host-side durable store).
- Task actor lifecycle (spawn, coordinate, review, finish, **recover**, watchdog)
  → kernel-side.
- Task built-in MCP tools → served kernel-side.
- Task ↔ host coupling (agent library / project membership / providers / cwd /
  worktree / decision-inbox / notifications / memory) → severed and replaced by
  injected ports or event-driven host reaction.
- Full functional parity (§9) plus a defined regression surface (§10).

### Out of scope (this refactor)

- The sandbox provisioning mechanics themselves (owned by
  `kernel-sandbox-deployment.md`) — this refactor *consumes* them.
- Changing the plan/DAG semantics, the review loop, or the tool contract's
  *behavior* (only their *location* moves).
- Cloud driver (e2b / AGS) work — lives in the commercial overlay.

### The honest caveat

In the **in-process** kernel form (`make dev`), kernel and host are the *same*
process; moving actors in **does not** reduce in-process actor count there. The
bloat relief is realized **only** in the `kernel_mode=http` / per-session-sandbox
topology. This refactor makes that topology *possible*; the deployment topology
*delivers* the win. Both facts belong in the acceptance criteria so the outcome
is not later mis-measured.

---

## 2. Governing decisions (locked)

These are the constraints every section below obeys.

| # | Decision |
|---|----------|
| **D1** | Task tables move **into the kernel**. Keep the `user_id` column; adopt kernel naming — **drop the `valuz_*` prefix** (`valuz_task` → `tasks`, `valuz_task_event` → `task_events`, `valuz_task_session` → `task_sessions`). |
| **D2** | Task tables follow the **same treatment as the kernel's three tables**: persisted through the **DataService** to the host-side durable store (PG in SaaS), *whether or not* the kernel is sandboxed. **Query wiring therefore still terminates on the host.** |
| **D3** | The **actor must live kernel-side.** Its *entire* lifecycle — spawn, coordinate, review, finish, and **recovery** — runs in the kernel (or the sandbox). No task actor ever runs in the host process. |
| **D4** | Task's dependency on **agent / project** must be handled as **abstracted entry parameters** (injected ports / resolved specs). Task code **must not** query host-side tables internally. |
| **D5** | Task's built-in **MCP tools are served kernel-side.** When sandboxed, the sandbox **exposes an access address** for them — keeping "local persistence + DataService write-back to host" semantics identical to the kernel's own tables. |
| **D6** | Produce this design doc: full **functional coverage** after refactor + expected **pitfalls & regressions**. |
| **D7** | The north star is **SaaS session-granular sandbox start/stop/control + real actor-bloat resolution.** This task refactor is the **foundation**, not the finish line. |

---

## 3. Target architecture

### 3.1 What moves, what stays

The rule of thumb: **execution + persistence + tools move into the kernel;
host-owned *knowledge* (agents, projects, providers, secrets) stays on the host
and is reached only through an injected abstraction.**

| Current file (`backend/valuz_agent/modules/tasks/`) | Layer | Post-refactor home |
|---|---|---|
| `plan.py`, `task_state.py`, `provenance.py` | Domain (pure) | **Kernel** `src/tasks/domain/` — direct move, zero host coupling |
| `actor_runner.py`, `mailbox.py`, `live_member_registry.py` | Runtime (actors) | **Kernel** `src/tasks/runtime/` — **D3**, same event loop as `SessionOrchestrator` |
| `orchestrator.py`, `dispatcher.py`, `coordination.py`, `lifecycle.py`, `recovery.py`, `planning.py`, `messaging.py`, `queries.py`, `health_monitor.py`, `_session_build.py` | Services | **Kernel** `src/tasks/` — actor lifecycle + recovery + watchdog kernel-side (**D3**); host-resolution parts extracted to the resolver port (**D4**) |
| `models.py`, `datastore.py` | Persistence | **Kernel** storage + **DataService RPC** ops (**D1**, **D2**) — renamed, `user_id` kept |
| `tools/declarations.py`, `tools/handlers.py`, `dispatch_mcp.py` | Transport (tools) | **Kernel**-served MCP (**D5**); sandbox exposes the address |
| `../../api/routes/tasks.py` | Transport (HTTP) | **Host** public API, delegating to the kernel via new `KernelClient` task methods; SSE reads task events through the host query path (**D2**) |
| host services it calls (decision-inbox, notifications, memory scheduler) | Side effects | **Host**, driven **event-first** off task events (§8), with a minimal outbound port only where a synchronous callback is unavoidable |

### 3.2 The seam picture

```
┌───────────────────────────────────────────────────────────────────────┐
│  HOST (control + data plane, stateless w.r.t. task actors)             │
│                                                                        │
│  api/routes/tasks.py ──► KernelClient.task_* ──┐   (public HTTP + SSE) │
│                                                │                       │
│  MemberResolverPort  ◄─────── callback ────────┼──┐ (D4: agents /      │
│   (agent lib · projects · providers ·          │  │  projects /        │
│    system-prompt · cwd/worktree · display)     │  │  providers stay    │
│                                                │  │  host-owned)       │
│  DataService  /rpc/{op}  ◄──── write/read ─────┼──┼──┐ (D2: durable    │
│   (host SQLite ▸ or PG in SaaS)                │  │  │  store on host)  │
│                                                │  │  │                 │
│  decision-inbox · notifications · memory  ◄────┼──┼──┼── task events    │
│   (host reacts to task events; §8)             │  │  │                 │
└────────────────────────────────────────────────┼──┼──┼─────────────────┘
        in-process call  OR  HTTP/JWT (sandbox)   │  │  │
┌────────────────────────────────────────────────▼──▼──▼─────────────────┐
│  KERNEL  (in-process OR session-granular sandbox)                       │
│                                                                        │
│  SessionOrchestrator            TaskOrchestrator  (NEW, sibling)        │
│   sessions/messages/events       tasks/task_events/task_sessions        │
│                                                                        │
│  src/tasks/runtime/   actor_runner · mailbox · live_member_registry     │
│    ▸ lead + member actors (asyncio, ONE loop)   ── D3 ──                │
│    ▸ recovery sweep + health watchdog at KERNEL boot                    │
│  src/tasks/            dispatcher · coordination · lifecycle · planning  │
│  kernel MCP toolkit    dispatch · plan_task · review_subtask · finish …  │  ── D5
│    (served kernel-side; sandbox publishes its address)                 │
└────────────────────────────────────────────────────────────────────────┘
```

Three seams, all pre-existing patterns — **no new architectural primitive**:

1. **DataService** (`POST /rpc/{op}`) — task tables ride the exact mechanism the
   kernel's three tables already use (`data-service-architecture.md`). Two knobs
   (execution location, backend) unchanged; we only add task ops.
2. **MemberResolverPort** — a kernel→host callback, isomorphic to how a sandboxed
   kernel already calls back to DataService for storage and to the toolkit MCP
   server for tools. In-process it's a direct host object; sandboxed it's an
   HTTP/JWT callback to the host.
3. **KernelClient task methods** — the host's public HTTP routes delegate through
   the existing `KernelClient` protocol (both transports), extended 1:1 with the
   new kernel task API (the `InProcessKernelClient` path already *is* a direct
   call).

---

## 4. Data model in the kernel (D1, D2)

### 4.1 Renamed, owner-retained tables

Three tables move to kernel ownership. Columns are preserved verbatim except the
rename; `user_id` **stays** (kernel tables already carry `user_id` — owner is
stamped at the **store/DataService boundary from the verified JWT**, never lifted
into `ExecContext`, so this does **not** violate the "owner-agnostic kernel
runtime" rule).

| Was (`valuz_*`) | Now (kernel) | Keeps |
|---|---|---|
| `valuz_task` | `tasks` | `user_id`, `plan` (DAG JSON), `plan_version` (CAS), `status`, `trigger_*`, `metadata_` |
| `valuz_task_event` | `task_events` | `user_id`, per-`(user, task)` monotonic `sequence`, `type`/`actor`/`session_id`/`payload` |
| `valuz_task_session` | `task_sessions` | `user_id`, `session_id` (→ kernel `sessions.id`), `kind`, `subtask_key`, `result_manifest`, `run_dir` |

Alembic ownership moves from the host chain to the **kernel Alembic chain**. The
migration must be **reversible** (repo rule) and **data-preserving** — reuse the
`boot/kernel_db_colocate.py` precedent (backup → copy → verify) rather than a
drop-and-recreate.

### 4.2 DataService RPC extension (D2)

Add one `POST /rpc/{op}` op per new StorePort method, mirroring the existing
`save_session`/`load_session`/`append_event`/`get_events_after` shape:

```
save_task · load_task · list_tasks · list_active_tasks (system, cross-task)
append_task_event · get_task_events_after · get_task_events_window
save_task_session · load_task_session · list_task_sessions · update_task_session_by_session
```

Owner is derived from the **verified bearer token** (`_owner_dep`), never the
request body — identical to the kernel-table ops. On PG, the same
`install_rls_guc` per-transaction `SET LOCAL app.current_user_id` backstops owner
isolation at the DB. **The host durable store remains the single source of
truth; whether the kernel is sandboxed changes only the *transport* (in-process
call vs HTTP/JWT), never the write target** (D2).

### 4.3 Kernel-side dual-write, host-terminated queries (D2)

The three task tables are **dual-written kernel-side** — local kernel DB **plus**
the DataService durable store — exactly like `sessions`/`messages`/`events` under
`WriteThroughStore` (`authority="durable"`). This applies to the task-owned
tables (`tasks` / `task_events` / `task_sessions`), **not** to any host-side
project↔task linkage, which stays host-owned. The local buffer gives the
in-sandbox actor low-latency reads; the durable copy on the host is the wire
source of truth.

The public read/stream path stays on the host:

- `GET /v1/tasks`, `GET /v1/tasks/{id}`, `GET /v1/tasks/{id}/events`,
  `.../events/stream` remain **host** routes.
- They read task rows/events through the **host durable store** (the DataService
  write target) — the host does **not** reach into the kernel process for reads.
  The current 500 ms DB-poll SSE (`_iter_task_events_sse`) keeps working against
  `task_events` unchanged (only the table name changes).
- The **cross-store seq caveat applies by design.** As with `sessions`/`events`,
  the local buffer and the durable store carry divergent ids/counts (a constant
  offset = healthy dual-write); the **wire exposes the durable `sequence` only**,
  and cross-store identity is `event_uid`, not `seq`. Do not attempt to make the
  two stores' counts match.

---

## 5. Severing host coupling → injected abstractions (D4)

Today `build_member_session` (in `adapters/agent_resolver`) is the single funnel
through which task code reaches the agent library, project membership, providers,
capability (skills/MCP) resolution, and the system-prompt builder. Plus scattered
direct reads of `ProjectMemberDatastore` / `ProjectDatastore` / `ProviderDatastore`
and cwd/worktree helpers. **All of this is host knowledge the kernel must not
query.** D4 turns it into one injected port.

> **Not a tool.** `MemberResolverPort` is **internal plumbing**, invisible to the
> LLM. It is distinct from the kernel-served Task **MCP toolkit** (§6), which is
> the *agent-facing* surface (`dispatch`, `plan_task`, …). Flow: the lead agent
> calls the `dispatch` MCP tool → the tool **handler**, in code, calls
> `MemberResolverPort` to obtain a `ResolvedSession` → spawns the actor. The tool
> is the face shown to the agent; the resolver is the pipe the handler uses. In a
> sandbox the resolver rides its **own dedicated host `/rpc`-style endpoint**
> (DataService JWT model), **not** the toolkit MCP channel — resolution is
> data-shaped, not tool-shaped.

### 5.1 `MemberResolverPort` (host-implemented, kernel-called)

```
MemberResolverPort:
  resolve_member_session(project_id, agent_slug, goal, *, run_kind) -> ResolvedSession
      # AgentConfig snapshot + skills + mcp_servers + model/provider + system prompt + cwd
  resolve_lead_session(project_id, lead_agent_slug, ...) -> ResolvedSession
  resolve_display_name(agent_slug) -> str
  resolve_role_summary(agent_slug) -> str
  resolve_project_cwd(project_id) -> Cwd            # incl. worktree healing/snapshot
  credential_gap(resolved) -> Optional[Gap]         # oauth vs keyed provider check
```

- **In-process kernel:** the port is a thin host object wrapping today's
  `build_member_session` — behavior-identical, just relocated behind an interface.
- **Sandboxed kernel:** the port is an **HTTP/JWT callback to the host**
  (same trust model as DataService: sandbox holds token + URL, never the DB DSN
  or the agent library). This is the third leg of the existing "sandbox calls
  back to host" triangle (storage, tools, **resolution**).
- The task engine **receives a fully `ResolvedSession`** and never learns *how*
  it was resolved. `ProjectMemberDatastore` / `ProjectDatastore` /
  `ProviderDatastore` imports are **deleted** from task code (this also fixes the
  pre-existing cross-module datastore-boundary violations).

**§13 is the full P3 contract** — exact Protocol, `/rpc` wire, both transports,
the sync-invariant timing rule, error taxonomy, and the contract test. This
section is the overview; build from §13.

### 5.2 Resolution timing preserves the actor invariant

Critical: `LiveMemberRegistry` requires that **`create_task(spawn)` and
`add_member(...)` have no `await` between them** (else a racing `finish_task`
drops a just-spawned member). Therefore resolution (which *is* async / may be an
HTTP callback) must complete **before** the synchronous spawn block:

```
resolved = await resolver.resolve_member_session(...)   # async, may call host
# ── synchronous block, no await ──────────────────────
mailbox.register(lead); registry.add_member(task, member_id)
mailbox.register(member); asyncio.create_task(run_actor_loop(resolved))
# ─────────────────────────────────────────────────────
```

This keeps the entire spawn atomic **inside the kernel event loop** even when
resolution round-trips to the host — the load-bearing reason the *whole* actor
machine must move as one unit (D3) and cannot be split host/kernel.

### 5.3 cwd, worktree, filesystem

The kernel already manages the subtree under `project.cwd` and receives a
resolved cwd via `project_cwd(...)`; subrun directory allocation and worktree
healing become part of `resolve_project_cwd`/`resolve_member_session` outputs.
Sandbox path projection (`integrations/sandbox_runtime.py` /
`MountGrant.kernel_cwd`) is unchanged — the resolver returns the host path and
the existing projection layer stages it into the sandbox mount.

---

## 6. Tools served kernel-side (D5)

The lead-agent tool surface (`dispatch`, `await_members`, `send`, `list_members`,
`finish_task`, `update_deliverable`, `stop_subtask`, `plan_task`, `get_plan`,
`modify_plan`, `review_subtask`) plus the base orchestration set (`create_task`,
`draft_task`, `commit_task`, `abandon_task`, `inject_into_task`, `resume_task`,
`list_tasks`, `get_task`) move to a **kernel-served MCP toolkit**.

- **Handlers read owner from the session, not `ExecContext`.** Every task action
  runs in the context of a kernel session that already carries `user_id`; the
  handler reads `session.user_id`. This preserves the owner-agnostic
  `ExecContext` while giving handlers the owner they need — replacing today's
  host `HostExecContext(ExecContext)` injection.
- **Lead-gate becomes a kernel-state check.** The `_check_lead_gate` /
  `_check_plan_writer_gate` logic keys on task-session role (lead vs member),
  which is now kernel-owned state — cleaner than today's handler-only gate.
- **Sandbox exposes the toolkit address (D5).** When the kernel is sandboxed, its
  MCP toolkit is reachable at the sandbox-published address, exactly as the
  DataService write-back and resolver callback are wired. The
  "local-persistence + DataService-write-to-host" semantics are identical to how
  the kernel's own tables behave — one mental model for all kernel state.
- Resolution called from a tool handler goes through `MemberResolverPort` (§5),
  so even a kernel-served `dispatch` never touches a host table directly (D4).

---

## 7. Actor lifecycle & recovery, kernel-side (D3)

Everything in the actor lifetime moves to the kernel and is owned by a new
`TaskOrchestrator` sibling to `SessionOrchestrator` (same process, same loop,
same runtime-cache discipline):

- **Spawn / coordinate / review / finish** — `actor_runner`, `mailbox`,
  `live_member_registry`, `dispatcher`, `coordination`, `planning`, `lifecycle`
  run kernel-side. Members reuse the kernel's warm-runtime cache (per-`session_id`,
  idle-TTL + LRU), which in a per-session sandbox is naturally bounded.
- **Recovery at kernel boot.** `recover_active_tasks` moves from the host lifespan
  to **kernel boot**. A sandboxed kernel recovers *its own* tasks from the
  durable store via DataService on start. This must interoperate with the
  **snapshot/resume config-gate** micro-VM path: recovery runs after config is
  applied, and re-hydrates actors from `tasks`/`task_sessions`/`task_events` +
  the DeepAgents checkpoint (`FileCheckpointSaver` / COS in sandbox).
- **Watchdog kernel-side.** `TaskHealthMonitor` runs in the kernel; it marks a
  still-`active` task `blocked` when its lead mailbox is unregistered for N
  sweeps. `is_draining` becomes the *kernel's* drain flag.
- **finish_task de-dup.** There are two `finish_task` implementations today
  (a live one and a dead copy); **confirm which is dead before moving** and drop
  it — do not carry the dead copy into the kernel.

---

## 8. Events, side effects, and the decision inbox (D2 + D4)

Three host services react to task progress today via lazy imports: the
**decision inbox** (AskUser), the **notification ledger** (OS notifications /
badges), and the **memory scheduler** (post-finish extraction). After the move
these become **event-first host reactions**, minimizing new outbound ports:

- Task events are DataService-written to the host durable store (§4). The host
  **subscribes to / polls** task events (it already owns the query path, D2) and
  drives its own side effects — no synchronous kernel→host call needed for
  notifications or memory scheduling.
- **AskUser simplifies.** "A member is parked on a clarifying question" is a
  **kernel-native** fact (the runtime's pending-action / `AskUserQuestion`
  approval state) — the kernel actor detects it from *its own* session/event
  state instead of reading the host decision aggregator. The host decision inbox
  becomes a pure **read-projection** over `awaiting_user` / `user_answered` task
  events. The `record_awaiting_user` / `record_user_answered` writes become task
  events emitted kernel-side.
- Only where a truly synchronous host acknowledgement is required does a thin
  outbound port remain; default to event-driven.
- **Member-attributed events must stamp `payload.agent_name` at emit time**
  (via `resolve_display_name`), so the frontend never has to re-resolve a name
  against a racy members list.

---

## 9. Functional coverage after refactor (D6)

Every current capability, mapped to its post-refactor home. **Parity is the
acceptance bar** — nothing in this column may regress.

| Capability | Today | After | Preserved by |
|---|---|---|---|
| Plan DAG authoring / validation / `ready_keys` | `plan.py` (host) | kernel domain | Pure move; unit tests move with it |
| Task state machine + `assert_transition` | `task_state.py` | kernel domain | Pure move |
| Trigger provenance | `provenance.py` | kernel domain | Pure move |
| Kickoff / draft / commit / abandon | `lifecycle.py` | kernel `TaskOrchestrator` | Behavior identical; resolution via port |
| Dispatch (sync + async + batch) | `dispatcher.py` | kernel runtime | **Confirm which dispatch paths are live** before moving |
| Lead↔member coordination / heartbeat / probe | `coordination.py` | kernel runtime | AskUser probe now kernel-native (§8) |
| Actor loop / turn-to-idle / manifest collect | `actor_runner.py` | kernel runtime | Same loop, same TTL constants |
| Mailbox / InboxMsg / shutdown | `mailbox.py` | kernel runtime | Process-local, unchanged |
| Live member registry (spawn/drain invariant) | `live_member_registry.py` | kernel runtime | **Sync invariant preserved** (§5.2) |
| Review (approve / rework) | `planning.py` | kernel | `plan_version` CAS preserved |
| Messaging / inject / goal-revise | `messaging.py` | kernel + host events | Side effects event-driven (§8) |
| Read queries (list/get/activity) | `queries.py` | host read path | Reads durable store (D2) |
| Recovery sweep | `recovery.py` | **kernel boot** | D3; snapshot-resume aware |
| Health watchdog | `health_monitor.py` | kernel | D3 |
| Task tables | host `valuz_*` | kernel, via DataService | D1/D2; reversible migration |
| Lead + base MCP tools | host toolkit MCP | kernel toolkit MCP | D5; owner from session |
| Public HTTP + SSE | `api/routes/tasks.py` | host, delegating | KernelClient task methods |
| Decision inbox / notifications / memory | host lazy imports | host, event-first | §8 |
| Worktree / subrun dirs | `fs_registry`/`worktree_service` | resolver port output | Projection layer unchanged |

**Also fold in (this is the moment):** the three known reliability gaps —
`failed` task status wiring, stop/pause front-end **de-spin** (stop parks only
`in_progress` while `in_review`/`rework` still render spinning), and the
watchdog/OS-notification coverage — should be closed *as part of* the move rather
than carried over verbatim. See `task-attention-and-reliability.md`.

---

## 10. Pitfalls & regression watch (D6)

Concrete failure modes, ranked by how much they will hurt.

1. **The `LiveMemberRegistry` sync invariant.** All registry methods synchronous;
   no `await` between `create_task(spawn)` and `add_member`. Resolution (async /
   HTTP callback) must finish *before* the spawn block (§5.2). Violating this
   re-introduces the "finish_task drops a just-spawned member" race. **This is
   why the whole actor machine moves as one unit and dispatch cannot straddle the
   host/kernel seam.**
2. **`finish_task` has a dead duplicate.** Verify the live vs dead copy against
   the real call graph before moving; do not port the dead one.
3. **Stop/pause de-spin gap.** `stop` parks only `in_progress`, but the panel maps
   `in_review`/`rework` → spinning → subtasks spin forever on a halted task. Close
   the front-end de-spin during the move; add a regression test.
4. **Recovery timing under snapshot/resume.** Kernel-boot recovery must run after
   config-gate application and must reconcile against the DeepAgents checkpoint
   (COS `FileCheckpointSaver` in sandbox). Test: kill a sandbox mid-task → resume
   → actors re-hydrate and the plan continues.
5. **Data migration must be reversible + lossless.** `valuz_task*` → kernel tables
   crosses stores. Use backup→copy→verify (the `kernel_db_colocate` precedent),
   never drop-and-recreate; never `git stash` for the baseline.
6. **Task-event `sequence` authority (dual-write).** Task tables are dual-written
   (local + durable), so local and durable ids/counts **diverge by design** (a
   constant offset = healthy). Expose the **durable seq only** on the wire; use
   `event_uid` for cross-store identity. Do not "fix" the count mismatch — that is
   the same trap the kernel's `events` table already documents.
7. **Resolver callback latency & failure.** In sandbox, `resolve_member_session`
   is a network hop; dispatch now has an external dependency. Handle timeout /
   host-unreachable explicitly (fail the dispatch cleanly, emit `kickoff_failed` /
   `subtask_failed`, don't wedge the lead loop). The `credential_gap` pre-flight
   must survive the round-trip.
8. **Member-name attribution.** Stamp `payload.agent_name` at emit; a removed /
   renamed agent must still show a name in the timeline.
9. **Owner leakage.** Handlers must read owner from `session.user_id`, not from a
   re-introduced owner-carrying `ExecContext`. Keep `ExecContext` owner-agnostic;
   owner lives only at the DataService/store boundary (JWT) and on the session.
10. **Two SSE lineages must not tangle.** Task SSE stays a host DB-poll over
    `task_events`; do **not** casually merge it into the kernel per-session event
    bus (that path has a history of stuck-loading / seq bugs). Keep them separate.
11. **Test sandbox tripwires.** Upstream recently tightened sandbox-escape and
    ambient-DB-url tests (#526/#532 lineage). New kernel task tables + DataService
    ops + resolver callbacks will trip these unless they route through the
    sanctioned store/URL wiring. Run the sandbox test suite early, not at the end.
12. **`make dev` shows no bloat relief.** In-process kernel = same process;
    acceptance must assert relief only in the `kernel_mode=http` topology (§1
    caveat), and both topologies must pass the full task suite.

---

## 11. Phased migration (dependencies in brackets)

**P0 — Contract & schema skeleton** *(blocks all)*
Extend `KernelClient` + `api/openapi.yaml` with the task method surface (both
transports, contract test). Define kernel `tasks`/`task_events`/`task_sessions`
schema + reversible Alembic (create tables, no data yet). Define
`MemberResolverPort` interface + host impl stub.

**P1 — Pure domain move** *(P0)*
`plan.py`, `task_state.py`, `provenance.py` → kernel; move their unit tests.
Lowest risk, warms up the seam.

**P2 — Actor machine into the kernel** *(P1)* — **the core, delivers D3**
`actor_runner`, `mailbox`, `live_member_registry`, coordination, dispatcher
(execution part), planning, lifecycle → kernel `TaskOrchestrator`. **Hold the
§5.2 sync invariant.**

**P3 — Resolver seam** *(P2)* — **delivers D4** — full contract in **§13**.
Land `MemberResolverPort`; delete `ProjectMemberDatastore`/`ProjectDatastore`/
`ProviderDatastore`/`build_member_session` imports from task code. In-process
impl first; HTTP/JWT callback impl for sandbox second.

**P4 — Tables + data migration via DataService** *(P0, P2)* — **delivers D1/D2**
Add task RPC ops; switch datastore to the kernel store/DataService; migrate data
backup→copy→verify; owner stamped at the boundary.

**P5 — Tools kernel-side** *(P2, P3)* — **delivers D5** — full detail in **§16**.
Move the task toolkit to kernel-native tools; handlers read owner from session;
lead-gate on kernel state; sandbox serves the toolkit at a sandbox-local address.

**P6 — Events / side effects / public API / live interrupt** *(P4, P5)* — full
detail in **§17**. Task events event-first host reaction (decision inbox as
read-projection, notifications, memory, activity index). `api/routes/tasks.py`
delegates to `KernelClient` (frontend surface preserved); SSE reads `task_events`.
Live stop/interrupt as a single in-kernel fan-out.

**P8 — Session/task sandbox start-stop control** — **out of scope here**, a
**commercial** concern (§18). This refactor guarantees only the task=sandbox
semantic (§14.1) the control plane will build on.

**P7 — Verification** *(all)*
`make test-all` / `make typecheck` / `make lint` green (hard gate). **No
task-engine feature flag / dual-run fallback** — a one-way cutover whose safety net
is **full-chain coverage**: end-to-end plan→dispatch→review→finish + live
interrupt/stop + **recovery-after-sandbox-restart**, run in **both** in-process and
`kernel_mode=http`/sandbox topologies. Assert the SaaS bloat-relief acceptance
criterion explicitly in the http topology. Run the sandbox-tripwire suite
(#526/#532 lineage) early, not at the end (§10.11).

---

## 12. Resolved decisions

1. **`TaskOrchestrator` placement** — a **self-contained `src/tasks/` package**
   with its own orchestrator (not a `src/core` sibling). Keeps the sub-session
   concept — which the kernel lacks today — cohesive and independently testable.
2. **Resolver callback transport for sandbox** — a **dedicated host `/rpc`-style
   endpoint** using the DataService JWT model, **not** the toolkit MCP channel.
   Resolution is data-shaped, not tool-shaped (see the callout in §5).
3. **Task-table persistence** — **kernel-side dual-write** (local kernel DB +
   DataService durable), aligned 1:1 with `sessions`/`messages`/`events` under
   `WriteThroughStore`. Durable is the wire authority; local↔durable seq
   divergence is expected (§4.3, §10.6).

---

## 13. P3 detail — `MemberResolverPort` contract

The full contract for **P3's** resolver seam (delivers **D4**). §5 is the
overview; this is what you build from. It reuses the DataService `POST /rpc/{op}`
+ verified-token-owner pattern verbatim ([data-service-architecture.md](data-service-architecture.md)).

### 13.1 The core insight

`build_member_session` (`valuz_agent/adapters/agent_resolver.py:853`) **already
returns a `CreateSessionRequest`** — the kernel's own wire schema. It is the sole
funnel through which task code today reaches:

- the **agent library** (`_member_agent_config` → `AgentDatastore.get_agent`),
- **project membership** (`ProjectMemberDatastore.get`, `build_member_roster`),
- **project context** (`ProjectDatastore` → name + instructions),
- **providers / model channel** (`_resolve_agent_provider` → `ProviderDatastore`),
- **skills** (`resolve_skill_slugs_to_paths`, `always_on_skill_paths`),
- **host MCP** (`always_on_http_mcp_servers`), and the **system-prompt assembly**.

So P3 is **not** "write a new resolver." It is: **relocate this function behind a
Protocol, strip the injected datastore arguments (the host impl opens its own unit
of work), and expose it over two transports.** The output type — a
`CreateSessionRequest` — is unchanged. P3 is a *decoupling* refactor, not a
behavioral one.

### 13.2 What the kernel must stop knowing

Today the caller (`dispatcher.py`, `lifecycle.py`) threads host objects into the
resolver: `members: ProjectMemberDatastore`, `providers: ProviderDatastore`, plus
pre-fetched `project_name` / `project_instructions_md` / `worktree_notice` /
`run_dir`. After P3 the kernel passes **pure data only**; the host impl fetches
everything else itself.

| Was passed in by the caller | After P3 |
|---|---|
| `members` (`ProjectMemberDatastore`) | host impl opens its own UoW |
| `providers` (`ProviderDatastore`) | host impl opens its own UoW |
| `project_name`, `project_instructions_md` | host impl reads `ProjectDatastore` |
| `worktree_notice`, `run_dir` | host impl resolves cwd/worktree (`fs_registry` + `worktree_service`) from `isolation` |
| `user_id` | in-process: explicit arg; **sandbox: derived from the verified token** (anti-spoof, §13.6) |

The three sibling-datastore imports (`ProjectMemberDatastore`, `ProjectDatastore`,
`ProviderDatastore`) and the `agent_resolver` import are **deleted from all task
code** — clearing the module-boundary violations flagged in `backend/CLAUDE.md`.

### 13.3 The port surface (kernel-side Protocol)

One resolve call that returns everything needed to spawn, plus two read helpers.
Owner is threaded per transport (§13.6).

```python
# kernel/src/tasks/ports/resolver.py   (the Protocol — kernel owns it)
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from app.schemas import CreateSessionRequest          # kernel wire schema = ResolvedSession

ResolvedSession = CreateSessionRequest                 # explicit alias for intent

@dataclass(frozen=True)
class MemberResolveSpec:
    project_id: str
    agent_slug: str
    is_lead: bool
    task_id: str
    brief: str                                         # goal/md (lead) or scoped goal+refs (member)
    isolation: Literal["shared", "worktree"] = "shared"  # host resolves the concrete cwd
    dispatch_mode: Literal["sync", "async"] = "sync"
    goal_mode: bool = False
    plan_pre_committed: bool = False
    model_override: str | None = None
    lead_session_id: str | None = None
    user_id: str | None = None                         # in-process only; sandbox ⇒ token owner

@dataclass(frozen=True)
class ResolveResult:
    session: ResolvedSession | None                    # None ⇒ orphaned slug (no membership/library agent)
    credential_gap: str | None                         # human reason when no usable model provider
    # invariant: session is None  XOR  the caller may spawn it (subject to credential_gap)

class MemberResolverPort(Protocol):
    async def resolve_session(self, spec: MemberResolveSpec) -> ResolveResult: ...

    async def resolve_display_names(
        self, project_id: str, slugs: Sequence[str], *, user_id: str | None = None
    ) -> dict[str, str]: ...                            # for event agent_name stamping + queries

    async def resolve_project_cwd(
        self, project_id: str, *, task_id: str, isolation: str = "shared",
        user_id: str | None = None,
    ) -> str: ...                                       # for paths the kernel needs outside a dispatch
```

**Why `resolve_session` folds in the credential check.** Today dispatch is **two**
host calls: `build_member_session(...)` then `_credential_gap(session, ...)` (which
re-reads `ProviderDatastore` to tell an OAuth-subscription `model_provider=None`
from a real gap). Over a sandbox hop that is two round-trips per dispatch. Folding
the check in and returning `ResolveResult.credential_gap` makes dispatch **one
round-trip**. The kernel decides: `session is None` → orphaned →
`subtask_failed`/`kickoff_failed`; `credential_gap` set → same, with the reason;
else → spawn.

### 13.4 File placement

| Component | Path | Side | Notes |
|---|---|---|---|
| Port Protocol + dataclasses | `kernel/src/tasks/ports/resolver.py` | kernel | `MemberResolverPort`, `MemberResolveSpec`, `ResolveResult` |
| HTTP client transport | `kernel/src/tasks/adapters/http_resolver.py` | kernel | `HttpMemberResolver` — speaks HTTP+JWT to the host; **no host import** |
| Transport factory | `kernel/src/tasks/adapters/resolver_factory.py` | kernel | picks `http` from env else the injected in-process object (mirrors `_build_durable_store`) |
| Host impl | `valuz_agent/adapters/member_resolver.py` | host | `HostMemberResolver(MemberResolverPort)` — wraps `build_member_session` + opens its own UoW |
| Host `/rpc` ASGI app | `valuz_agent/adapters/member_resolver_service.py` | host | mounted at `/_internal/resolver` (+ legacy `/internal/resolver`), same shape as `data_service.py` |
| Sandbox env injector | `valuz_agent/boot/resolver_inject.py` | host | `resolver_env(owner_user_id, host_callback_url)` — mirrors `data_service_inject.py` |

The split mirrors the store seam: the **kernel** owns the Protocol + HTTP client;
the **host** owns the concrete impl + the endpoint. The in-process object is
injected host→kernel at boot (allowed direction); the HTTP client imports nothing
host-side.

### 13.5 Two transports

**In-process (default, `make dev`, OSS).** At boot the host constructs
`HostMemberResolver()` and injects it into the kernel's `TaskOrchestrator`, exactly
as it injects the store:

```python
# host boot (valuz_agent/boot/kernel.py, alongside the store wiring)
from valuz_agent.adapters.member_resolver import HostMemberResolver
task_orchestrator.bind_resolver(HostMemberResolver())     # direct object, no network
```

`HostMemberResolver.resolve_session` opens `async_unit_of_work()`, builds the
datastores, and calls the relocated `build_member_session` — behavior-identical to
today, just behind the interface.

**Sandbox (HTTP + JWT, SaaS).** The kernel holds only a URL + token;
`resolver_factory` reads env and constructs `HttpMemberResolver`:

```python
# kernel/src/tasks/adapters/resolver_factory.py
def build_resolver(injected: MemberResolverPort | None) -> MemberResolverPort:
    if os.environ.get("VALUZ_RESOLVER_API_KIND") == "http":
        return HttpMemberResolver(
            base_url=os.environ["VALUZ_RESOLVER_API_URL"],
            token=os.environ["VALUZ_RESOLVER_API_TOKEN"],
        )
    assert injected is not None, "in-process resolver must be injected at boot"
    return injected
```

Host injects the env for a sandboxed kernel (mirrors `data_service_env`):

```python
# valuz_agent/boot/resolver_inject.py
def resolver_env(*, owner_user_id: str, host_callback_url: str) -> dict[str, str]:
    if not host_callback_url:
        return {}                                   # no sandbox ⇒ in-process
    secret = get_or_create_ds_secret(owner_user_id) # REUSE the DataService per-owner secret
    return {
        "VALUZ_RESOLVER_API_KIND": "http",
        "VALUZ_RESOLVER_API_URL": host_callback_url.rstrip("/") + "/_internal/resolver",
        "VALUZ_RESOLVER_API_TOKEN": mint_data_service_token(secret, user_id=owner_user_id),
    }
```

**Reuse the DataService per-owner HS256 secret + verifier** — resolver and
DataService share one host↔sandbox trust boundary per owner. No new secret, no new
verifier; only a new URL. This is the third leg of the existing callback triangle:
**storage** (`/_internal/data`), **tools** (`/_internal/mcp/toolkit`), and now
**resolution** (`/_internal/resolver`).

### 13.6 The `/rpc` wire contract (host endpoint)

Byte-for-byte the DataService shape: `POST /rpc/{op}`, owner from the **verified
bearer token** (never the body), `{"data": ...}` envelope, 401 on missing/invalid
token.

```python
# valuz_agent/adapters/member_resolver_service.py  (host)
@router.post("/rpc/resolve_session")
async def resolve_session(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    spec = MemberResolveSpec(**{**body["spec"], "user_id": owner_id})   # owner forced from token
    result = await resolver.resolve_session(spec)
    return {"data": {
        "session": result.session.model_dump() if result.session else None,
        "credential_gap": result.credential_gap,
    }}

@router.post("/rpc/resolve_display_names")
async def resolve_display_names(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    return {"data": await resolver.resolve_display_names(
        body["project_id"], body["slugs"], user_id=owner_id)}

@router.post("/rpc/resolve_project_cwd")
async def resolve_project_cwd(body: JsonBody, owner_id: OwnerDep, resolver: ResolverDep):
    return {"data": await resolver.resolve_project_cwd(
        body["project_id"], task_id=body["task_id"],
        isolation=body.get("isolation", "shared"), user_id=owner_id)}
```

| op | request body | `data` response |
|---|---|---|
| `resolve_session` | `{spec: MemberResolveSpec}` (body `user_id` ignored) | `{session: CreateSessionRequest \| null, credential_gap: str \| null}` |
| `resolve_display_names` | `{project_id, slugs: [str]}` | `{[slug]: name}` |
| `resolve_project_cwd` | `{project_id, task_id, isolation?}` | `str` (absolute cwd) |

**Anti-spoof, identical to `save_session`:** the endpoint overwrites `spec.user_id`
with the token owner. On PG the same `install_rls_guc` stamps `app.current_user_id`
so any read the resolver runs is owner-scoped at the DB. `HttpMemberResolver`
re-hydrates `CreateSessionRequest.model_validate(data["session"])`.

### 13.7 The sync-invariant timing rule (load-bearing)

`LiveMemberRegistry` requires **no `await` between `create_task(spawn)` and
`add_member`** (else a racing `finish_task` drops a just-spawned member — the
keystone invariant, §10.1). `resolve_session` is async and, in a sandbox, a network
hop. Therefore **all resolution completes before the synchronous spawn block**:

```python
# kernel/src/tasks/runtime/dispatcher.py  (post-P3)
result = await self._resolver.resolve_session(spec)        # ← the ONLY await before spawn
if result.session is None:
    await self._emit_subtask_failed(task_id, key, "orphaned agent"); return
if result.credential_gap:
    await self._emit_subtask_failed(task_id, key, result.credential_gap); return

member = result.session
# ── synchronous block: NO await until create_task returns ─────────────
self._mailbox.register(lead_session_id)
self._registry.add_member(task_id, member.id)
self._mailbox.register(member.id)
asyncio.create_task(self._actor.run_actor_loop(member))    # spawn
# ──────────────────────────────────────────────────────────────────────
await self._kernel_create_session(member)                  # persist AFTER spawn is registered
```

This is exactly why the **whole** actor machine moved as one unit (P2) and dispatch
cannot straddle the host/kernel seam: the async resolution round-trip is pushed
*before* the atomic spawn, keeping the invariant intact even over HTTP.

### 13.8 What the resolver does NOT own (P5 interaction)

Today `build_member_session` injects `always_on_http_mcp_servers(..., toolkit="lead"|"base")`
— both **host** MCP (docs / schedules / connectors) **and** the task **toolkit**
(dispatch / plan / review …). Under D5 the task toolkit becomes **kernel-served**
(P5). The clean split:

- **Resolver (host) injects host-owned MCP only** — docs / schedules / connectors +
  the agent's own `mcp_servers` (they need host credentials/URLs the kernel lacks).
- **Kernel injects the task toolkit itself** when it creates the session (P5), from
  its native tool registry — no host URL.

**P3/P5 sequencing:** to keep P3 behavior-preserving *before* P5 lands, the host
resolver keeps injecting the full set (including the toolkit's host URL). When P5
flips the toolkit to kernel-native, delete only the `toolkit=` arm from the
resolver's `always_on_http_mcp_servers` call — the single line that changes hands.

### 13.9 Error taxonomy & failure semantics

The resolver adds an external dependency to dispatch; failures must be explicit,
never a wedged lead loop.

| Condition | `resolve_session` result / raise | Kernel action |
|---|---|---|
| Orphaned slug (no membership / no library agent) | `ResolveResult(session=None)` | emit `subtask_failed`/`kickoff_failed("orphaned agent")`, unlock nothing |
| No usable model provider (real gap) | `ResolveResult(credential_gap="…")` | emit failure with the human reason |
| OAuth subscription (`model_provider=None` but valid) | `credential_gap=None` (resolved) | spawn normally |
| Host unreachable / timeout (sandbox) | raise `ResolverUnavailableError` | fail *this* dispatch cleanly (retryable), keep the lead loop alive |
| Bad token / owner mismatch | HTTP 401 → `ResolverAuthError` | boot/config error, surface loudly |

`HttpMemberResolver` carries a bounded timeout + typed errors mirroring
`kernel_client`'s `Kernel*Error` family (`ResolverUnavailableError`,
`ResolverAuthError`, `ResolverBadRequestError`). A dispatch failure is a
**node-level** failure (plan node → `failed`), not a task-level crash.

### 13.10 Migration steps within P3 (ordered)

1. **Extract** `build_member_session` and its helpers (`_resolve_agent_provider`,
   `_member_agent_config`, `build_member_roster`, `_credential_gap`) into
   `HostMemberResolver`, changing the signature to open its own UoW and drop the
   `members` / `providers` / pre-fetched-context parameters.
2. **Define** the Protocol + dataclasses in `kernel/src/tasks/ports/resolver.py`.
3. **In-process wire**: host injects `HostMemberResolver` into `TaskOrchestrator`
   at boot; `resolve_session` returns the folded `ResolveResult`.
4. **Delete** `ProjectMemberDatastore` / `ProjectDatastore` / `ProviderDatastore` /
   `agent_resolver` imports from all task code; dispatcher/lifecycle now hold a
   `MemberResolverPort` and call it with a `MemberResolveSpec`. Verify
   `scripts/check_module_boundaries.py` passes (violations disappear).
5. **HTTP transport**: `HttpMemberResolver` + `member_resolver_service.py` mounted
   at `/_internal/resolver`; `resolver_env` injected for sandboxed kernels.
6. **Contract test** (§13.11).

Steps 1–4 land the decoupling on the in-process path (green `make dev`); 5–6 light
up the sandbox path. Each is independently reviewable.

### 13.11 Contract test

Pin route ↔ client ↔ Protocol the way `test_data_service_contract.py` pins the
store seam:

- **Shape parity**: every `MemberResolverPort` method has exactly one `/rpc/{op}`
  route; request/response bodies round-trip through `HttpMemberResolver` and the
  host endpoint with no field drift.
- **Transport equivalence**: the *same* `MemberResolveSpec` yields an **identical**
  `CreateSessionRequest` through the in-process impl and the HTTP transport
  (golden-compare the serialized session — id-generation deterministic under the
  test seed or excluded from the compare).
- **Anti-spoof**: a body `user_id` different from the token owner is ignored; owner
  always comes from the token. Missing/invalid token → 401.
- **Invariant guard**: a unit test asserts the dispatcher performs no `await`
  between `registry.add_member` and `create_task` (static check or a monkeypatched
  resolver that would interleave).

### 13.12 Open edges (decide during the P3 build, not blocking the contract)

1. **`resolve_project_cwd` scope.** Needed only where the kernel wants a raw cwd
   outside a dispatch (e.g. writing the task `.md`). If task-file writing stays a
   host concern in P6, this method may not be needed in P3 — add it only when a
   caller appears.
2. **Roster freshness.** The lead's member roster is baked into its instructions at
   `resolve_session` time; a mid-task membership change is stale until the lead's
   next session. Matches today's snapshot-at-build behavior; note it, don't fix it
   in P3.
3. **Resolver result caching.** A member re-dispatched on `rework` re-resolves
   (another round-trip). Acceptable now; a per-`(task,slug)` short-TTL cache is a
   later optimization, not part of the contract.

---

## 14. P2 detail — actor machine into the kernel

The full design for **P2**, which delivers **D3** (the actor's *entire* lifecycle
kernel-side). This is the core structural move; P3 (resolver) and P4 (tables) then
*harden* the seams P2 introduces, **without moving code again**.

### 14.1 The sandbox unit, and the interim-wiring principle

**The sandbox unit is the task (locked).** A task's lead session and all its member
(subtask) sessions are co-resident in ONE kernel / sandbox. A member is a *sub-run
inside the task's sandbox*, never its own sandbox. "Session-granular sandbox
start/stop" applies to **top-level** sessions — a standalone chat, a project chat,
or **a task (its lead together with its members)** — each a sandbox that starts and
stops as a unit.

This is faithful to today's model (members are already same-process `asyncio`
siblings sharing the project cwd) and it is what keeps the coordination substrate
**in-memory**: `MailboxRegistry` (`asyncio.Queue`) and `LiveMemberRegistry`
(in-process dict) work because lead and members share one event loop. Had members
been individually sandboxed, that substrate would have to become a cross-process
bus — a far larger rewrite. **It does not.** It also collapses the live
interrupt/stop path to a single in-kernel fan-out (no cross-sandbox coordination —
§17.3). Per-session/per-task sandbox *start/stop orchestration* is a later,
commercial concern (§18); this refactor guarantees only the task=sandbox
**semantic** the substrate depends on.

**Interim-wiring principle.** P2's deliverable is that **the task actor machine
physically runs in the kernel, on the kernel's event loop, behind a clean kernel
boundary.** But P2 lands *before* the resolver (P3) and the table move (P4). The resolution to that ordering is the
**interim-wiring principle**:

> P2 introduces every host→kernel dependency as an **injected port**. In-process
> (`make dev`), the host binds a concrete impl that wraps *today's* code. P3 and
> P4 then swap the impl behind that port (HTTP transport / DataService) — the
> kernel task code never changes again.

So from P2 onward the kernel task package imports **only kernel Protocols**; the
host injects impls at boot. The three seams P2 stubs:

| Seam | P2 impl (in-process) | Hardened by |
|---|---|---|
| `MemberResolverPort` (agent/project resolution) | host `HostMemberResolver` wrapping `build_member_session` | **P3** (contract + HTTP) |
| `TaskStorePort` (task persistence) | host wrapper over today's `TaskDatastore` (`valuz_task*` in `valuz.db`) | **P4** (rename + DataService dual-write) |
| Kernel session ops (create/run/events) | **direct** `SessionOrchestrator` + store calls (no more `kernel_client` facade) | already final |

**P2 acceptance is green `make dev` (in-process).** The sandbox path only fully
lights up after P3/P4/P5, because task tools are still host-served until P5 (§14.5).
Do not judge P2 by sandbox behavior — judge it by: actors run in the kernel loop,
the kernel boundary check passes, and the full task test suite is green in-process.

### 14.2 The `src/tasks/` package (what moves in)

A self-contained kernel package (decision §12.1), sibling to `src/core`:

```
kernel/src/tasks/
├── domain/            # from P1: plan.py, task_state.py, provenance.py (pure)
├── runtime/
│   ├── actor_runner.py           ← moved (the run_actor_loop engine)
│   ├── mailbox.py                ← moved (MailboxRegistry singleton)
│   ├── live_member_registry.py   ← moved (the keystone; sync invariant)
│   └── dispatcher.py             ← moved (spawn path; resolution now via port)
├── services/
│   ├── coordination.py           ← moved (await/heartbeat/probe/shutdown)
│   ├── planning.py               ← moved (plan/review; CAS)
│   ├── lifecycle.py              ← moved (kickoff/draft/commit/abandon/finish)
│   ├── recovery.py               ← moved (boot sweep; now kernel boot)
│   ├── messaging.py              ← moved (send/inject/goal-revise)
│   ├── queries.py                ← moved (read-side; host read path in P6)
│   └── health_monitor.py         ← moved (watchdog; kernel lifespan)
├── ports/
│   ├── resolver.py               ← P3 Protocol (stubbed + injected in P2)
│   ├── task_store.py             ← TaskStorePort Protocol (host-wrapped in P2)
│   └── side_effects.py           ← decision-inbox / notification / memory (optional; §8)
└── orchestrator.py               ← NEW: TaskOrchestrator composition root (§14.3)
```

The **domain** layer already moved in P1. P2 moves **runtime + services**, defines
**ports**, and adds the **orchestrator**. What does *not* move: the host HTTP routes
(P6), the tool serving (P5), and the host side-effect services themselves (they
stay host-side; the kernel calls them through `side_effects.py` ports or, per §8,
reacts event-first).

### 14.3 `TaskOrchestrator` skeleton

Mirrors today's host `TaskOrchestrator` composition root (`orchestrator.py:121`) —
one `LiveMemberRegistry` + one `MailboxRegistry` shared across the five services —
but constructed kernel-side and driven by injected ports instead of host imports.

```python
# kernel/src/tasks/orchestrator.py
class TaskOrchestrator:
    def __init__(
        self,
        *,
        sessions: SessionOrchestrator,          # kernel's own — direct, same loop
        task_store: TaskStorePort,              # injected (host-wrap in P2 → DataService in P4)
        resolver: MemberResolverPort,           # injected (host-wrap in P2 → HTTP in P3)
        side_effects: SideEffectPorts | None = None,   # optional (§8); None ⇒ no-op
    ) -> None:
        # keystone singletons — ONE instance, shared by every service (sync invariant)
        self._registry = LiveMemberRegistry()
        self._mailbox = MailboxRegistry()
        self._actor = ActorRunner(sessions=sessions, mailbox=self._mailbox)

        # the five peeled services (ADR-023 shape preserved), all sharing the two singletons
        self._planning = PlanningService(task_store)
        self._dispatcher = DispatcherService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )
        self._coordination = CoordinationService(
            registry=self._registry, mailbox=self._mailbox, sessions=sessions,
            task_store=task_store, side_effects=side_effects,
        )
        self._lifecycle = LifecycleService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )
        self._recovery = RecoveryService(
            registry=self._registry, mailbox=self._mailbox, actor=self._actor,
            resolver=resolver, sessions=sessions, task_store=task_store,
        )

    # thin delegators (unchanged surface — callers/tools resolve on self)
    async def kickoff(self, spec): ...
    async def dispatch(self, ...): return await self._dispatcher.dispatch_async(...)
    async def await_member_results(self, ...): return await self._coordination.await_member_results(...)
    async def review_subtask(self, ...): return await self._planning.review_subtask(...)
    async def finish_task(self, ...): return await self._lifecycle.finish_task(...)
    async def recover_active_tasks(self): return await self._recovery.recover_active_tasks()
    # … stop/resume/inject/plan/get/list …
```

Two invariants carried verbatim from the host version:

- **One `LiveMemberRegistry`, one `MailboxRegistry`, shared by every service** —
  constructed once in `__init__`, passed by reference. A second instance would
  split the spawn/drain state and reopen the finish-drops-member race.
- **`finish_task` lives in exactly one place.** The host has a live copy and a dead
  copy (§10.2); confirm which is dead against the call graph and move **only the
  live one** into `LifecycleService`.

### 14.4 Rewiring `kernel_client` → direct orchestrator/store

Today the host task code reaches the kernel through the `kernel_client` facade
(`create_session`, `run_turn`, `emit_live_event`, `get_session`, `get_events`,
`set_mode`, `interrupt`, `cleanup_runtime`). Once the task code lives *inside* the
kernel, those facade hops collapse into **direct** calls — the `InProcessKernelClient`
already *was* that path, so this is deleting a layer, not adding one:

| Was (host, via facade) | Now (in-kernel, direct) |
|---|---|
| `kernel_client.create_session(user_id, s)` | `sessions.create_session(s)` (SessionOrchestrator/store) |
| `kernel_client.run_turn(...)` | `sessions.run_turn(...)` |
| `kernel_client.emit_live_event(...)` | `sessions.emit_live_event(...)` |
| `kernel_client.get_session / get_events` | store reads via `sessions` / `TaskStorePort` |
| `kernel_client.interrupt / set_mode` | `sessions.interrupt / set_session_mode` |

The `ActorRunner` holds the `SessionOrchestrator` reference and calls `run_turn`
directly; `emit_live_event` for `session_error` becomes a direct orchestrator call.
No task code imports `valuz_agent.adapters.kernel_client` anymore.

### 14.5 The shared event loop, runtime cache, and the temporary tools bridge

- **One event loop.** `TaskOrchestrator` runs on the *same* asyncio loop as
  `SessionOrchestrator`. Member/lead actors are `asyncio.create_task(...)` on that
  loop. In a per-**task** sandbox (§14.1), that loop is the sandbox's own — which is
  exactly how the actor bloat is distributed off the shared host process (§1, D7).
- **Runtime cache reuse.** A member *is* a kernel session; its turns go through
  `SessionOrchestrator._ensure_runtime` — the existing warm-runtime cache
  (per-`session_id`, idle-TTL + LRU). No separate task runtime pool; in a task's
  sandbox it is naturally bounded to that task's members.
- **Temporary tools bridge (until P5).** In P2 the task **tools are still
  host-served** (host toolkit MCP). Their handlers now call the *kernel*
  `TaskOrchestrator` — in-process, a direct object reference bound at boot. This
  bridge works **only in-process**; a sandboxed agent's tool call cannot reach back
  into its own sandbox kernel through a host handler. That is why sandbox task
  tools require P5 (kernel-served tools), and why P2's acceptance is in-process
  only (§14.1).

### 14.6 Preserving the sync invariant (the keystone)

The spawn block moves into `kernel/src/tasks/runtime/dispatcher.py` unchanged in
shape. All async work (resolution in P3, session persistence) is pushed **outside**
the synchronous `register → add_member → create_task` block. The canonical form is
in §13.7; the P2 rule is simply: **the block that spans `mailbox.register` /
`registry.add_member` / `asyncio.create_task` contains no `await`.** A unit test
guards this (§13.11 "invariant guard"). This constraint is *why* the entire actor
machine moves as one unit in P2 rather than being split across the seam.

### 14.7 Recovery & health monitor at kernel boot (D3)

Both the startup sweep and the watchdog move from the host lifespan to **kernel
boot / kernel lifespan**:

- **Recovery.** `recover_active_tasks` runs when the kernel starts (in a sandbox:
  when *that* sandbox starts, recovering *its* owner's active tasks). It reads
  `tasks` / `task_sessions` / `task_events` through `TaskStorePort` and re-hydrates
  lead + member actors, respawning through the same dispatcher path (so the sync
  invariant holds on recovery too). It must run **after** the config-gate applies
  (snapshot/resume micro-VM) and reconcile against the DeepAgents checkpoint.
- **Watchdog.** `TaskHealthMonitor` starts/stops with the kernel lifespan; its
  drain check reads the **kernel's** `is_draining`, not the host's.

### 14.8 Boot wiring

The kernel composition root (`app/dependencies.py`) constructs the
`TaskOrchestrator` singleton alongside the store + `SessionOrchestrator`. The
concrete ports differ by execution location:

```python
# kernel/app/dependencies.py  (composition root)
def build_task_orchestrator(sessions, store, *, injected_resolver=None, side_effects=None):
    resolver = build_resolver(injected_resolver)          # §13.5: env http OR injected object
    task_store = build_task_store(store)                  # P2: host-wrap; P4: DataService dual-write
    return TaskOrchestrator(
        sessions=sessions, task_store=task_store, resolver=resolver, side_effects=side_effects,
    )
```

- **In-process (host-embedded kernel).** At boot (`valuz_agent/boot/kernel.py`) the
  host injects `HostMemberResolver` + host side-effect impls into
  `build_task_orchestrator`, and binds the resulting `TaskOrchestrator` reference
  where the host toolkit MCP handlers can reach it (the temporary bridge, §14.5).
- **Sandbox/standalone kernel.** No injection — `build_resolver` reads
  `VALUZ_RESOLVER_API_*` (P3) and `build_task_store` reads `KERNEL_STORE`/DataService
  (P4). Recovery + watchdog start from the kernel's own lifespan.

Boot ordering: config gate → store/DataService ready → `SessionOrchestrator` →
`TaskOrchestrator` → `recover_active_tasks()` → `health_monitor.start()`.

### 14.9 Session-module coupling — turn enrichment as data (push, not pull)

The shared turn driver `run_session_to_idle` reaches into five host `sessions`-module
internals today (`context_builder._build_additional_context`,
`attachments._load_pending_attachments` / `_mark_attachments_consumed`,
`run_orchestrator._finalize_session`, `project_index.record`, `SESSION_FINISHED`).
Naively each becomes a **per-turn cross-seam callback** — unacceptable latency once
the actor runs in a sandbox. The elegant resolution splits them into three
categories, and the hot ones collapse to **data passed in, never pulled**:

**A. Turn enrichment (`attachments` + `additional_context`) — push at host entry
points.** The kernel `run_turn` **already accepts** `attachments` and
`additional_context` as inputs. They are host-owned overlays, and they are only
meaningful when a **host-originated input** starts a turn — `kickoff`, a user
`inject`, or a user message. Those all originate host-side, where
`_load_pending_attachments` / `_build_additional_context` already have DB access.
So the host computes them at those entry points and passes them **into** the kernel
task API (kickoff/inject carry `attachments` + `additional_context`). Autonomous
actor turns — a member's goal-loop iterations, or the lead reacting to
`member_done` — carry no new user input; their context already lives in
`session.instructions` (built by the resolver at dispatch), so they drive
`run_turn` with empty enrichment. **Result: zero per-turn host callback.** The
coupling becomes "enrichment is data, computed host-side at the entry points,
passed down"; `_load_pending_attachments` / `_mark_attachments_consumed` /
`_build_additional_context` stay host-side, invoked by the host route handlers
(kickoff/inject) exactly where they already run — members are a no-op (no staged
files).

**B. Turn finalization (`_finalize_session`) — already a kernel op.** It appends a
`session_error` event + stamps the kernel status; it is a wrapper over
`finalize_session`. In-kernel it becomes a **direct** `SessionOrchestrator` call in
the actor's `finally` block — no host coupling remains. `run_session_to_idle`
itself becomes a kernel primitive (it is the generic turn driver; the chat path
calls the kernel's version too, passing its own host-built enrichment).

**C. Host projections (`project_index`, `SESSION_FINISHED`) — task drops them; no
new event.** *Verified* (§17.4): the kernel emits **no** `session.created` event, and
`project_index` is **chat-scoped** — `touch_activity`/recents ride the chat turn
path, and task recency comes from the task tables, not the index. Chats are still
created by **host** routes, so their `record` / `touch_activity` stay host-side
unchanged. **Task lead/member sessions simply stop being recorded in
`project_index`**; the one load-bearing use — `project_of(session_id)` reverse
lookup — resolves instead from the durable session's `metadata.valuz.project_id`
(present on every session). `SESSION_FINISHED` for task sessions becomes a **task
event** (§17.5); for chats it stays the host chat path. → No kernel→host callback,
no new kernel event.

The elegant core: **the kernel actor loop pulls nothing from the host per turn.**
Enrichment is pushed in with host-originated inputs; finalization is native;
projections are event-driven off the stream the host already consumes.

### 14.10 Migration steps within P2 (ordered, each green in-process)

1. **Define the ports** (`resolver.py` stub, `task_store.py`, `side_effects.py`) and
   the `TaskOrchestrator` shell in `kernel/src/tasks/` — no logic yet.
2. **Move the runtime trio** (`actor_runner`, `mailbox`, `live_member_registry`)
   into `runtime/`, rewiring `kernel_client.run_turn/emit_live_event` → direct
   `SessionOrchestrator` calls (§14.4).
3. **Move the five services** (`dispatcher`, `coordination`, `planning`,
   `lifecycle`, `recovery`, `messaging`, `queries`) behind the shared singletons;
   replace host-datastore/resolver imports with the injected ports.
4. **Move `health_monitor`**; wire recovery + watchdog into kernel boot (§14.7).
5. **Bind in-process impls** at host boot; point the host toolkit MCP handlers at
   the kernel `TaskOrchestrator` (temporary bridge).
6. **Move the actor test suites** (`tests/modules/tasks/test_actor_v2.py`,
   `test_plan_orchestrator.py`, …) alongside the code; make them run against the
   kernel `TaskOrchestrator`. Green `make dev` + full task suite is the P2 gate.

Steps 1–3 are the bulk; each service can move and go green independently because
the ports isolate it from the not-yet-moved neighbors.

### 14.11 Open edges (decide during the P2 build)

1. **`SideEffectPorts` shape.** §8 argues most side effects (notifications, memory)
   should be **event-first** (host reacts to `task_events`) rather than synchronous
   outbound ports. P2 may inject no-op side-effect ports and defer the host reaction
   wiring to P6 — as long as the *events* are still emitted. Decide whether any side
   effect genuinely needs a synchronous callback in P2 (likely none).
2. **Queries placement.** `queries.py` is read-side; §9 routes reads through the
   host in P6. In P2 it can stay a thin kernel service over `TaskStorePort`; the
   host read path replaces it in P6. Don't over-invest in it during P2.
3. **`kernel_client` supervision hooks.** `scan_orphan_pendings` / `scan_orphan_runs`
   / `cleanup_runtime` are in-process-only supervision with no remote analog. Confirm
   whether the task recovery path needs any of them, or whether the kernel's own
   `scan_orphan_runs` at boot already covers task sessions.

---

## 15. P4 detail — tables into the kernel + DataService dual-write

The full design for **P4**, which delivers **D1** (tables kernel-owned, `valuz_*`
prefix dropped, `user_id` kept) and **D2** (persisted through the DataService,
**kernel-side dual-write**, host-terminated queries). It reuses the kernel's own
three-table machinery verbatim — `_owner_column`, `event_uid` idempotency,
`WriteThroughStore`, `store_wire`, and the `kernel_db_colocate` data-move
precedent. **P4 changes the impl behind P2's `TaskStorePort`; the kernel task code
does not change again.**

### 15.1 What P4 delivers, and the P2 seam it fills

P2 introduced `TaskStorePort` and injected a host wrapper over today's
`TaskDatastore` (still `valuz_task*` in `valuz.db`). P4 replaces that impl with the
real thing:

- **Kernel-owned ORM tables** `tasks` / `task_events` / `task_sessions` (renamed,
  `user_id` kept), in the kernel schema — so they materialize in both `kernel.db`
  (local buffer) and the durable store (host `valuz.db`, or PG in SaaS).
- **`WriteThroughTaskStore`** — dual-write with the same `authority` semantics as
  `WriteThroughStore` (durable = system of record in remote/sandbox; local = buffer).
- **DataService RPC ops** for tasks (§4.2), owner from the verified token.
- **A one-time data move** from the retiring host `valuz_task*` (mirrors
  `kernel_db_colocate`).

The `TaskStorePort` *interface* stays exactly as P2 defined it — only the binding
changes (host-wrap → `WriteThroughTaskStore`), mirroring how `KERNEL_STORE` swaps
the session store behind one factory.

### 15.2 The three kernel task tables

New ORM models beside `SessionModel`/`MessageModel`/`EventModel`, in
`kernel/src/adapters/sqlalchemy_store/task_models.py` (same `Base`), following the
existing conventions **exactly**:

- **`_owner_column()`** for `user_id` — `String(64)`, `NOT NULL`, indexed, **no
  default**, stamped explicitly by the converters from the caller's owner.
- **`tasks`** — `id` (PK), `user_id`, `project_id`, `title`, `goal`, `status`,
  `plan` (JSON DAG), `plan_version` (int CAS), `trigger_*`, `metadata_` (`"metadata"`
  JSON), `created_at`/`updated_at` (BIGINT epoch ms). `CheckConstraint` on `status`
  (mirror the `ck_sessions_status` style). Indexes on `project_id`, `status`,
  `trigger_task_id`/`trigger_automation_id`.
- **`task_events`** — `id` (Integer PK **autoincrement**, the wire cursor),
  `user_id`, `project_id`, `task_id`, `type`, `actor`, `session_id`, `payload`
  (JSON), `timestamp` (BIGINT), **`event_uid`** (`String(64)`, nullable) with
  **`uq_task_events_owner_uid (user_id, event_uid)` unique** — byte-for-byte the
  `EventModel` idempotency pattern. Indexes on `(task_id, id)` (the SSE cursor) and
  `(task_id, type)`.
- **`task_sessions`** — `id` (PK), `user_id`, `project_id`, `task_id`, `session_id`
  (→ kernel `sessions.id`, business key, no FK), `kind`, `subtask_key`, `sequence`
  (0=lead), `status`, `label`, `goal`, `dispatched_by`, `project_mode`, `run_dir`,
  `result_manifest` (JSON), `ended_at`. Unique `(task_id, session_id)`.

Everything is dialect-agnostic (SQLite / PG), instants are epoch-ms `BIGINT`, JSON
columns via `sqlalchemy.types.JSON` — identical to the kernel tables.

### 15.3 The seq model decision — adopt the kernel pattern

Today `valuz_task_event` carries a **host-assigned per-`(project, task)` monotonic
`sequence`** with retry-on-collision (`datastore.py:282`), and the SSE cursor pages
on it. The kernel `events` table instead uses a **per-store autoincrement `id`** as
the wire cursor + `event_uid` for cross-store identity, and deliberately tolerates
divergent local/durable ids (memory: [[valuz-event-seq-two-stores]]).

**P4 adopts the kernel pattern** (required for clean dual-write):

- `task_events.id` (durable autoincrement) **is the wire cursor**. Within one
  task's event stream the durable id is strictly increasing, so the SSE
  `?after_seq=` contract keeps working — it just pages on the durable row id
  instead of a per-task counter.
- `event_uid` bridges identity across the two stores; a retried dual-write append
  reuses the uid and the unique index collapses the duplicate.
- **Drop** the host-assigned per-`(project, task)` `sequence` + its retry-on-collision
  loop. This removes a whole class of write-contention code.
- `append_event` returns the **authority's** seq (durable in remote/sandbox); the
  two stores' ids diverge by a constant offset **by design** — never reconcile them
  (§10.6).

**Edge to verify (§15.12):** the frontend Todo-panel SSE consumes `after_seq`
purely as a monotonic cursor, so the durable-id switch is transparent. Confirm no
consumer treats the old per-task `sequence` as a stable business number (it is not
exposed as one today).

### 15.4 `TaskStorePort` surface

Task-shaped (keyed by `task_id`/`project_id`, not `session_id`), but structurally
the same owner-first, dual-write-aware shape as `StorePort`:

```python
# kernel/src/tasks/ports/task_store.py   (defined in P2, impl lands in P4)
class TaskStorePort(Protocol):
    # tasks
    async def save_task(self, task: Task) -> None: ...                      # owner from task.user_id
    async def load_task(self, user_id: str, task_id: str) -> Task | None: ...
    async def list_tasks(self, user_id: str, *, project_id: str | None = None,
                         status: str | None = None, limit: int = 50, offset: int = 0) -> list[Task]: ...
    async def list_active_tasks(self, user_id: str | None) -> list[Task]: ...   # None ⇒ recovery sweep
    async def update_task_status(self, user_id: str, task_id: str, status: str) -> None: ...  # asserts transition

    # task events (kernel-pattern: autoincrement id cursor + event_uid idempotency)
    async def append_task_event(self, user_id: str, task_id: str, event: TaskEvent,
                                *, request_id: str | None = None) -> int | None: ...
    async def get_task_events_after(self, user_id: str, task_id: str, *,
                                    after_seq: int = 0, limit: int = 200) -> list[StoredTaskEvent]: ...

    # task sessions (the lead + member index)
    async def save_task_session(self, user_id: str, row: TaskSession) -> None: ...
    async def load_task_session(self, user_id: str, task_id: str, session_id: str) -> TaskSession | None: ...
    async def list_task_sessions(self, user_id: str, task_id: str) -> list[TaskSession]: ...
    async def update_task_session_by_session(self, user_id: str, session_id: str, **fields) -> None: ...
```

`list_active_tasks(None)` is the cross-owner recovery sweep — the exact `user_id=None`
escape hatch `list_sessions` already documents for kernel orphan scans.

### 15.5 `WriteThroughTaskStore` — dual-write

A task-shaped sibling of `WriteThroughStore`, with **identical authority
semantics** (do not re-derive them):

- **`authority="durable"`** (remote / SaaS sandbox): the **durable DataService is
  the system of record** — reads + the event id cursor come from durable, and the
  durable append is **fail-loud** (must land before returning; the sandbox is
  ephemeral). The local `kernel.db` copy is a best-effort buffer (`_buffer_local`,
  non-fatal on failure).
- **`authority="local"`** (`pg` tier, resident): local authoritative + durable
  best-effort via the `DurableOutbox` replay queue.
- **Each store owns its own `task_events` autoincrement**; return the authority's;
  `event_uid` bridges identity. **Never** pass an explicit id to the other store.

The local `SQLAlchemyTaskStore` and the durable one are the *same* impl over
different engines — exactly like `SQLAlchemyStore`. The factory
`build_task_store(store)` (from §14.8) constructs the write-through wrapper only
when durable is genuinely distinct from local (a co-located DSN collapses to a
single write).

### 15.6 DataService RPC + `store_wire` extension

Add task ops to the DataService (`kernel/app/data_service.py`, or a co-located
`task_rpc` router mounted by the same app) — same `POST /rpc/{op}`, same
`OwnerDep`/`StoreDep`, same `{"data": ...}` envelope, same **anti-spoof** (owner
forced from token, body `user_id` ignored; PG `install_rls_guc` backstop):

```
save_task · load_task · list_tasks · list_active_tasks · update_task_status
append_task_event · get_task_events_after
save_task_session · load_task_session · list_task_sessions · update_task_session_by_session
```

Add `store_wire` converters (`task_to_row`/`row_to_task`, `task_event_to_row`/…,
`task_session_to_row`/…) beside the existing session/message/event ones — the
wire currency stays plain dict rows, never domain dataclasses.

### 15.7 Data migration — `task_colocate` boot step

Mirror `boot/kernel_db_colocate.py` (do **not** invent a new pattern):

- **New boot step** `boot/task_colocate.py` — copies host `valuz_task` /
  `valuz_task_event` / `valuz_task_session` (in `valuz.db`) → the new kernel
  `tasks`/`task_events`/`task_sessions` (durable). Runs **early** (right after schema
  bootstrap, before the durable task store is read).
- **sqlite-only, insert-only, idempotent, count-gated** — a task already present in
  the target is skipped; nothing is updated/deleted; a fast no-op after the first
  boot. Backup `valuz.db` once before the first seed (`.bak-pretaskcolocate`),
  keeping the first backup if a prior run left one.
- **Owner preserved** verbatim (`user_id` copied straight through; the durable store
  stamps it at the boundary anyway).
- **PG/remote durable** → not a local co-locate case (return early); the SaaS path
  seeds through the DataService, same as sessions.

**Reversible + data-preserving** (repo rule): the copy is insert-only and backed
up, so it is trivially reversible by restoring the backup. The old `valuz_task*`
tables are **left in place** for one release (belt-and-suspenders); a **follow-up
host Alembic migration** drops them once the kernel path is proven — with a
`downgrade()` that recreates them. Do not drop in the same release that introduces
the copy.

### 15.8 Two schema paths — Alembic (local) vs create_all (durable)

The kernel schema lives in **two** places with **two** creation mechanisms — get
this right or the durable copy silently lacks the tables:

- **Local `kernel.db`** — owned by the **kernel Alembic chain** (`alembic/kernel/`,
  `alembic_version`). Add a reversible revision that creates
  `tasks`/`task_events`/`task_sessions` (with the `event_uid` unique index, the
  status check constraints, the cursor indexes); `downgrade()` drops them. Numeric
  revision id only (`"00NN"`); SQLite `ALTER` is limited — batch ops if altering
  later.
- **Durable copy (host `valuz.db`, or PG in SaaS)** — **NOT Alembic.** The durable
  schema is `create_all`'d from the kernel `Base` via
  `ensure_host_data_service_schema` (the same path that already builds the durable
  `sessions`/`messages`/`events`). Adding the three models to the kernel `Base`
  makes them materialize there automatically — no separate durable migration.
- **Host chain** (`alembic/host/`, `alembic_version_host`) — only the **later**
  reversible revision that retires `valuz_task*` (after the copy is proven).

### 15.9 Query wiring (unchanged contract, durable read source)

Per D2 the host keeps the public read/stream path (§4.3). After P4:

- `GET /v1/tasks…` and the task SSE read the **durable** task tables (the
  DataService write target) — in OSS that is `valuz.db`, in SaaS the central PG,
  and for an ephemeral sandbox the host reads durable directly (the
  `DataServiceReadClient` pattern the kernel `events` already uses so a dead
  sandbox still serves history).
- The 500 ms DB-poll SSE (`_iter_task_events_sse`) pages on `task_events.id` (§15.3)
  — same `?after_seq=` wire, durable cursor.

### 15.10 Migration steps within P4 (ordered)

1. **ORM + Alembic**: add the three kernel task models + the reversible kernel
   revision (create only; no data yet). Green schema bootstrap.
2. **`store_wire` converters** + the `SQLAlchemyTaskStore` impl (local + durable, one
   class).
3. **`WriteThroughTaskStore`** + `build_task_store` factory; bind it behind
   `TaskStorePort` (replacing P2's host-wrap). In-process (`local` authority path)
   green first.
4. **DataService RPC ops** + `store_wire` wire; extend the contract test.
5. **`task_colocate` boot step**; run early; verify counts + backup.
6. **Retire path**: leave `valuz_task*` in place; schedule the host-chain drop for a
   later release.

Steps 1–3 land the dual-write on the resident path; 4 lights up the sandbox
(`remote` authority); 5 preserves existing installs' history.

### 15.11 Contract test + regression

- **Extend `test_data_service_contract.py`**: every `TaskStorePort` method has one
  `/rpc/{op}` route; task rows round-trip through the wire with no field drift.
- **Idempotency**: a retried `append_task_event` with the same `event_uid` returns
  the original id and inserts no second row (the `uq_task_events_owner_uid` guard) —
  the same test the kernel `events` table has.
- **Authority parity**: under `authority="durable"`, a durable append failure is
  fail-loud (raises) while a local-buffer failure is swallowed; under
  `authority="local"`, the reverse (durable failure → outbox, local fail-loud).
- **Migration idempotency**: running `task_colocate` twice copies once; a task
  already in target is skipped; the backup is taken once.
- **Cursor regression**: task SSE `?after_seq=` still delivers a gapless, monotonic
  stream after the switch from per-task `sequence` to durable id.

### 15.12 Open edges (decide during the P4 build)

1. **Per-task `sequence` drop.** §15.3 recommends replacing it with the durable
   autoincrement id. Verify no frontend/consumer treats the old `sequence` as a
   stable business value before deleting the column.
2. **Task-event `types` filter.** The kernel `get_events` supports a `types=` filter
   for O(matches) reads; decide whether `get_task_events_after` needs the same for
   the panel's `task_plan_update`-only reads, or whether the panel already filters
   client-side.
3. **Drop timing.** Confirm the release gap between introducing the copy and
   dropping `valuz_task*` — one release is the safe default; longer if SaaS rollout
   is staged.

---

## 16. P5 detail — task tools served kernel-side

The full design for **P5**, which delivers **D5**: the task built-in MCP tools are
served **kernel-side**, so a sandboxed task's tool calls resolve inside the sandbox
(local persistence + DataService write-back to host) with no host round-trip.

### 16.1 Today vs after

Today the task tools (`dispatch`, `await_members`, `send`, `list_members`,
`finish_task`, `update_deliverable`, `stop_subtask`, `plan_task`, `get_plan`,
`modify_plan`, `review_subtask` + the base set `create_task`/`draft_task`/
`commit_task`/`abandon_task`/`inject_into_task`/`resume_task`/`list_tasks`/
`get_task`) are served by the **host** toolkit MCP server
(`integrations/toolkit_mcp_server.py`, mounted at `/_internal/mcp/toolkit/{base,lead}`),
referenced from `session.mcp_servers` as the `harness` entry, with owner injected
via `HostExecContext(ExecContext)` at the `_call_tool` boundary.

After P5 the **task** tools become **kernel-native** — registered in the kernel
tool registry (`src/core/tool_registry.py`) and served by the kernel's own MCP
toolkit at a **kernel/sandbox-local address**. The handlers call the in-kernel
`TaskOrchestrator` directly, removing the temporary host bridge (§14.5).

### 16.2 Owner from the session, gate from kernel state

- **Owner.** Handlers read owner from **`session.user_id`** (every task action runs
  in a session context that carries the owner), **not** from `ExecContext`. This
  keeps `ExecContext` owner-agnostic ([[builtin-mcp-user-id-context-break]]) while
  giving handlers the owner they need — replacing the host `HostExecContext`
  injection.
- **Lead-gate.** `_check_lead_gate` / `_check_plan_writer_gate` key on the task-
  session **role** (lead vs member), which is now kernel-owned state
  (`task_sessions.kind`) — cleaner and more authoritative than today's handler-only
  gate.

### 16.3 The `mcp_servers` wiring handoff (from §13.8)

This is the single line that changes hands between P3 and P5:

- **Before P5:** the resolver injects `always_on_http_mcp_servers(..., toolkit="lead"|"base")`
  — the task toolkit as a **host URL** in `session.mcp_servers`.
- **After P5:** the resolver injects **host-owned MCP only** (docs / schedules /
  connectors + the agent's own servers); the **kernel** appends the **native task
  toolkit** to `session.mcp_servers` at session creation, pointing at its own
  in-process/sandbox-local toolkit — no host URL for task tools.

**Scope fence.** Only the **task** tools move. The other harness tools that share
today's toolkit server (`memory`, `submit_skill`, non-task orchestration) and the
host builtin MCP (docs / schedules / connectors) **stay host-served** — their owner
context and callback wiring are explicitly out of scope for this refactor. P5
**splits** the toolkit along the task boundary; it does not empty the host server.

### 16.4 Sandbox address (D5)

When the kernel is sandboxed, its MCP toolkit is reachable at a **sandbox-local
address** (the agent runtime connects to it within the sandbox, e.g. a loopback
port or stdio). A task tool call therefore never leaves the sandbox: the handler
mutates local task state and the write-back to the host durable store rides the
DataService (§15) — identical to how the kernel's own tables behave. This is the
"local persistence + DataService API write-back" symmetry D5 asks for.

### 16.5 Migration steps within P5 (ordered)

1. **Register** the task tools in the kernel tool registry (declarations → kernel
   `ToolDef`s); handlers call `TaskOrchestrator` directly.
2. **Owner + gate**: handlers read `session.user_id`; lead-gate reads
   `task_sessions.kind`.
3. **Serve**: the kernel MCP toolkit exposes the task tools; the kernel injects the
   native toolkit into `session.mcp_servers` at create; delete the `toolkit=` arm
   from the resolver's `always_on_http_mcp_servers` (§16.3).
4. **Remove** the temporary host bridge (§14.5): the host toolkit MCP server no
   longer serves task tools; host handlers for task tools are deleted.
5. **Sandbox address**: publish/consume the sandbox-local toolkit address.

### 16.6 Test & regression

- Every task tool resolves through the kernel toolkit with the same args/results as
  the host toolkit did (golden parity).
- A member session cannot call lead-only tools (gate on `task_sessions.kind`).
- In a sandbox, a task tool call does **not** hit the host (assert no host-toolkit
  request), while the resulting task-event write **does** land in the host durable
  store via DataService.
- Non-task harness tools (`memory`, `submit_skill`) still work host-served
  (unchanged).

### 16.7 Open edges

1. **Toolkit transport in-sandbox** — loopback HTTP vs stdio MCP for the kernel's
   own toolkit. Pick the one the runtimes' MCP clients already speak most cheaply.
2. **`harness` entry identity** — keep the `harness` server name so runtimes and
   existing sessions resolve it unchanged, even though it now points kernel-local.

---

## 17. P6 detail — events, side effects, public API, live interrupt

The full design for **P6** — the wiring that makes the moved engine observable and
controllable, completing **D2**'s host-terminated query side and closing the live
interrupt/stop path (§gap #4) and the activity-projection path (§gap #6).

### 17.1 Public HTTP — host surface preserved, delegates to the kernel

`api/routes/tasks.py` **stays host-side unchanged in shape** (the frontend surface
is preserved deliberately; a later frontend refactor may revisit it). Each route
delegates to the kernel through new `KernelClient` task methods (both transports),
exactly as the store/session routes already do:

| Route (host, unchanged) | Delegates to |
|---|---|
| `POST /v1/projects/{id}/tasks` (kickoff), `:draft`, `:commit`, `:abandon`, `:inject` | `KernelClient.task_*` → `TaskOrchestrator` |
| `POST /v1/tasks/{id}:intervene` (note / revise_goal / pause / resume / **stop**) | `KernelClient.task_intervene` (§17.3) |
| `GET /v1/tasks…`, `/events`, `/plan` | host read path over the **durable** task tables (§15.9) |
| `GET /v1/tasks/{id}/events/stream` (SSE) | host DB-poll over durable `task_events` (§17.2) |
| `POST /v1/runs/{session_id}:stop` | `KernelClient.stop_member` |

The host computes turn enrichment (attachments + `additional_context`) at the
kickoff/inject entry points and passes it into the kernel call (§14.9 A).

### 17.2 Task SSE — durable cursor, host-terminated

The existing 500 ms DB-poll SSE (`_iter_task_events_sse`) keeps its `?after_seq=`
contract, now paging on `task_events.id` (the durable autoincrement, §15.3) read
from the durable store. For an **ephemeral** sandbox the host reads history
straight from the durable store (the `DataServiceReadClient` pattern the kernel
`events` already uses), so a dead sandbox still serves task history; live deltas
arrive as the durable rows land.

> Alignment note: an in-flight branch is unifying event delivery onto a user-level
> control-plane SSE (`GET /v1/stream`) to replace client polling. Task SSE should
> land on whichever delivery mechanism is current when P6 executes — the
> **durable-cursor semantics here are transport-independent** and hold either way.

### 17.3 Live interrupt / stop — one in-kernel fan-out (§gap #4)

Because the sandbox unit is the **task** (§14.1), a stop/interrupt touches exactly
**one** kernel (the task's) — there is **no cross-sandbox fan-out**. The path:

```
host route  POST /v1/tasks/{id}:intervene {action: stop|pause}
   └─► KernelClient.task_intervene(user_id, task_id, action)      (one call)
         └─► TaskOrchestrator.stop_task(task_id)      [in the task's kernel]
               ├─ set task status (assert_transition)
               ├─ LiveMemberRegistry.drain_members(task_id)  → for each live member:
               │     ├─ mailbox.post(member, shutdown)        (in-process queue)
               │     └─ SessionOrchestrator.interrupt(member) (kernel-native)
               └─ mailbox.post(lead, shutdown)
```

All fan-out is **in-process, synchronous-to-schedule** over the in-memory
mailbox/registry — the exact substrate §14.1 preserved. This also fixes the known
stop/pause **de-spin** gap ([[valuz-task-stop-state-mismatch]]): `stop_task`
parks members and the panel de-spins gated on the task status, closed here rather
than carried over. `pause` is the same path minus the terminal status; `resume`
re-drives via recovery (§14.7).

### 17.4 Activity / recents / project reverse-lookup (§gap #6 — verified)

**Verification result** (checked against the code): kernel `create_session`
(`sessions.py:159`) emits **no** lifecycle event — it is a silent `save_session`;
the cross-session stream (`subscribe_all_events` / `_global_taps`) only carries
events that flow through a **running** session's event bus, which creation does not.
Today the host pairs every create with an explicit `project_index.record(...)`
(chat sites + task sites). Given that, the elegant resolution needs **no new kernel
event**:

- **Recents/activity is chat-scoped.** `ProjectSessionRow` + `touch_activity` serve
  **chat** conversations (`run_orchestrator.py:121`, the chat turn path). The task
  activity view reads the **task tables** (`queries.list_activity_tasks_page`), not
  the index; `list_session_ids(user_only=True)` even filters task kinds out. Chats
  are host-created, so the host keeps `record` / `touch_activity` exactly as today —
  **no task/kernel change.**
- **Task sessions drop their index records.** After the move they are created
  kernel-side and no longer call `project_index.record`. Nothing in recents needs
  them (task recency comes from the task tables).
- **`project_of(session_id)` reverse-lookup** — the one load-bearing use of the task
  records (3 callers: `tools_agent_proposal`, `docs_mcp_server`, `agents` route),
  which may receive a task-session id. Resolve it from the durable session's
  **`metadata.valuz.project_id`** (every session — chat, lead, member — carries it;
  the host already reads durable sessions), with `task_sessions` as a fallback. This
  removes the last reason a task session needed a `project_index` row.

→ **No `session.created` event, no kernel→host callback.** The index stays
host-driven at the host creation points; task sessions are simply out of it.

**Deferred (per the frontend-preserve decision, §gap #9):** derive chat recents at
**read time** from the durable `sessions` + `messages` tables (last-activity =
`MAX(messages.started_at)`), retiring the maintained index entirely (only the
host-only `queue_paused_at` stays in a minimal side table). A sessions-module
rewrite — later, not now.

### 17.5 Side effects — event-first (from §8)

Decision inbox, notifications, and memory scheduling become **host reactions to
task events** (which the host owns the query path for, D2), not synchronous kernel
callbacks:

- **Decision inbox / AskUser** — "a member is parked on a clarifying question" is a
  **kernel-native** fact (the runtime's pending-action / `AskUserQuestion` state);
  the kernel emits `awaiting_user` / `user_answered` **task events**, and the host
  decision inbox becomes a pure **read-projection** over them. No host aggregator
  probe from inside the actor loop.
- **Notifications / memory** — the host subscribes to terminal task events
  (`task_failed`, `task_completed`) and drives its notification ledger + memory
  scheduler from there.
- Member-attributed events stamp `payload.agent_name` at emit
  ([[valuz-task-event-member-name]]).

### 17.6 Migration steps within P6 (ordered)

1. **KernelClient task methods** (kickoff/draft/commit/abandon/inject/intervene/
   stop_member + reads) on both transports; `api/routes/tasks.py` delegates.
2. **Enrichment at entry points** — host route handlers compute attachments +
   `additional_context` and pass them into the kernel call (§14.9 A).
3. **SSE** over durable `task_events` (§17.2).
4. **Live interrupt/stop** fan-out (§17.3) + panel de-spin.
5. **Activity projection** event-driven (§17.4); ensure `session.created` emission.
6. **Side-effect reactions** event-first (§17.5): decision inbox read-projection,
   notifications, memory.

### 17.7 Test & regression

- Live stop mid-task: all members interrupt, lead shuts down, panel de-spins, task
  reaches a terminal status — in **both** topologies.
- SSE gapless/monotonic on the durable cursor across a reconnect; a dead sandbox
  still serves history.
- Activity feed updates on create/turn/finish without any kernel→host callback.
- AskUser: a parked member surfaces in the decision inbox purely from task events.

---

## 18. Out of scope — session/task sandbox start-stop control (commercial)

The north-star **control plane** — *who* starts/stops a task's sandbox and *when*
(idle-stop, resume-on-open, per-request provisioning), and the evolution of the
current per-**user** `_kernel_for(user_id)` allocator toward per-**task**
granularity — is a **commercial** concern and is **not designed here**. This
refactor is its **foundation**: it guarantees the task=sandbox semantic (§14.1),
puts the entire actor lifecycle behind the kernel seam (D3), and makes the host a
stateless control+data plane — the preconditions a start-stop controller needs. The
controller itself is a follow-on (call it P8), built in the commercial overlay on
top of this base. Tracking it here only to state the boundary: **delivering §§1–17
is the whole of this refactor; P8 builds on it later.**
