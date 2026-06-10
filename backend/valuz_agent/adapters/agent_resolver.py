"""agent_resolver — resolve project members and build kernel Sessions for dispatch.

Slice 1 scope (lead-dispatch-mvp §S3):
  resolve_member_agent — project_id + agent_slug → AgentConfig or None.

Slice 2 scope (lead-dispatch-mvp §S3 / H-T6):
  DISPATCH_PLAYBOOK — the §1.5 methodology text injected into lead sessions only.
  build_member_session — constructs a full kernel Session dataclass ready for
    save_session_sync. Lead sessions receive the dispatch playbook; member sessions
    receive only the scoped brief. Caller is responsible for saving the returned
    create request via ``kernel_client.create_session``.

Boundary notes (§S0):
  - kernel Session has NO ``tools`` field — tools live on AgentConfig only.
  - The lead gate is enforced inside each dispatch handler, not here.
  - lead-capable agents must have the 4 dispatch ToolDef declarations on their
    AgentConfig.tools (handler=None); the TaskOrchestrator ensures this at kickoff.
"""

# ruff: noqa: I001 — kernel_bootstrap side-effect import must precede ``from src.core``
from __future__ import annotations

import logging
import os
from uuid import uuid4

import valuz_agent.boot.kernel  # noqa: F401 — ensure kernel sys.path

from app.schemas import (
    CreateSessionRequest,
    ModelSettingsSchema,
)
from src.core import AgentConfig

from valuz_agent.adapters.capability_resolver import (
    always_on_http_mcp_servers,
    always_on_skill_paths,
    resolve_skill_slugs_to_paths,
)
from valuz_agent.adapters.system_prompt_builder import build_project_system_prompt
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Goal-mode brief length guard
# ---------------------------------------------------------------------------
#
# The bundled Claude Code CLI inside ``claude_agent_sdk._bundled.claude`` caps
# ``/goal <text>`` payloads at 4000 characters and rejects anything longer
# with::
#
#     Goal condition is limited to 4000 characters (got NNNN)
#
# That error surfaces mid-turn (after the session has been created, after
# attachments are registered, after agents start spinning up) — by which
# point the user sees an opaque crash, not a fixable input error.
#
# We pre-check inside ``build_member_session`` whenever ``goal_mode=True`` so
# the failure is loud and immediate, with a friendly hint at the call site
# that actually has the raw user prompt. The budget is 3900 — leaves ~100
# chars of headroom for the ``/goal `` prefix and any future runtime padding.
# Subjective hierarchy: a single number kept in one place, reusable from
# tests, is preferable to a literal sprinkled across the 6 brief construction
# sites in ``modules/tasks/orchestrator.py``.

GOAL_BRIEF_MAX_CHARS: int = 3900


class BriefTooLongError(ValueError):
    """Brief exceeds the goal-mode payload limit (3900 chars).

    Subclasses ``ValueError`` so generic ``except ValueError`` paths still
    work — but specific handlers can match this type to format a friendlier
    user-facing message (e.g. "shorten the task description, move long
    context into a reference file").
    """

    def __init__(self, length: int, limit: int = GOAL_BRIEF_MAX_CHARS) -> None:
        self.length = length
        self.limit = limit
        super().__init__(
            f"brief is {length} characters but goal mode is limited to {limit} "
            f"(bundled Claude CLI caps /goal at 4000). Shorten the task goal, "
            f"or move long context into a reference file and pass its path in refs."
        )


def assert_goal_brief_length(brief: str, *, limit: int = GOAL_BRIEF_MAX_CHARS) -> None:
    """Raise ``BriefTooLongError`` when ``brief`` exceeds the goal-mode cap.

    Pure — no side effects. Callers that need the friendly error at task
    *creation* time (chat draft/commit), before any session is built, can
    call this directly with the user's prompt.
    """
    n = len(brief)
    if n > limit:
        raise BriefTooLongError(length=n, limit=limit)


# ---------------------------------------------------------------------------
# Dispatch Playbook (§1.5) — injected into lead sessions only.
# Explains the collaboration protocol to the lead LLM.
# ---------------------------------------------------------------------------

