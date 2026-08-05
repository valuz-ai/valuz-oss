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

import functools
import logging
import os
import sys
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any
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
from valuz_agent.adapters.system_prompt_builder import (
    AUTHORIZATION_BOUNDARY_INSTRUCTIONS,
    OUTPUT_FORMAT_INSTRUCTIONS,
    assemble_session_instructions,
    build_project_system_prompt,
    ensure_citation_system_policy,
)
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.memory.injection import memory_instructions_block
from valuz_agent.ports.instructions import (
    agent_inherits_global_instructions,
    resolve_global_instructions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Goal-mode brief budget
# ---------------------------------------------------------------------------
#
# A goal-mode session feeds its brief into the bundled Claude Code CLI as a
# ``/goal <text>`` payload. Two limits apply:
#
#   1. TOKEN budget (primary) — we keep a goal brief within ``GOAL_BRIEF_MAX_TOKENS``
#      (~2000 tokens). Past that the goal is too heavy to drive a focused
#      auto-loop, so the full text is spilled to a doc and only a short pointer
#      rides ``/goal``.
#   2. CHARACTER backstop (hard) — the CLI itself rejects ``/goal`` payloads over
#      4000 chars ("Goal condition is limited to 4000 characters (got NNNN)").
#      A token-light but char-heavy brief could pass the token budget yet still
#      blow this cap, so ``GOAL_BRIEF_MAX_CHARS`` (3900, ~100 chars headroom for
#      the ``/goal `` prefix + padding) is enforced regardless.
#
# ``goal_brief_exceeds_budget`` spills when EITHER limit is exceeded, so we honor
# the token budget while never crashing the CLI mid-turn. Token counting uses the
# OSS ``tiktoken`` BPE tokenizer (``o200k_base`` — already in the dependency
# tree): the exact tokenizer for the codex runtime and a close open-source proxy
# for Claude (no official Claude tokenizer ships for offline use). If tiktoken
# can't load its vocab (offline packaged build), counting degrades to a
# script-aware char heuristic so the fence never fails.

GOAL_BRIEF_MAX_TOKENS: int = 2000
GOAL_BRIEF_MAX_CHARS: int = 3900

# OSS tokenizer vocabulary. ``o200k_base`` is the GPT-4o / codex BPE vocab — the
# most modern tiktoken encoding and the best open-source proxy for Claude.
_TOKEN_ENCODING_NAME = "o200k_base"


def _vendored_tiktoken_cache_dir() -> str | None:
    """Locate the vendored tiktoken vocab dir so the (offline) packaged app can
    load the encoding without ever reaching the network.

    tiktoken reads a cached vocab from ``$TIKTOKEN_CACHE_DIR/<sha1(blob_url)>``
    before downloading. We ship that file under ``backend/vendor/tiktoken/`` and
    point the env there. Priority: ``VALUZ_TIKTOKEN_CACHE_DIR`` override > the
    frozen bundle (``_MEIPASS/vendor/tiktoken``, staged by the PyInstaller spec)
    > the dev tree (``backend/vendor/tiktoken``). Returns None if none exist,
    leaving tiktoken to its default (network) behavior.
    """
    env = os.environ.get("VALUZ_TIKTOKEN_CACHE_DIR")
    if env and os.path.isdir(env):
        return env
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "vendor", "tiktoken")  # type: ignore[attr-defined]
        if os.path.isdir(bundled):
            return bundled
    # Dev tree: this file is backend/valuz_agent/adapters/agent_resolver.py.
    dev = Path(__file__).resolve().parents[2] / "vendor" / "tiktoken"
    return str(dev) if dev.is_dir() else None


@functools.lru_cache(maxsize=1)
def _token_encoding() -> Any | None:
    """Lazily load the OSS ``tiktoken`` encoding; return None if unavailable.

    Points ``TIKTOKEN_CACHE_DIR`` at the vendored vocab first (so a packaged,
    offline app loads it without network), unless the operator already set it.
    Cached so the one-time load is paid once per process. Returns None — never
    raises — when tiktoken or its vocab can't be loaded (e.g. an unvendored
    offline build), letting ``estimate_tokens`` fall back to the heuristic.
    Prefer ``prewarm_token_estimator`` at boot so this load is off the event loop.
    """
    try:
        if not os.environ.get("TIKTOKEN_CACHE_DIR"):
            cache_dir = _vendored_tiktoken_cache_dir()
            if cache_dir:
                os.environ["TIKTOKEN_CACHE_DIR"] = cache_dir

        import tiktoken

        return tiktoken.get_encoding(_TOKEN_ENCODING_NAME)
    except Exception:  # noqa: BLE001 — any failure → heuristic fallback
        logger.debug(
            "tiktoken %s unavailable — goal token budget falls back to the char heuristic",
            _TOKEN_ENCODING_NAME,
            exc_info=True,
        )
        return None


