# Task Attention & Reliability — 强提醒、后台监控、可重试

> Design for the three task-UX gaps raised in 2026-07: (1) mid-task user
> questions are under-surfaced, (2) halted tasks felt dead-ended, (3) task
> failures produce no alert, no background monitoring, and no retry story.
> Gap (2) is fixed (resume-with-instruction + inject-revive, this PR); this
> document designs the remaining attention/monitoring/retry system.

## 0. Ground truth (verified 2026-07-12)

- **There is no `failed` task status** — task-level failure folds into
  `blocked` (recoverable) or `stopped` (user-driven). The backend never emits
  `task_failed`; the real failure events are `task_blocked`, `subtask_failed`,
  `kickoff_failed`. (`task_state.py`, `lifecycle.py::_auto_finalize_lead_task`)
- **User questions** ride the kernel `requires_action(clarifying_questions)`
  event → host `DecisionAggregator` → Decision Inbox (`/v1/decisions/pending`
  + `/v1/decisions/stream` SSE). Both **lead and member** sessions are
  included (`decisions/service.py::is_task_driven` gates on
  `run_kind ∈ {lead, subtask}`); plain chats are deliberately excluded (their
  question renders inline on the page the user is already on).
- The frontend inbox subscription is a **process singleton**
  (`use-decision-inbox.ts`: store `_inited` flag + module-level stream handle
  + 60s poll backstop) — safe to mount from multiple components; only one SSE
  connection ever opens.
- `DecisionInboxProvider/Badge/Drawer` mount in `ProjectLayoutBase`, which
  wraps **every** `layout: "project"` route (conversation, tasks, agents,
  settings, activity…). Only standalone routes (onboarding, /welcome,
  /auth/api-key) sit outside — inbox coverage is already near-global in-app.
- **No OS-level notification exists anywhere** (no Electron `Notification`,
  no dock badge, no tray badge, no flashFrame).
- **No live watchdog for tasks** — reconciliation is boot-only
  (`recover_active_tasks`, only sweeps `active`). Automations DO have one
  (`automations/failure_monitor.py`, ADR-012).
- **No in-turn LLM retry — and we keep it that way (by design).** A 429/500
  terminates the turn; lead → task `blocked`, member → node `rework`. This is
  the desired behaviour: the error is surfaced for intervention, not retried
  away. Resume-time member re-run (`RESUME_RETRY_CAP = 3`) is restart
  continuity, not live-error auto-retry (see §3).

## 1. Attention model — one derived "needs you / went wrong" signal

Rather than inventing a new push channel per concern, define **one attention
taxonomy** and drive every surface (task page, Activity, sidebar, toast, OS
notification) from the same two existing streams:

| Attention kind | Source of truth (already exists) | Today's surfacing | Target |
|---|---|---|---|
| `awaiting_user` — an agent asked a question | Decision Inbox entry (`task_id` set) | topbar badge, one toast, task-page amber card | + task event, + header chip, + OS notify, + sidebar dot |
| `failed` — task blocked / kickoff failed | `task_blocked` / `kickoff_failed` task events | timeline dot only (no banner, no toast) | + failure banner (done), + toast, + OS notify, + sidebar dot |
| `stalled` — active but nothing happening | (new) watchdog verdict | nothing | `task_blocked(reason=stalled)` → same failed pipeline |

Design rule: **`blocked` is the single "needs intervention" terminal**; the
watchdog and all failure paths converge on it. We do NOT add an
`awaiting_user` task *status* (the question already blocks the turn, the task
is genuinely still `active`, and a status would have to be atomically cleared
on answer — racy). Instead `awaiting_user` stays a **derived overlay**:
`decisionStore.pending.some(e => e.task_id === id)`.

### 1.1 Backend additions

1. **`awaiting_user` task event** (P1) — when
   `coordination.await_member_results` flips `awaiting_user_break = True`, and
   when the aggregator sees a `clarifying_questions` pending whose session is a
   task lead, append a task event `awaiting_user`
   (`payload: {session_id, subtask_key?, question, pending_id}`) and, on
   resolve, `user_answered` (`payload: {pending_id}`). This puts the wait on
   the task's own timeline (today it is invisible there) and gives the SSE
   stream a frame to drive UI from. Emit at most one per `pending_id`.
2. **Stamp `agent_name`** on these events at emit time (established rule —
   see valuz-task-event-member-name).

### 1.2 Frontend additions