DISPATCH_PLAYBOOK = """\
## Dispatch Playbook (lead session only)

You are the lead for this Task. Drive the WHOLE task in this one turn —
dispatch, collect, review, repeat — until you call finish_task. Protocol:

1. PLAN FIRST. Decompose the goal into a structured subtask plan and record it
   with plan_task(subtasks=[{key, title, goal, agent, review_criteria,
   depends_on}]). Each subtask has a stable `key`, the member `agent`,
   `depends_on` (keys that must finish first; independent subtasks have none),
   and `review_criteria` — the concrete, checkable acceptance bar for THAT
   subtask (what "done" means). Set review_criteria for every subtask: it's
   given to the member so it knows the bar, and shown back to you at review
   time so you judge against your own stated criteria. You CANNOT dispatch
   before you plan. Members/roles are under "Team members" above (or
   list_members()).
2. DISPATCH INDEPENDENT SUBTASKS IN PARALLEL. dispatch(subtask_key) is
   NON-BLOCKING — it returns immediately and the member runs concurrently. For
   every subtask whose deps are satisfied (get_plan() shows ready keys), call
   dispatch once per key, back to back, so they run AT THE SAME TIME. Never
   wait for one independent subtask before starting another.
3. COLLECT with await_members. After dispatching, call
   await_members(mode="any") in a loop: it blocks until the next member
   finishes and returns its SubtaskResult — review that one immediately, then
   loop to collect the rest. (Or await_members(mode="all") to get the whole
   batch at once.) Omit `keys` to wait for all outstanding subtasks.
4. REVIEW each finished subtask: review_subtask(subtask_key, decision, feedback)
   — judge it against that subtask's `review_criteria` (call get_plan to see
   the criteria). "approve" (optionally with a one-line reason) marks it done
   and unlocks dependents; "rework" sends it back with feedback (then dispatch
   that key again). Read result files directly to judge.
5. LOOP: once a batch is reviewed, dispatch the newly-ready dependent subtasks
   (get_plan() to see them) → await_members → review. Repeat until every
   subtask is done. Use modify_plan(...) to add/patch subtasks mid-flight.
6. You are the ONLY agent allowed to dispatch (single layer; members can't).
   NEVER use the built-in `Agent` / `Task` tool to spawn sub-agents — it runs
   a redundant nested agent that BLOCKS you for minutes. Delegate ALL sub-work
   exclusively through `dispatch` + `await_members`. Likewise do not re-do a
   member's work yourself; dispatch it.
7. finish_task IS THE ONLY COMPLETION SIGNAL. EVERY plan node must be done
   first — including a final summary/aggregation node (it becomes ready once
   its deps finish, so dispatch + review it like any other). finish_task with
   status="completed" is REJECTED while any node is still planned/in_progress/
   in_review/rework; it returns the pending keys — dispatch and review them,
   then finish. Keep orchestrating until the goal is truly achieved; do NOT
   stop just because intermediate results look complete. Then call
   finish_task(summary, artifacts, status="completed") (list key result files
   in `artifacts`); use status="stopped" only when the user explicitly asked
   you to stop via an injected instruction, or when the goal has become
   unreachable. Do not continue working after finish_task. (Task-level
   "failed" is not a valid status — use "stopped".)
8. RECOVERY: if your session is resumed after an app restart or user stop, you
   may receive a <system-recovery> reconcile brief. ALWAYS call get_plan FIRST
   to align with the reconciled truth (members may now be in_review, rework, or
   re-running) before dispatching, reviewing, or finishing — never assume the
   pre-restart state still holds.
"""

# Single unified protocol now that dispatch is non-blocking + await_members
# collects (v0.14). The async kickoff path reuses the same playbook.
DISPATCH_PLAYBOOK_V2 = DISPATCH_PLAYBOOK