def prewarm_token_estimator() -> None:
    """Warm the tiktoken encoding in a background daemon thread (best-effort).

    The first ``tiktoken.get_encoding`` may fetch + parse the vocab (seconds);
    doing it lazily on the first task would stall the event loop. Boot calls this
    so the cache (or the None fallback) is ready before any task runs. Safe to
    call repeatedly — the lru_cache makes the actual load run at most once.
    """
    threading.Thread(target=_token_encoding, name="tiktoken-prewarm", daemon=True).start()


def _heuristic_tokens(text: str) -> int:
    """Dependency-free token estimate used when tiktoken is unavailable.

    Approximate by script: CJK ideographs / kana / hangul cost ~1 token each
    (they rarely merge), and the remaining (mostly Latin/punctuation) text costs
    ~1 token per 4 chars — the widely-used rough ratio. Conservative on purpose.
    """

    def _is_cjk(ch: str) -> bool:
        return (
            "一" <= ch <= "鿿"  # CJK Unified Ideographs
            or "㐀" <= ch <= "䶿"  # CJK Extension A
            or "぀" <= ch <= "ヿ"  # Hiragana + Katakana
            or "가" <= ch <= "힣"  # Hangul syllables
            or "豈" <= ch <= "﫿"  # CJK Compatibility Ideographs
        )

    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_tokens(text: str) -> int:
    """Token count for the goal-mode budget.

    Uses the OSS ``tiktoken`` ``o200k_base`` BPE tokenizer when available
    (precise), falling back to a script-aware char heuristic offline. Arbitrary
    user text is encoded with ``disallowed_special=()`` so literal ``<|...|>``
    sequences are counted as plain text rather than raising.
    """
    if not text:
        return 0
    enc = _token_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001 — never let counting fail the fence
            logger.debug("tiktoken encode failed — using heuristic", exc_info=True)
    return _heuristic_tokens(text)