1. **Task page header chip** — when `taskPending.length > 0`, show a
   "等待你确认" chip next to the status label (today the header says
   "Running" while the task is actually blocked on the user).
2. **Inline answer on the task page** — replace the navigate-only amber card
   with the existing `DecisionEntryCard` (it already answers inline via
   `sessionsApi.submitAction`); keep "在会话中查看" as the secondary link.
3. **Sidebar / Activity dots** — `DesktopSidebarTaskItem` already supports
   `status: "failed"` (red dot) and generic `badgeDot`; wire them from
   (a) task status `blocked`, (b) decision-store pending per task. Activity
   rows get the same "needs attention" accent.
4. **Toasts on failure frames** — extend the active task page from 3s polling
   to `useTaskEvents` (hook exists, currently completed-only) OR, simpler and
   global: a `TaskAttentionProvider` beside `DecisionInboxProvider` that
   subscribes to a lightweight owner-scoped task-event stream and fires
   `toast.error` on `task_blocked` / `kickoff_failed` (dedup by event id,
   live-only like the inbox toast). Decision-inbox toasts already exist.

### 1.3 OS-level notification (the "强提醒")

Add a minimal Electron notification bridge (main process):

- Preload API: `valuzDesktop.notify({title, body, route})` +
  `valuzDesktop.setBadge(count)`.
- Renderer: one `NotificationBridgeProvider` mounted next to
  `DecisionInboxProvider`, driven by the SAME two streams:
  - new decision pending → `新的确认请求 — {agent}: {question}`,
  - `task_blocked` / `kickoff_failed` → `任务受阻 — {task title}`.
  - Click → focus window + navigate to the task / conversation.
- Badge count = pending decisions + blocked tasks (`app.dock.setBadge` on
  macOS, `setBadgeCount` elsewhere). Fire only when the window is
  unfocused/hidden (tray-resident is the norm); in-app toast covers the rest.
- Settings toggle under 通知 (default on).

## 2. Background monitoring — TaskHealthMonitor (watchdog)

Model on `automations/failure_monitor.py` (periodic sweep, ADR-012), owned by
the tasks module, started in `boot/lifespan.py`:

```
every 60s, for each task with status == "active":
  lead = lead run; ks = kernel_client.get_session(lead.session_id)
  1. zombie lead:    ks.status terminal-with-error AND no live actor loop
                     (mailbox unregistered) AND not awaiting_user
                     → auto-finalize path: task → blocked,
                       event task_blocked(reason="lead_dead", error=…)
  2. stalled:        last task event older than STALL_AFTER (default 30 min)
                     AND not awaiting_user (a pending question is a
                     legitimate indefinite wait)
                     → emit task_stalled_warning (ATTENTION ONLY — never
                       auto-block; a stall is not definitively an error, so we
                       surface it and let the user decide, per the §3 contract)
  3. awaiting_user:  pending decision exists but no awaiting_user task event
                     yet → backfill the event (idempotent by pending_id)
```

Notes:
- The monitor **only converges state and emits events** — all alerting rides
  the §1 pipeline; all recovery rides the existing `resume_task`. No new
  recovery machinery.
- `awaiting_user` gating prevents the classic false positive (a task waiting
  3 days on a human is healthy).
- Boot recovery (`recover_active_tasks`) stays as-is; the monitor covers the
  "process stayed up but a lead silently died" hole that boot recovery cannot.

## 3. Recovery — user-driven, never auto-retry

**Design decision (explicit):** an error — a model call, a lead session, or a
member session — is an *acceptable* outcome. We do **not** try to paper over it
with automatic retry. Auto-retry hides the failure, burns tokens on a problem
it can't diagnose, and gives the user no chance to correct the actual cause
(bad prompt, wrong model, exhausted quota). The contract is instead:

> **异常可监控 → 监控到异常可提醒 → 用户干预。**
> Exceptions are observable; an observed exception raises an attention signal;
> the user (or an explicit future policy) decides whether to re-trigger.

So there is **no turn-level retry** in `actor_runner`. A failed turn propagates
exactly as today: a lead error → `_auto_finalize_lead_task` marks the task
`blocked` + emits `task_blocked`; a member error → node `rework` + emits
`subtask_failed`. Both are *surfacing* events, not silent recoveries.