# Playbook variant for leads spawned from a chat draft commit_task path
# (VALUZ-CHATPLAN). The plan is already laid out (and signed off by the user),
# so the lead skips step 1 "PLAN FIRST" and goes straight to dispatch. The
# handler-level gate also rejects plan_task when ``plan`` is non-empty, so this
# is belt-and-suspenders: tell the model the right path, AND refuse the wrong
# call if it tries anyway.
COMMITTED_LEAD_PLAYBOOK = """\
## Dispatch Playbook (lead session — plan pre-committed)

You are the lead for this Task. Your plan was ALREADY laid down and approved
by the user during a chat draft session — DO NOT call plan_task (the handler
will reject it because the plan is non-empty). Drive execution in this one
turn until finish_task. Protocol:

1. READ THE PLAN. Start by calling get_plan() to see the committed subtask
   DAG: each node carries a stable `key`, target `agent`, dependencies, and
   the `review_criteria` the user signed off on. The response includes
   `current_version` — remember this for any modify_plan call you make.
2. DISPATCH INDEPENDENT SUBTASKS IN PARALLEL. For every key whose deps are
   already satisfied (the `ready` list from get_plan), call dispatch(key)
   back-to-back — dispatch is NON-BLOCKING, members run concurrently. Never
   serialize independent work.
3. COLLECT with await_members(mode="any") in a loop, reviewing each result as
   it arrives. Use await_members(mode="all") only when you genuinely need
   the whole batch at once before continuing.
4. REVIEW each finished subtask with review_subtask(key, decision, feedback)
   — judge against the node's `review_criteria`. "approve" marks done and
   unlocks dependents; "rework" sends it back with feedback (then dispatch
   that key again). Read result files directly to judge.
5. EXTEND THE PLAN IF NEEDED. If during execution you discover the plan
   needs new nodes (a missed step, a dependency to verify, a follow-up the
   user implicitly wanted), call modify_plan(add=[...], expected_version=N)
   where N is the last `current_version` you saw. On
   PLAN_VERSION_CONFLICT, call get_plan() to refresh and retry — someone
   else (a user inject) may have just edited the plan from a chat session.
6. You are the ONLY agent allowed to dispatch (single layer; members can't).
   NEVER use the built-in `Agent` / `Task` tool to spawn sub-agents.
7. finish_task IS THE ONLY COMPLETION SIGNAL. Every plan node must be done
   first. Call finish_task(summary, artifacts, status="completed"); use
   status="stopped" when the user explicitly asked you to stop via an
   injected instruction, or when the goal has become unreachable. Do not
   continue working after finish_task. (Task-level "failed" is not a valid
   status — use "stopped".)
8. EXTERNAL INSTRUCTIONS. You may receive turn-boundary messages tagged
   <user-instruction source="chat"> — these are user follow-ups injected
   from the chat that drafted you. Read them as authoritative user intent;
   typically translate them into modify_plan + dispatch (or rework for an
   in-flight subtask), then continue.
9. RECOVERY: if your session is resumed after an app restart, you may
   receive a <system-recovery> reconcile brief. ALWAYS call get_plan FIRST
   to align with the reconciled truth before dispatching or reviewing.
"""


# Max chars of a member's instructions surfaced in the lead roster / list_members.
ROLE_SUMMARY_LIMIT = 400