def goal_brief_exceeds_budget(brief: str) -> bool:
    """True when ``brief`` is too long to ride a ``/goal`` payload — over the
    token budget OR the hard character backstop (see module comment)."""
    return estimate_tokens(brief) > GOAL_BRIEF_MAX_TOKENS or len(brief) > GOAL_BRIEF_MAX_CHARS


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

    Pure — no side effects. Retained as a public predicate; the task pipeline
    no longer raises on a long goal — it *spills* it to a doc instead (see
    ``spill_goal_brief_if_too_long``). Kept for callers/tests that want the
    bare length assertion.
    """
    n = len(brief)
    if n > limit:
        raise BriefTooLongError(length=n, limit=limit)


# ---------------------------------------------------------------------------
# Goal-mode brief spill — the fence that replaces the hard length error.
# ---------------------------------------------------------------------------
#
# Instead of failing an over-long task goal / subtask brief mid-turn, we LAND the
# full brief in a doc under the session's working dir and hand ``/goal`` only a
# short pointer that tells the agent to read that doc first. Goal mode then
# auto-loops against a brief that always fits the budget, while the full goal +
# refs + acceptance criteria live in a file every agent (lead or member) can
# read. The spill triggers on ``goal_brief_exceeds_budget`` (token budget OR the
# hard char backstop).
#
# This is a Lead-facing concern (the lead drives the whole task goal AND writes
# each subtask's goal), so the lead playbooks flag both directions: a goal may
# arrive as a doc pointer to read, and an over-long subtask goal should be
# written to a file with only its path dispatched.


def _goal_brief_pointer(doc_path: str, *, is_lead: bool) -> str:
    """Build the short ``/goal`` pointer that stands in for a spilled brief."""
    noun = "任务" if is_lead else "子任务"
    return (
        f"本{noun}的完整目标内容较长(超出 {GOAL_BRIEF_MAX_TOKENS} token 预算),已落地为"
        f"文档,无法直接作为 goal 条件传入。\n\n"
        f"请先用文件读取工具完整阅读下面这份文档——它包含本{noun}的完整目标、参考资料"
        f"与验收标准——然后据此开展工作,直到达成其中描述的目标:\n\n"
        f"{doc_path}"
    )


def spill_goal_brief_if_too_long(
    brief: str,
    *,
    run_dir: str | Path,
    task_id: str,
    label: str,
    is_lead: bool,
) -> str:
    """Return ``brief`` unchanged when it fits the goal-mode budget; otherwise
    spill the full text to a doc under ``run_dir`` and return a short pointer.

    "Fits the budget" = within ``GOAL_BRIEF_MAX_TOKENS`` AND the hard char
    backstop (see ``goal_brief_exceeds_budget``). Call this at every site that
    feeds a brief into a goal-mode session (i.e. the ``/goal`` payload): task
    kickoff/commit (lead), dispatch (member), and recovery re-injection.
    Idempotent against an already-spilled (short) brief — one within budget is
    returned verbatim and no file is written, so it is safe to call both at the
    caller (to fence the initial prompt) and as defense-in-depth inside
    ``build_member_session`` (to fence the embedded instructions) without
    double-writing.

    Only the file write touches disk; the path comes from ``FsRegistry`` (which
    never writes content), keeping the single-write-registry rule intact.
    """
    if not goal_brief_exceeds_budget(brief):
        return brief
    from valuz_agent.infra.fs_registry import fs_registry

    doc_path = fs_registry.task_brief_path(run_dir, task_id, label)
    doc_path.write_text(brief, encoding="utf-8")
    logger.info(
        "goal brief spilled to doc (~%d tokens / %d chars > budget): task=%s label=%s -> %s",
        estimate_tokens(brief),
        len(brief),
        task_id,
        label,
        doc_path,
    )
    return _goal_brief_pointer(str(doc_path), is_lead=is_lead)


# ---------------------------------------------------------------------------
# Dispatch Playbook (§1.5) — injected into lead sessions only.
# Explains the collaboration protocol to the lead LLM.
# ---------------------------------------------------------------------------

DISPATCH_PLAYBOOK = """\
## Dispatch Playbook (lead session only)

You are the lead for this Task. Drive the WHOLE task in this one turn —
dispatch, collect, review, repeat — until you call finish_task.

NOTE — the goal-mode length budget (~2000 tokens per goal). Two directions:
  • RECEIVING: if THIS task's goal was too long to pass inline you'll get a
    short pointer to a doc file instead of the full goal — read that doc FIRST
    (file-read tool) for the complete goal / references / criteria before you
    plan.
  • DISPATCHING: keep each subtask's `goal` concise. When a subtask needs a lot
    of context or instructions (over ~2000 tokens), FIRST write that content to
    a file in the project (e.g. tasks/_briefs/<name>.md) and put the FILE PATH
    in the subtask `goal` (or in refs) — do NOT inline a huge goal. (Over-long
    goals are auto-spilled to a doc as a safety net, but writing the file
    yourself keeps the plan readable and the member focused.)

Protocol:

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
   and unlocks dependents. "rework" sends it back: READ THE REPLY —
   `delivered_to_live_member: true` means the member is already redoing it
   (just await_members again); `false` means the node is parked in `rework`,
   so dispatch(subtask_key=...) to re-run it. Read result files to judge.
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
   in_review/rework/paused; it returns the pending keys — dispatch and review them,
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
turn until finish_task.

NOTE — the goal-mode length budget (~2000 tokens per goal). Two directions:
  • RECEIVING: if THIS task's goal was too long to pass inline you'll get a
    short pointer to a doc file instead of the full goal — read that doc FIRST
    (file-read tool) for the complete goal / references / criteria.
  • DISPATCHING / modify_plan: keep each subtask's `goal` concise. When a
    subtask needs a lot of context (over ~2000 tokens), FIRST write it to a
    file in the project and put the FILE PATH in the subtask `goal` (or refs)
    — do NOT inline a huge goal. (Over-long goals are auto-spilled as a safety
    net, but writing the file yourself keeps the plan readable.)

Protocol:

1. READ THE PLAN. Start by calling get_plan() to see the committed subtask
   DAG: each node carries a stable `key`, target `agent`, dependencies, and
   the `review_criteria` the user signed off on. The response includes
   `current_version` — remember this for any modify_plan call you make.