| Layer | Trigger | Mechanism | Status |
|---|---|---|---|
| Turn-level | any turn error | **none — deliberately no auto-retry.** The error surfaces as `task_blocked` / `subtask_failed`. | by design |
| Resume-level (exists) | member resumable on **restart/resume reconcile** | `RESUME_RETRY_CAP = 3` bounds re-runs of a member that was interrupted by host teardown — this is restart continuity, NOT auto-retry of a live error | keep |
| Task-level | `blocked` | **user-driven** `resume` (now with an optional instruction). No auto-resume — the user is the one who decides to re-trigger, after seeing why it stopped. | shipped |

Also fix the silent path: `_heartbeat_pending`'s member-failure reconcile must
emit `subtask_failed` like every other member-failure path (today it archives
the run invisibly) — so the failure is *observable*.

## 4. Cleanups that fall out

- Remove the frontend's dead `task_failed` mappings or keep them only as
  legacy-row rendering (backend never emits it) — done for `failureInfo`
  (now reads `task_blocked`); sweep `ActivityFeedList` / `LiveTaskCard` next.
- `stop_task` should go through `assert_transition` like every other status
  write (it hand-rolls its guard today).
- Consider promoting `DecisionInboxProvider` mount from `ProjectLayoutBase`
  to the router Root so standalone routes (rare) are covered too — low value,
  the shell already covers all main surfaces.

## 5. Sequencing & status

1. **P0 — SHIPPED:** resume-with-instruction + inject-revive + halted-task
   composer + `task_blocked` failure banner + legacy-`failed` resume.
2. **P1 — SHIPPED:** `awaiting_user`/`user_answered` task events
   (`decisions/aggregator` → `tasks/messaging.record_*`, deduped by
   pending_id); task-page inline answer (reuses `DecisionEntryCard`) + header
   "等待你确认" chip; `_heartbeat_pending` now emits `subtask_failed` (making
   heartbeat-detected failures observable).
   - **Explicitly NOT done (rejected):** turn-level auto-retry. Errors are
     surfaced, not retried (§3).
   - **Still open in P1:** sidebar/Activity attention dots; global failure
     toasts (needs an owner-scoped task-event stream — the decision-inbox
     toast already covers *questions* globally, failures don't have a global
     stream yet).
3. **P2 — MOSTLY SHIPPED:** `TaskHealthMonitor` watchdog
   (implemented; lives in `tasks/recovery.py` since 2026-07-28 — the
   watchdog is the same notice-dead-lead concern as boot recovery — wired in
   `boot/steps.py`; liveness =
   `mailbox_registry.is_registered(lead)`, 2-sweep confirm → `task_blocked
   (reason="lead_dead")`, env `VALUZ_TASK_HEALTH_MONITOR_INTERVAL`).
   **Electron notification bridge + dock badge SHIPPED:** main
   `ipc/notifications.ts` (`desktop_notify` → native `Notification`, click →
   focus + `notification-clicked` route; `desktop_set_badge_count` →
   `app.setBadgeCount`); renderer `NotificationBridgeProvider` (mounted in
   `ProjectLayoutBase`, no-op off Electron) drives questions off the decision
   store (+ badge = pending count) and failures off a new owner-wide
   `GET /v1/tasks/attention` poll (`TaskEventDatastore.list_attention_events_since`,
   cursor by `created_at`, primed to mount time). Pure content builders
   (`notification-content.ts`) unit-tested.
   - **Still open in P2:** live desktop/browser walkthrough of the native
     notification (the renderer logic + main IPC are done and typecheck/unit-
     pass, but the OS-level popup itself needs a real desktop run to eyeball);
     optional auto-resume policy; `stop_task` state-machine alignment.

### What "问题" now delivers
- **Q1 (user questions):** inbox was already global (badge+toast on every
  project-layout route, singleton SSE); now also on the task's own timeline
  (`awaiting_user`/`user_answered`), answerable inline on the task page, with a
  header chip. Remaining: OS notification (P2).
- **Q2 (stopped ⇒ can't continue):** fully addressed — halted tasks resume with
  an optional instruction; chatting to a halted task revives it.
- **Q3 (failure alert / monitor / re-trigger):** the model is **observe →
  alert → user intervenes**, never auto-retry. Failures surface as
  `task_blocked` / `subtask_failed` (heartbeat path now emits too); the failure
  banner reads the real `task_blocked`; a live watchdog catches silently-dead
  leads and converges them to the resumable `blocked` state; the user
  re-triggers via `resume` (with an optional instruction). Remaining: push/OS
  notification so an unobserved failure still reaches the user (P2).