# Playbook nudge appended to project-conversation agents (i.e. chat sessions
# in a project that can spawn tasks). Teaches the model when to use
# ``draft_task`` vs ``create_task`` and when to ``inject_into_task`` instead
# of starting a new task mid-execution. NOT applied to lead/member agents —
# their playbooks (DISPATCH_PLAYBOOK / COMMITTED_LEAD_PLAYBOOK) cover their
# specific orchestration role.
CHAT_TASK_PLAYBOOK = """\
## Task playbook (chat mode)

You are the user's chat partner inside a project. When the user asks
you to "do" something that needs orchestration (multiple steps, parallel
sub-work, or a longer-running job), follow this flow.

### Step 0 — EXISTING-TASK CHECK (do this FIRST, before any draft/create)

Most "感知不到子任务" mistakes come from skipping this. If the user's
message refers to a task that ALREADY EXISTS — by name ("那个打豆豆任务"),
by reference ("刚才那个任务", "上面的 task"), or by naming a subtask to
**add / change / remove** within it — you MUST locate that task and modify
it IN PLACE. **Do NOT draft_task / create_task a new one** — re-creating a
task the user meant to amend is the #1 failure mode.

  a. Call ``list_tasks(mine_only=true)`` (optionally with ``status``) and
     pick the task the user means. ``get_task(task_id)`` + ``get_plan(
     task_id)`` to read its current subtask DAG (keys, agents, statuses).
  b. Route by the task's status — the goal is to touch SUBTASKS of the
     EXISTING task, not spawn a new task:
       - ``active`` / ``paused`` → ``inject_into_task(task_id, text)`` with
         a clear instruction ("加一个子任务 X：…" / "把子任务 Y 改成 …" /
         "删掉子任务 Z"). The running lead turns it into modify_plan +
         dispatch. (For ``paused``, ``resume_task`` first, then inject.)
       - ``blocked`` / ``stopped`` → ``resume_task(task_id)`` to revive the
         lead, then ``inject_into_task`` with the subtask change.
       - ``completed`` → **区分场景** (judge the user's intent):
           · SUPPLEMENT / ADJUST subtasks of the SAME goal ("再补一个子任务"
             / "那一步重做一下") → ``resume_task(task_id)`` to REOPEN it
             (flips back to active), then ``inject_into_task`` / the lead
             does modify_plan for the new/changed nodes.
           · A genuinely NEW direction (different goal that merely builds
             on the old result) → ``draft_task`` a fresh FOLLOW-UP task,
             passing the old task via ``refs`` so context carries over.
  c. Only when the request is a brand-new goal with NO matching existing
     task do you proceed to the NEW-TASK flow below.

You may modify the plan of a DRAFT task directly with ``modify_plan``
(no lead yet); for a RUNNING task, subtask edits go through the lead via
``inject_into_task`` (you don't dispatch from chat).

### New-task flow (only when Step 0 found no existing task to amend)

1. KNOW THE TEAM FIRST.

   ⚠️ ``list_members`` is an MCP TOOL CALL — it returns the project's
   agent roster (slugs + role_summary) from the database. It is NOT a
   filesystem lookup. **DO NOT** use Bash / Read / ls to search for
   agents — this project's team is NOT defined in ``.claude/agents/``
   or any other directory. Agents are project-scoped DB rows,
   accessible only via the ``list_members`` MCP tool.

   Hard rule: BEFORE the FIRST ``draft_task`` / ``create_task`` of a
   conversation, you MUST emit a ``list_members()`` tool call and read
   its result. Skip this and the next call will fail with
   ``agent <slug> is not a member of project`` — slugs you invent
   (``claude``, ``assistant``, ``lead``, ``Frontend Engineer``, …)
   are not real members.

   This rule is for TASKS only. It does NOT apply to the ``automation``
   tool: a chat automation defaults to the agent you are already talking
   to, so you can create it directly WITHOUT list_members. (And in a chat
   with no project, list_members is expected to be empty — never read that
   as "no agent available".)

   Map user intent → real ``agent_slug`` from the roster yourself —
   the user thinks in role names ("研究员") while the API needs slugs
   ("research-director"). The roster includes a one-line
   ``role_summary`` for each member so you can pick well.
2. PROPOSE A DRAFT. Call ``draft_task(goal, lead_agent_slug, refs?,
   title?)`` to open a task in ``status=draft`` — using a real
   ``lead_agent_slug`` from step 1. No lead session starts; no tokens
   are spent on execution yet.
3. LAY DOWN A PLAN. Immediately call ``plan_task(task_id,
   subtasks=[...])`` with the decomposition you propose — each subtask
   gets a stable ``key``, target ``agent`` (again, from the real team
   roster), ``review_criteria``, and ``depends_on``. Then summarise the
   plan back to the user in plain text so they can review it inline.
4. ITERATE WITH ``modify_plan``. If the user pushes back ("change
   step 3 to X", "add an EVA model"), call ``modify_plan(task_id,
   add=..., update=..., expected_version=N)`` where ``N`` is the
   ``current_version`` you last saw via ``get_plan``. On
   ``PLAN_VERSION_CONFLICT``, refresh with ``get_plan`` and retry —
   another chat session may have touched the plan.
5. WAIT FOR EXPLICIT "GO" BEFORE ``commit_task``. The user must clearly
   say "execute" / "run it" / "OK 启动" — ask if it's ambiguous. Then
   call ``commit_task(task_id)`` to spawn the lead session and start
   real work. Do NOT auto-commit just because the plan looks complete.
6. ABANDON ON USER REQUEST. If the user says "forget it" / "drop it",
   call ``abandon_task(task_id, reason)`` — terminal, no lead starts.

Mid-execution intervention. If the task is already running (you see
an entry from ``list_tasks(mine_only=true, status="active")``) and the
user adds an instruction like "also add a competitor analysis":

  → Call ``inject_into_task(task_id, text)`` to push the instruction
    into the lead's mailbox. DO NOT start a new task. The lead reads
    the message at its next turn boundary and typically translates it
    into a ``modify_plan`` + ``dispatch``.
  → If ``delivered=false`` with ``reason=LEAD_OFFLINE``, the lead has
    already finished — see "Reviving a stopped lead" below.

Reviving a paused/blocked/stopped/completed lead. If the user wants to
continue (or reopen) a task whose lead has gone away:

  → Call ``resume_task(task_id)``. This respawns the lead session and
    flips the task back to ``active``; you can then inject_into_task as
    normal. Qualifying sources:
      - ``paused`` — REST /intervene action=pause
      - ``blocked`` — auto-finalize couldn't close (or lead turn crashed)
      - ``stopped`` — the user previously stopped it (typical: "停止此任务"
        then they change their mind).
      - ``completed`` — REOPEN a finished task to supplement/adjust its
        subtasks (区分场景: only when the user is amending the SAME goal;
        a brand-new goal → fresh follow-up draft_task instead).
  → Only ``abandoned`` CANNOT be resumed — a discarded draft has no plan
    to revive; if the user wants that plan back, draft_task + plan_task
    from scratch.

Quick rules of thumb:
  - The user references an EXISTING task / names a subtask to add/change
    → Step 0: inject_into_task (active/paused) or resume_task then inject
    (blocked/stopped/completed). NEVER create a new task for an amendment.
  - Single-step / one-off answer → answer in chat directly. No task.
  - Brand-new multi-step goal / "go produce X" → draft_task + plan_task,
    confirm, then commit_task.
  - User talks while a task runs → inject_into_task.
  - User wants to continue/reopen a paused/blocked/stopped/completed task
    → resume_task (then inject the change).
  - Genuinely new goal that builds on a finished task → new draft_task
    with ``refs`` to the old one (follow-up), not a reopen.
"""


