"""Per-turn capability convergence — the hooks handed to ``run_turn``.

Sessions carry a persisted capability snapshot (``skills`` / ``mcp_servers`` /
``instructions`` / citation policy) that has to converge on the CURRENT world
before every turn: MCP headers whose credentials expire, KB bindings added
after the session was created, a citation preference the user just toggled.
``modules/sessions/capabilities`` owns each individual refresher; this module
owns **when** they run and packages them as the ``pre_turn`` hook
``kernel_client.run_turn`` invokes.

The "when" is the whole point. Every refresher writes through
``kernel_client.update_session``, which routes live-kernel-first and never
provisions. Called before the turn's kernel is allocated, an at-rest session
resolves to the durable — and a scoped (sandbox) deployment then boots the
turn's instance seeded from a COS snapshot and reads only its own runtime
sqlite, so the durable write is never read. Handing these to ``run_turn``
instead puts them in the one window where the write is guaranteed to reach the
kernel that is about to consume it. See ``kernel_client.run_turn`` for the
failure this fixes.

Credential/skill refreshers are best-effort. Check-policy convergence is
required: dispatching with the previous input's disabled checks is unsafe.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from valuz_agent.ports.capability_policy import OptionalCheckOverrides, TaskCheckConfig
from valuz_agent.ports.message_context import HostRef

logger = logging.getLogger(__name__)

# What ``kernel_client.run_turn`` accepts: a nullary awaitable closed over the
# session it converges. Callers bind their own parameters at build time.
PreTurnHook = Callable[[], Awaitable[None]]


async def restamp_always_on_mcp(session_id: str, user_id: str | None) -> None:
    """Refresh the always-on in-process MCP servers + external connector
    credentials on the session row.

    Two distinct staleness sources converge here:

    - **The always-on tools** (docs / automations / playbooks / connectors). Their headers
      carry ``X-Valuz-Internal`` plus ``backend_base_url`` and the session id,
      baked at create time. A session re-driven after a backend restart — task
      resume / recovery, the persistent actor loop, a sync kickoff — can carry
      stale ones; the in-process MCP gate then 403s every request and the
      runtime parks the ``harness`` server in ``needsAuth``, hiding ALL its
      tools (memory / submit_skill, and for a lead the whole orchestration set:
      dispatch / review_subtask / finish_task / await_members / send /
      get_plan). The symptom is a re-launched lead reporting it "has no
      orchestration tools". ``internal_mcp_token`` is now DERIVED from the
      stable owner id so it no longer rotates across restarts (the historical
      root cause); the re-stamp stays to converge the other drift-prone bits.

    - **User-attached external connectors**, whose OAuth bearer expires in ~1h.
      This is the load-bearing case for an ordinary long-lived conversation:
      without a re-stamp that actually reaches the runtime, every external tool
      call 401s for the rest of the session's life, with no self-healing path.

    Best-effort and idempotent — a no-op when nothing drifted, which keeps the
    prompt cache warm.
    """
    if user_id is None:
        return
    try:
        from valuz_agent.modules.sessions.capabilities import (
            refresh_always_on_mcp_for_session,
        )

        await refresh_always_on_mcp_for_session(session_id, user_id)
    except Exception:  # noqa: BLE001 — never block a turn on a re-stamp failure
        logger.warning("always-on MCP re-stamp failed for session %s", session_id, exc_info=True)


async def _refresh_docs_capabilities(session_id: str, user_id: str | None) -> None:
    """Re-install the docs skill + ``valuz_docs`` MCP if a KB binding appeared
    after the session was created.

    ``capability_resolver`` only fires at create time. The
    ``project.bindings.changed`` eventbus subscriber handles the binding moment
    for sessions that exist then; this is the belt-and-braces guarantee that by
    the time a turn actually runs, the docs capability is present.
    """
    if user_id is None:
        return
    try:
        from valuz_agent.modules.sessions.capabilities import (
            refresh_docs_capabilities_for_session,
        )

        await refresh_docs_capabilities_for_session(session_id, user_id)
    except Exception:  # noqa: BLE001 — never block a turn on a refresh failure
        logger.warning("docs capability refresh failed for session %s", session_id, exc_info=True)


async def _refresh_bundled_skills(session_id: str, user_id: str | None) -> None:
    """Attach bundled official packages that landed after the session started.

    Same staleness class as the docs skill above, one step earlier in the
    chain: ``capability_resolver`` injects every bundled package into every
    session, but it only runs at create time, and the packages are written by
    an asynchronous, out-of-band materialiser (a release that adds one, or a
    managed deployment landing an owner's tree for the first time). Without
    this, a session created in that window is the only one on the whole
    installation that cannot see the package.
    """
    if user_id is None:
        return
    try:
        from valuz_agent.modules.sessions.capabilities import (
            refresh_bundled_skills_for_session,
        )

        await refresh_bundled_skills_for_session(session_id, user_id)
    except Exception:  # noqa: BLE001 — never block a turn on a refresh failure
        logger.warning("bundled skill refresh failed for session %s", session_id, exc_info=True)


async def _refresh_citation_policy(
    session_id: str,
    user_id: str | None,
    *,
    citation_enabled_override: bool | None,
    verification_enabled_override: bool | None,
    task_coverage_enabled_override: bool | None = None,
    host_ref: HostRef | None = None,
    task_check_config: TaskCheckConfig | None = None,
    resume_task_checks: bool = False,
) -> None:
    """Converge the citation / verification / task-coverage policy on the
    session (skill, system policy block, resolved quality policy).

    The overrides let an internal document-summary run keep inspectable
    citation indices without paying for claim-quality verification.
    """
    if user_id is None:
        return
    try:
        from valuz_agent.modules.sessions.capabilities import (
            refresh_citation_policy_for_session,
        )

        await refresh_citation_policy_for_session(
            session_id,
            user_id,
            citation_enabled_override=citation_enabled_override,
            verification_enabled_override=verification_enabled_override,
            task_coverage_enabled_override=task_coverage_enabled_override,
            host_ref=host_ref,
            task_check_config=task_check_config,
            resume_task_checks=resume_task_checks,
        )
    except Exception as exc:
        # Continuing would run a new research request with a prior turn's
        # persisted disabled flags. The turn driver reports/finalizes this
        # preparation failure; never invoke the model with a stale exemption.
        logger.error("check policy refresh failed for session %s", session_id, exc_info=True)
        from valuz_agent.adapters import kernel_client

        raise kernel_client.RequiredPreTurnError(
            "Unable to resolve the current task check policy"
        ) from exc


def always_on_mcp_hook(session_id: str, user_id: str | None) -> PreTurnHook:
    """Task/recovery path: retain this run's check snapshot, refresh credentials."""

    async def _hook() -> None:
        await _refresh_citation_policy(
            session_id,
            user_id,
            citation_enabled_override=None,
            verification_enabled_override=None,
            resume_task_checks=True,
        )
        await restamp_always_on_mcp(session_id, user_id)

    return _hook


def chat_capability_hook(
    session_id: str,
    user_id: str | None,
    *,
    citation_enabled_override: bool | None = None,
    verification_enabled_override: bool | None = None,
    task_coverage_enabled_override: bool | None = None,
    host_ref: HostRef | None = None,
    task_check_config: TaskCheckConfig | None = None,
) -> PreTurnHook:
    """Full convergence — the chat turn path (send, queue drain, sync send).

    A chat session is long-lived and user-editable between turns: the user can
    bind a KB, toggle citations, or re-auth a connector while the session sits
    idle — and on a managed deployment the bundled packages themselves can land
    mid-session. Order matches the historical call order in ``SessionService``:
    citation policy → docs capabilities → always-on MCP re-stamp (last, so it
    re-stamps the docs MCP entry the step before it may have just added). The
    bundled sweep runs before the docs step, which is the one bundled package
    with an MCP server to pair with.
    """

    from valuz_agent.modules.sessions.task_checks import fresh_config

    config = fresh_config(task_check_config)
    overrides = config.overrides.model_dump()
    overrides.update(
        {
            key: value
            for key, value in {
                "citation_enabled": citation_enabled_override,
                "verification_enabled": verification_enabled_override,
                "task_coverage_enabled": task_coverage_enabled_override,
            }.items()
            if value is not None
        }
    )
    config = config.model_copy(update={"overrides": OptionalCheckOverrides(**overrides)})

    async def _hook() -> None:
        await _refresh_citation_policy(
            session_id,
            user_id,
            citation_enabled_override=citation_enabled_override,
            verification_enabled_override=verification_enabled_override,
            task_coverage_enabled_override=task_coverage_enabled_override,
            host_ref=host_ref,
            task_check_config=config,
        )
        await _refresh_bundled_skills(session_id, user_id)
        await _refresh_docs_capabilities(session_id, user_id)
        await restamp_always_on_mcp(session_id, user_id)
        # Last: PTC reads the post-restamp MCP set (final server names) to
        # decide the code face for this turn.
        await _refresh_ptc(session_id, user_id)

    return _hook


async def _refresh_ptc(session_id: str, user_id: str | None) -> None:
    """Converge the PTC code face (generated skill + opt-in metadata +
    prompt policy block) with the user's preference and the session's
    data connectors. Additive/reversible; a no-op keeps rows unchanged."""
    if user_id is None:
        return
    try:
        from valuz_agent.modules.ptc.session_refresh import refresh_ptc_for_session

        await refresh_ptc_for_session(session_id, user_id)
    except Exception:  # noqa: BLE001 — never block a turn on a refresh failure
        logger.warning("ptc refresh failed for session %s", session_id, exc_info=True)