2. DISPATCH INDEPENDENT SUBTASKS IN PARALLEL. For every key whose deps are
   already satisfied (the `ready` list from get_plan), call
   dispatch(subtask_key=...)
   back-to-back — dispatch is NON-BLOCKING, members run concurrently. Never
   serialize independent work.
3. COLLECT with await_members(mode="any") in a loop, reviewing each result as
   it arrives. Use await_members(mode="all") only when you genuinely need
   the whole batch at once before continuing.
4. REVIEW each finished subtask with
   review_subtask(subtask_key=..., decision=..., feedback=...) — judge against
   the node's `review_criteria`. "approve" marks done and unlocks dependents.
   "rework" sends it back: READ THE REPLY — `delivered_to_live_member: true`
   means the member is already redoing it (just await_members again); `false`
   means the node is parked in `rework`, so dispatch(subtask_key=...) to
   re-run it. Read result files directly to judge.
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


async def _member_agent_config(member, members: ProjectMemberDatastore, user_id: str):  # noqa: ANN001, ANN202
    """Build the member's AgentConfig from its source library row.

    The kernel has no agents table — the library AgentRow is the single
    source of truth and the config is built in memory (and embedded into
    sessions as their snapshot). Members created before provenance landed
    (``source_agent_slug`` NULL, despite the legacy backfill) resolve to None.
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
    row = await AgentDatastore(db).get_agent(user_id, member.source_agent_slug)
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
    user_id: str,
) -> str:
    """Build the lead's "team members" block (§1.5 — dispatch accuracy).

    Lists every dispatchable member with its name + role summary so the lead
    can route sub-tasks to the right agent without first calling
    ``list_members``. Excludes the lead itself.
    """
    rows = await members.list_by_project(user_id, project_id)
    lines: list[str] = []
    for row in rows:
        if row.agent_slug == exclude_slug:
            continue
        agent = await _member_agent_config(row, members, user_id=user_id)
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
    user_id: str,
) -> AgentConfig | None:
    """Resolve a project-local agent slug to its kernel AgentConfig.

    Returns None when:
      - No ProjectMemberRow exists for (project_id, agent_slug)
      - The kernel agent row is missing (orphaned membership)

    Callers should handle None as "agent not found" and surface a 404.
    """
    member = await members.get(user_id, project_id, agent_slug)
    if member is None:
        logger.debug("resolve_member_agent: no membership for %s/%s", project_id, agent_slug)
        return None

    agent = await _member_agent_config(member, members, user_id=user_id)
    if agent is None:
        logger.warning(
            "resolve_member_agent: member %s/%s has no resolvable library agent",
            project_id,
            agent_slug,
        )
    return agent


async def resolve_agent_display_names(
    project_id: str,
    agent_slugs: Iterable[str],
    user_id: str,
) -> dict[str, str]:
    """Batch resolve project-local agent slugs → human display names.

    Captured at **emit time** into task-event / plan-snapshot payloads
    (``agent_name``) so names are durable: they survive a member being
    un-deployed / renamed, and free the frontend from joining a slug against a
    separately-fetched members list (which races an async load and misses
    removed agents — the "成员智能体名称查询不到" bug).

    Resolves every unique non-empty slug in a **single** read-only unit of work
    (its own, so a failure can't poison a caller's in-flight write transaction).
    Each slug maps to its library-agent name, or to the slug itself when the
    membership / source agent can't be resolved. Empty slugs are skipped.
    """
    slugs = {s for s in agent_slugs if s}
    if not slugs:
        return {}
    from valuz_agent.infra.db import async_unit_of_work

    out: dict[str, str] = {}
    try:
        async with async_unit_of_work(commit=False) as db:
            members = ProjectMemberDatastore(db)
            for slug in slugs:
                agent = await resolve_member_agent(project_id, slug, members, user_id)
                out[slug] = agent.name if agent and agent.name else slug
    except Exception:  # noqa: BLE001 — name resolution must never fail a dispatch/review
        logger.warning(
            "resolve_agent_display_names: failed to resolve names for %s — falling back to slugs",
            project_id,
            exc_info=True,
        )
    # Backfill any slug the loop didn't reach (e.g. it raised partway) with itself.
    for slug in slugs:
        out.setdefault(slug, slug)
    return out


async def resolve_agent_display_name(
    project_id: str,
    agent_slug: str,
    user_id: str,
) -> str:
    """Resolve a single agent slug to its display name (see
    ``resolve_agent_display_names``). Returns an empty slug as-is."""
    if not agent_slug:
        return agent_slug
    names = await resolve_agent_display_names(project_id, [agent_slug], user_id)
    return names.get(agent_slug, agent_slug)


async def _model_hosted_provider_id(
    *,
    model: str,
    providers: object,
    user_id: str,
) -> str | None:
    """Chat-parity fallback: the first enabled, credential-bearing provider
    hosting *model*, or ``None`` when no configured provider hosts it.

    Mirrors the session-service provider resolution (``sessions/service.py``):
    agents routinely carry no pin — provider ids are install-local, so
    pack-imported / source-instantiated agents arrive unpinned — and the chat
    path quietly binds them to any enabled provider hosting their model. Task
    dispatch used to skip this step, so the same agent worked in chat but
    failed the dispatch pre-flight ("no model provider configured").
    """
    if not model:
        return None
    try:
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.modules.providers.service import ProviderService

        svc = ProviderService(datastore=providers, event_bus=event_bus)  # type: ignore[arg-type]
        match = await svc.resolve_provider_for_model(user_id, model)
        return match.id if match is not None else None
    except Exception:
        logger.warning(
            "agent_resolver: model-hosted provider lookup failed for model %s",
            model,
            exc_info=True,
        )
        return None


async def _resolve_agent_provider(
    *,
    agent: AgentConfig,
    model: str,
    providers: object | None,
    user_id: str,
) -> object | None:
    """Resolve a concrete ModelProvider for an agent, chat-parity fallbacks included.

    Resolution order matches the session-service path: the agent's pinned
    ``metadata.provider_id`` when present, else any enabled provider hosting
    the model (``_model_hosted_provider_id``); a pin that fails to resolve
    also falls back to a model-hosted provider once. Returns None (env
    fallback) when the resolver deps weren't wired by the caller, when the
    effective provider is an OAuth subscription (healthy — the CLI supplies
    the credential out-of-band), or when nothing resolved.
    """
    meta = agent.metadata or {}
    provider_id = meta.get("provider_id")
    if providers is None:
        # Nothing to resolve against. With a pin present this is a caller
        # bug — kickoff/dispatch should pass _provider_resolver_deps.
        if provider_id:
            logger.warning(
                "agent_resolver: agent %s has provider_id=%s but resolver deps "
                "are not wired. This is a caller bug — kickoff/dispatch should "
                "pass _provider_resolver_deps.",
                agent.id,
                provider_id,
            )
        return None

    from valuz_agent.adapters.provider_resolver import resolve_model_provider

    pinned = bool(provider_id)
    if not provider_id:
        provider_id = await _model_hosted_provider_id(
            model=model, providers=providers, user_id=user_id
        )
        if not provider_id:
            logger.warning(
                "agent_resolver: agent %s (%s) has no provider_id in metadata "
                "and no enabled provider hosts model %s — metadata keys=%s. "
                "Pin a model channel on the agent or enable a provider for "
                "the model.",
                agent.id,
                agent.name,
                model,
                sorted(meta.keys()) if isinstance(meta, dict) else type(meta).__name__,
            )
            return None
        logger.info(
            "agent_resolver: agent %s (%s) has no provider pin — using "
            "provider %s (hosts model %s), same fallback as the chat path.",
            agent.id,
            agent.name,
            provider_id,
            model,
        )
    try:
        resolved = await resolve_model_provider(
            provider_id=provider_id,
            model_id=model,
            providers=providers,  # type: ignore[arg-type]
            runtime_provider=agent.runtime_provider,
            user_id=user_id,
        )
        if resolved is None:
            # The ONLY non-raising None path in ``resolve_model_provider`` is an
            # OAuth subscription provider (``auth_type="oauth"`` — codex/claude
            # ``/login``): there is no API key to forward because credentials
            # live in the CLI's keychain, so the kernel skips env overrides and
            # the spawned process uses the ambient login token. This is the
            # healthy, expected path — every genuine failure (row missing /
            # disabled / no credentials) raises ``ProviderNotResolvable`` and is
            # handled in the ``except`` below. Debug breadcrumb only, not a warning.
            logger.debug(
                "agent_resolver: provider %s for agent %s is an OAuth subscription "
                "(no env override; CLI supplies the credential out-of-band).",
                provider_id,
                agent.id,
            )
        return resolved
    except Exception:
        logger.warning(
            "build_member_session: provider %s not resolvable for agent %s — "
            "trying a model-hosted fallback",
            provider_id,
            agent.id,
        )
        # A broken pin (row deleted / disabled / credential gone) gets one
        # shot at the same fallback an unpinned agent uses. Skip when the
        # failing id already CAME from the fallback lookup.
        if pinned:
            fallback_id = await _model_hosted_provider_id(
                model=model, providers=providers, user_id=user_id
            )
            if fallback_id and fallback_id != provider_id:
                try:
                    resolved = await resolve_model_provider(
                        provider_id=fallback_id,
                        model_id=model,
                        providers=providers,  # type: ignore[arg-type]
                        runtime_provider=agent.runtime_provider,
                        user_id=user_id,
                    )
                    logger.info(
                        "agent_resolver: pinned provider %s failed; resolved "
                        "fallback provider %s for agent %s.",
                        provider_id,
                        fallback_id,
                        agent.id,
                    )
                    return resolved
                except Exception:
                    logger.warning(
                        "agent_resolver: fallback provider %s not resolvable "
                        "for agent %s either — falling back to env.",
                        fallback_id,
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
    lead_session_id: str | None = None,
    goal_mode: bool = False,
    plan_pre_committed: bool = False,
    worktree_notice: str | None = None,
    user_id: str,
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
                 both lead and members run in the shared project cwd (a
                 task-level worktree relocates that cwd wholesale).
        brief: Text injected as the session brief — for leads this is the
               full task goal/md; for subtasks it is the scoped goal+refs.
        project_name: Optional project display name (for system prompt).
        project_instructions_md: Optional project-level instructions.
        model_override: Override the agent's default model when provided.

    Session fields set:
        cwd = run_dir (K-PR3)
        instructions = [deployment global preamble +] agent.instructions
                       + project_prompt
                       + (DISPATCH_PLAYBOOK if is_lead else "") + brief
        metadata["valuz"] = {project_id, agent_slug, task_id, run_kind}
        runtime_provider, model, skills, mcp_servers, permission_mode from agent
    """
    member_row = await members.get(user_id, project_id, agent_slug)
    if member_row is None:
        logger.debug("build_member_session: no membership for %s/%s", project_id, agent_slug)
        return None

    agent = await _member_agent_config(member_row, members, user_id=user_id)
    if agent is None:
        logger.warning(
            "build_member_session: member %s/%s has no resolvable library agent",
            project_id,
            agent_slug,
        )
        return None

    agent_meta = agent.metadata or {}
    all_available_manifest = None
    if agent_meta.get("resource_policy") == "all_available":
        from valuz_agent.modules.agents.effective_resources import (
            EffectiveResourceResolver,
            current_execution_supports_stdio,
        )
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore
        from valuz_agent.modules.docs.datastore import DocumentDatastore
        from valuz_agent.modules.skills.datastore import SkillDatastore

        db = members._db  # noqa: SLF001 — same owner-scoped unit of work
        all_available_manifest = await EffectiveResourceResolver(
            skills=SkillDatastore(db),
            connectors=ConnectorDatastore(db),
            docs=DocumentDatastore(db),
        ).resolve(
            user_id,
            runtime=str(agent.runtime_provider),
            supports_stdio=current_execution_supports_stdio(),
        )

    # Goal-mode payload fence (see ``spill_goal_brief_if_too_long``). Only the
    # runtimes whose kernel wrap_for_mode prepends ``/goal `` (claude_agent +
    # codex) are capped; deepagents bypasses the slash wrap so a long brief is
    # not a CLI-level failure there. Defense-in-depth: callers spill the brief
    # they also use as the initial ``/goal`` prompt, so by here ``brief`` is
    # already short and this is a no-op — but if a path forgets, this fences the
    # brief embedded into the session instructions.
    if goal_mode and agent.runtime_provider in ("claude_agent", "codex"):
        brief = spill_goal_brief_if_too_long(
            brief,
            run_dir=run_dir,
            task_id=task_id,
            label=agent_slug,
            is_lead=is_lead,
        )

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
            playbook_block = DISPATCH_PLAYBOOK
    roster_block = (
        await build_member_roster(
            project_id=project_id,
            members=members,
            exclude_slug=agent_slug,
            user_id=user_id,
        )
        if is_lead
        else ""
    )
    # Always-on baseline skills (valuz-project-docs + skill-creator) — every
    # session carries them, matching what ``resolve_session_capabilities``
    # injects for chat/project sessions. Task sessions don't flow through that
    # resolver, so inject the same set here. Dedupe against the agent's own
    # skills by basename so an agent that explicitly lists one isn't doubled.
    baseline_skill_paths = always_on_skill_paths(user_id=user_id)
    own_skill_names = (
        [item.slug for item in all_available_manifest.skills]
        if all_available_manifest is not None
        else [(s.name if hasattr(s, "name") else str(s)) for s in (agent.skills or [])]
    )
    # Resolve the agent's skill slugs → absolute source dirs (the kernel
    # materializer needs paths, not slugs); display names stay as the slugs.
    # Shared chokepoint — same resolver the chat path uses.
    own_skill_paths = (
        all_available_manifest.skill_paths
        if all_available_manifest is not None
        else await resolve_skill_slugs_to_paths(agent.skills, run_dir, user_id=user_id)
    )
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
    # Frozen memory snapshot (memory-system-design §8): lead and members share
    # the same project memory (design §2), each frozen into its own session's
    # instructions at create time — one copy per session, never per turn.
    mem_block = await memory_instructions_block(user_id=user_id, project_id=project_id)

    # Wrap each block in an XML tag (shared chokepoint with the chat/project
    # path) so the agent / task guidance / project instructions / roster /
    # skills / brief are delineated instead of one undelimited blob.
    inherits_global = agent_inherits_global_instructions(
        kind=agent_meta.get("agent_kind", "standard"),
        inherit_global_instructions=agent_meta.get("inherit_global_instructions", True),
    )
    prompt_snapshot = await resolve_global_instructions(user_id) if inherits_global else None
    instructions = assemble_session_instructions(
        [
            (
                "global-instructions",
                prompt_snapshot.content if prompt_snapshot is not None else "",
            ),
            ("authorization-boundary", AUTHORIZATION_BOUNDARY_INSTRUCTIONS),
            ("agent-instructions", agent.instructions or ""),
            ("project-instructions", project_prompt),
            ("memory", mem_block),
            ("member-roster", roster_block),
            ("available-skills", skills_block),
            ("task-playbook", playbook_block),
            # Task-level worktree (design §5): every session of the task
            # shares one worktree cwd; the notice keeps the agent from
            # wandering back into the main workspace or force-pushing.
            ("worktree-context", worktree_notice or ""),
            ("task-brief", brief),
            ("output-format", OUTPUT_FORMAT_INSTRUCTIONS),
        ]
    )
    instructions = ensure_citation_system_policy(instructions)

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
        user_id=user_id,
    )

    # Surface the agent's pinned provider id as the session's locked provider
    # so the conversation composer can match (provider, model) and display the
    # agent's actual configuration instead of falling back to a default.
    pinned_provider_id = agent_meta.get("provider_id")

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
    builtin_mcp = await always_on_http_mcp_servers(
        session_id, owner_user_id=user_id, toolkit="lead" if is_lead else "base"
    )
    # De-dupe by name in case the agent's own mcp_servers already carry a
    # reserved ``valuz_*`` name (shouldn't, but keep injection idempotent).
    external_mcp = []
    if all_available_manifest is not None:
        from valuz_agent.adapters.mcp_resolver import resolve_mcp_servers
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore

        external_mcp = await resolve_mcp_servers(
            enabled_slugs=all_available_manifest.connector_slugs,
            connectors=ConnectorDatastore(members._db),  # noqa: SLF001
            user_id=user_id,
        )
    existing_names = {
        getattr(m, "name", None)
        for m in (external_mcp if all_available_manifest is not None else (agent.mcp_servers or ()))
    }
    from app.serializers import mcp_to_schema as _mcp_to_schema

    mcp_servers = (
        list(external_mcp)
        if all_available_manifest is not None
        else [_mcp_to_schema(m) for m in (agent.mcp_servers or ())]
    ) + [m for m in builtin_mcp if m.name not in existing_names]

    from app.serializers import (
        agent_config_to_schema,
    )

    valuz_metadata: dict[str, object] = {
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
    if prompt_snapshot is not None:
        valuz_metadata["global_instructions"] = prompt_snapshot.metadata()
    if all_available_manifest is not None:
        valuz_metadata["capability_manifest"] = all_available_manifest.session_metadata()

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
        metadata={"valuz": valuz_metadata},
    )
    return session