def summarize_role(instructions: str | None) -> str:
    """Condense an agent's instructions into a one-paragraph role summary.

    Used both for the lead's team roster (system prompt) and the
    ``list_members`` tool return so the lead can dispatch accurately.
    """
    text = (instructions or "").strip()
    if not text:
        return ""
    # Collapse whitespace; keep it to a single readable paragraph.
    flat = " ".join(text.split())
    if len(flat) <= ROLE_SUMMARY_LIMIT:
        return flat
    return flat[:ROLE_SUMMARY_LIMIT].rstrip() + "…"



async def _member_agent_config(member, members: ProjectMemberDatastore):  # noqa: ANN001, ANN202
    """Build the member's AgentConfig from its source library row.

    The kernel has no agents table — the library AgentRow is the single
    source of truth and the config is built in memory (and embedded into
    sessions as their snapshot). Members created before provenance landed
    (``source_agent_slug`` NULL, despite the 0003 backfill) resolve to None.
    """
    if not member.source_agent_slug:
        logger.warning(
            "member %s/%s has no source_agent_slug — cannot build agent config",
            member.project_id,
            member.agent_slug,
        )
        return None
    from valuz_agent.modules.agents.datastore import AgentDatastore
    from valuz_agent.modules.agents.service import AgentService

    db = members._db  # noqa: SLF001 — same unit of work as the member lookup
    row = await AgentDatastore(db).get_agent(member.source_agent_slug)
    if row is None:
        logger.warning(
            "member %s/%s points at missing library agent %s",
            member.project_id,
            member.agent_slug,
            member.source_agent_slug,
        )
        return None
    return await AgentService(db).build_agent_config(row)


async def build_member_roster(
    *,
    project_id: str,
    members: ProjectMemberDatastore,
    exclude_slug: str,
) -> str:
    """Build the lead's "team members" block (§1.5 — dispatch accuracy).

    Lists every dispatchable member with its name + role summary so the lead
    can route sub-tasks to the right agent without first calling
    ``list_members``. Excludes the lead itself.
    """
    rows = await members.list_by_project(project_id)
    lines: list[str] = []
    for row in rows:
        if row.agent_slug == exclude_slug:
            continue
        agent = await _member_agent_config(row, members)
        if agent is None:
            continue
        summary = summarize_role(agent.instructions)
        label = agent.name or row.agent_slug
        detail = f": {summary}" if summary else ""
        lines.append(f"- **{row.agent_slug}** ({label}){detail}")
    if not lines:
        return ""
    return (
        "## Team members (dispatch targets)\n\n"
        "These are the members you can dispatch sub-tasks to. Match each "
        "sub-task to the member whose role fits best.\n\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_member_agent(
    project_id: str,
    agent_slug: str,
    members: ProjectMemberDatastore,
) -> AgentConfig | None:
    """Resolve a project-local agent slug to its kernel AgentConfig.

    Returns None when:
      - No ProjectMemberRow exists for (project_id, agent_slug)
      - The kernel agent row is missing (orphaned membership)

    Callers should handle None as "agent not found" and surface a 404.
    """
    member = await members.get(project_id, agent_slug)
    if member is None:
        logger.debug("resolve_member_agent: no membership for %s/%s", project_id, agent_slug)
        return None

    agent = await _member_agent_config(member, members)
    if agent is None:
        logger.warning(
            "resolve_member_agent: member %s/%s has no resolvable library agent",
            project_id,
            agent_slug,
        )
    return agent


async def _resolve_agent_provider(
    *,
    agent: AgentConfig,
    model: str,
    providers: object | None,
    secrets: object | None,
) -> object | None:
    """Resolve a concrete ModelProvider for an agent's pinned provider_id.

    Returns None (env fallback) when no provider is pinned or the resolver
    deps weren't wired by the caller. Resolution failures are logged and
    downgraded to None so a misconfigured pin never hard-fails a dispatch.
    """
    meta = agent.metadata or {}
    provider_id = meta.get("provider_id")
    # Diagnostic: most "no model provider configured" reports trace to one
    # of these three gaps (metadata-empty / pre-fix legacy agent / caller
    # forgot to wire deps). Log once per resolve so the failure is debuggable
    # in production without attaching a debugger.
    if not provider_id:
        logger.warning(
            "agent_resolver: agent %s (%s) has no provider_id in metadata — "
            "metadata keys=%s. Re-add the agent via the project "
            "dialog so the provider pin is written.",
            agent.id,
            agent.name,
            sorted(meta.keys()) if isinstance(meta, dict) else type(meta).__name__,
        )
        return None
    if providers is None or secrets is None:
        logger.warning(
            "agent_resolver: agent %s has provider_id=%s but resolver deps "
            "are not wired (providers=%s secrets=%s). This is a "
            "caller bug — kickoff/dispatch should pass _provider_resolver_deps.",
            agent.id,
            provider_id,
            providers is not None,
            secrets is not None,
        )
        return None
    try:
        from valuz_agent.adapters.provider_resolver import resolve_model_provider

        resolved = await resolve_model_provider(
            provider_id=provider_id,
            model_id=model,
            providers=providers,  # type: ignore[arg-type]
            secrets=secrets,  # type: ignore[arg-type]
            runtime_provider=agent.runtime_provider,
        )
        if resolved is None:
            logger.warning(
                "agent_resolver: resolve_model_provider returned None for "
                "agent %s provider_id=%s model=%s — provider row may be "
                "missing, disabled, or its credential source unresolved.",
                agent.id,
                provider_id,
                model,
            )
        return resolved
    except Exception:
        logger.warning(
            "build_member_session: provider %s not resolvable for agent %s — falling back to env",
            provider_id,
            agent.id,
        )
        return None


def embed_agent_config(request: CreateSessionRequest, agent: object) -> CreateSessionRequest:
    """Return *request* with its ``agent_config`` snapshot replaced by *agent*.

    *agent* is a domain ``AgentConfig`` (e.g. the per-task lead clone from
    ``_materialize_lead_agent``); it is serialized to the wire schema here so
    callers never touch the schema layer themselves. The request is a Pydantic
    model — ``dataclasses.replace`` does not apply.
    """
    from app.serializers import agent_config_to_schema

    return request.model_copy(update={"agent_config": agent_config_to_schema(agent)})


async def build_member_session(
    *,
    project_id: str,
    agent_slug: str,
    members: ProjectMemberDatastore,
    is_lead: bool,
    task_id: str,
    run_dir: str,
    brief: str,
    project_name: str = "",
    project_instructions_md: str | None = None,
    model_override: str | None = None,
    providers: object | None = None,
    secrets: object | None = None,
    lead_session_id: str | None = None,
    dispatch_mode: str = "sync",
    goal_mode: bool = False,
    plan_pre_committed: bool = False,
) -> CreateSessionRequest | None:
    """Construct the kernel create-session request for a dispatch member or lead.

    Returns None when the member cannot be resolved (orphaned slug).
    Caller persists via ``kernel_client.create_session`` (the returned object IS the request).

    Args:
        project_id: The valuz project id (= kernel project id).
        agent_slug: The project-local agent handle.
        members: Open ProjectMemberDatastore instance.
        is_lead: True for the task lead session; False for subtask sessions.
        task_id: The valuz task id (for metadata).
        run_dir: Absolute path to this session's working directory. Under v2.1
                 both lead and members default to the shared project cwd;
                 subrun_dir is only used for opt-in repo-worktree isolation.
        brief: Text injected as the session brief — for leads this is the
               full task goal/md; for subtasks it is the scoped goal+refs.
        project_name: Optional project display name (for system prompt).
        project_instructions_md: Optional project-level instructions.
        model_override: Override the agent's default model when provided.

    Session fields set:
        cwd = run_dir (K-PR3)
        instructions = agent.instructions + project_prompt
                       + (DISPATCH_PLAYBOOK if is_lead else "") + brief
        metadata["valuz"] = {project_id, agent_slug, task_id, run_kind}
        runtime_provider, model, skills, mcp_servers, permission_mode from agent
    """
    member_row = await members.get(project_id, agent_slug)
    if member_row is None:
        logger.debug("build_member_session: no membership for %s/%s", project_id, agent_slug)
        return None

    agent = await _member_agent_config(member_row, members)
    if agent is None:
        logger.warning(
            "build_member_session: member %s/%s has no resolvable library agent",
            project_id,
            agent_slug,
        )
        return None

    # Goal-mode payload guard (see ``assert_goal_brief_length`` docstring).
    # Only enforced for runtimes whose kernel wrap_for_mode actually prepends
    # ``/goal `` — claude_agent + codex. deepagents bypasses the slash wrap
    # so a long brief isn't a CLI-level failure there.
    if goal_mode and agent.runtime_provider in ("claude_agent", "codex"):
        assert_goal_brief_length(brief)

    # Build the instructions string (§S3 point ③)
    project_prompt = build_project_system_prompt(
        project_name=project_name,
        instructions_md=project_instructions_md,
    )
    # Lead-only: dispatch playbook + a roster of dispatchable members (with
    # their role summaries) so the lead can route sub-tasks accurately.
    playbook_block = ""
    if is_lead:
        if plan_pre_committed:
            # VALUZ-CHATPLAN: the chat draft path already wrote the plan;
            # this playbook tells the lead to skip plan_task and read the
            # existing plan instead. handler also rejects plan_task on
            # non-empty plan as belt-and-suspenders.
            playbook_block = COMMITTED_LEAD_PLAYBOOK
        else:
            playbook_block = DISPATCH_PLAYBOOK_V2 if dispatch_mode == "async" else DISPATCH_PLAYBOOK
    roster_block = (
        await build_member_roster(
            project_id=project_id, members=members, exclude_slug=agent_slug
        )
        if is_lead
        else ""
    )
    # Always-on baseline skills (valuz-project-docs + skill-creator) — every
    # session carries them, matching what ``resolve_session_capabilities``
    # injects for chat/project sessions. Task sessions don't flow through that
    # resolver, so inject the same set here. Dedupe against the agent's own
    # skills by basename so an agent that explicitly lists one isn't doubled.
    baseline_skill_paths = always_on_skill_paths()
    own_skill_names = [(s.name if hasattr(s, "name") else str(s)) for s in (agent.skills or [])]
    # Resolve the agent's skill slugs → absolute source dirs (the kernel
    # materializer needs paths, not slugs); display names stay as the slugs.
    # Shared chokepoint — same resolver the chat path uses.
    own_skill_paths = await resolve_skill_slugs_to_paths(agent.skills, run_dir)
    baseline_skill_names = [os.path.basename(p) for p in baseline_skill_paths]
    extra_skill_paths = tuple(
        p for p in baseline_skill_paths if os.path.basename(p) not in set(own_skill_names)
    )
    session_skills = tuple(own_skill_paths) + extra_skill_paths

    # v2.1 skill scoping: under the shared project cwd, all claude_agent members
    # materialise skills into the same ``.claude/skills/`` (union). Instead of
    # filesystem isolation, scope by prompt — tell the agent which skills are
    # *its own* vs the shared always-on baseline, and to ignore anything else
    # (M10 附录 D.3). The baseline must NOT be in the "ignore" set.
    block_lines: list[str] = []
    if own_skill_names:
        block_lines.append("## Your skills\n\nThese are assigned to you — prefer these:")
        block_lines += [f"- {n}" for n in own_skill_names]
    if baseline_skill_names:
        block_lines.append(
            ("\n" if block_lines else "")
            + "Shared skills available to every agent (use when relevant):"
        )
        block_lines += [f"- {n}" for n in baseline_skill_names]
    if own_skill_names:
        block_lines.append(
            "\nIgnore any other skills present in the working directory not listed above."
        )
    skills_block = "\n".join(block_lines)
    parts = [
        p
        for p in [
            agent.instructions,
            project_prompt,
            roster_block,
            skills_block,
            playbook_block,
            brief,
        ]
        if p.strip()
    ]
    instructions = "\n\n".join(parts)

    run_kind = "lead" if is_lead else "subtask"

    # Per-agent provider pinning: when the agent stored a provider_id and the
    # caller wired the resolver deps, resolve a concrete model_provider
    # (base_url/api_key/protocol) so this run uses the chosen provider. When
    # unset, model_provider stays None and the runtime falls back to its env
    # (preserves the prior env-based behavior).
    model_provider = await _resolve_agent_provider(
        agent=agent,
        model=model_override or agent.model,
        providers=providers,
        secrets=secrets,
    )

    # Surface the agent's pinned provider id as the session's locked provider
    # so the conversation composer can match (provider, model) and display the
    # agent's actual configuration instead of falling back to a default.
    pinned_provider_id = (agent.metadata or {}).get("provider_id")

    # Agent-level reasoning-effort budget flows into the session here (effort
    # is configured on the agent, not per-conversation). ``None`` leaves
    # model_settings unset so the runtime uses its SDK default.
    #
    # Effort is a per-agent opt-in and travels as configured. DeepAgents maps
    # it → OpenAI ``reasoning_effort``, which most openai-compatible backends
    # accept (mimo /v1 does); only some reject it (deepseek-v4-flash 400s:
    # "thinking options type cannot be disabled when reasoning_effort is set").
    # That's a per-model constraint — clear effort on those agents — not a
    # reason to strip it for every deepagents session.
    agent_effort = getattr(agent, "effort", None)
    model_settings = ModelSettingsSchema(effort=agent_effort) if agent_effort else None

    # Session-modes (docs/exec-plans/active/task-goal-mode.md): when the
    # caller opts into goal mode (lead whole-task / member sub-run), set
    # ``Session.mode="goal"`` so the kernel wraps this session's first
    # message into ``/goal <brief>`` and the runtime auto-loops until the
    # goal is met (Claude Haiku evaluator / codex goal protocol). Gated on
    # runtime support — deepagents has no native goal mode (kernel routes
    # 400), so it falls back to a single ``default`` run_turn.
    session_mode = (
        "goal" if goal_mode and agent.runtime_provider in ("claude_agent", "codex") else "default"
    )

    # Task dispatch sessions (lead AND member) do NOT flow through
    # ``resolve_session_capabilities``, so the always-on built-in HTTP MCP
    # servers (docs / schedules / connectors) must be injected here. Generate
    # the session id up front so it can scope those servers' request headers.
    session_id = uuid4().hex
    builtin_mcp = always_on_http_mcp_servers(session_id)
    # De-dupe by name in case the agent's own mcp_servers already carry a
    # reserved ``valuz_*`` name (shouldn't, but keep injection idempotent).
    existing_names = {getattr(m, "name", None) for m in (agent.mcp_servers or ())}
    from app.serializers import mcp_to_schema as _mcp_to_schema

    mcp_servers = [_mcp_to_schema(m) for m in (agent.mcp_servers or ())] + [
        m for m in builtin_mcp if m.name not in existing_names
    ]

    from app.serializers import (
        agent_config_to_schema,
    )

    session = CreateSessionRequest(
        id=session_id,
        agent_config=agent_config_to_schema(agent),
        cwd=run_dir,
        mode=session_mode,
        runtime_provider=agent.runtime_provider,
        model=model_override or agent.model,
        model_provider=model_provider,
        model_settings=model_settings,
        instructions=instructions,
        skills=list(session_skills),
        mcp_servers=list(mcp_servers),
        permission_mode=agent.permission_mode,
        metadata={
            "valuz": {
                "project_id": project_id,
                "agent_slug": agent_slug,
                "task_id": task_id,
                "run_kind": run_kind,
                # Composer reads locked_provider_id from valuz metadata to match
                # the session's locked (provider, model) pair.
                **({"locked_provider_id": pinned_provider_id} if pinned_provider_id else {}),
                # v2 actor dispatch: members carry their lead's session id so
                # member_done notifications can be routed back (M10 附录 B).
                **({"lead_session_id": lead_session_id} if lead_session_id else {}),
            }
        },
    )
    return session
