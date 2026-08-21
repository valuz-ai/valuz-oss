"""TaskSessionResolver — host knowledge → ready-to-create kernel sessions.

The ONE place task code turns project rows, membership, the agent library,
providers and credentials into lead/member sessions (enforced by
check_module_boundaries.py). Shaped after MemberResolverPort
(task-kernel-migration §5.1) in case that migration is revived.

Resolve methods return the value or a ``Failure`` — callers decide whether the
reason becomes a raise, an error dict, or a task event.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from valuz_agent.adapters.agent_resolver import (
    _member_agent_config,
    _resolve_agent_provider,
    build_member_session,
    embed_agent_config,
    resolve_agent_display_name,
    spill_goal_brief_if_too_long,
    summarize_role,
)
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.projects.datastore import ProjectDatastore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider deps + credential pre-flight (absorbed from _session_build.py)
# ---------------------------------------------------------------------------


def _provider_resolver_deps(db: Any) -> dict[str, Any]:
    """Build the provider deps so build_member_session
    can resolve a per-agent pinned provider into the run's model_provider."""
    from valuz_agent.modules.providers.datastore import ProviderDatastore

    return {
        "providers": ProviderDatastore(db),
    }


async def _credential_gap(
    session: Any,
    agent_slug: str,
    *,
    db: Any | None = None,
    user_id: str,
) -> str | None:
    """A clear reason when a built session has no usable credentials, else None.

    Detected up front because a ``model_provider=None`` run only fails
    mid-turn with a cryptic SDK "Not logged in". OAuth subscriptions are
    healthy with provider=None — see ``_provider_gap``.
    """
    if getattr(session, "model_provider", None) is not None:
        return None
    agent = getattr(session, "agent_config", None)
    pinned_provider_id = (
        (getattr(agent, "metadata", None) or {}).get("provider_id") if agent is not None else None
    )
    return await _provider_gap(
        db,
        agent_slug=agent_slug,
        pinned_provider_id=pinned_provider_id,
        runtime=getattr(session, "runtime_provider", "") or "",
        model=getattr(session, "model", "") or "",
        user_id=user_id,
    )


async def _provider_gap(
    db: Any | None,
    *,
    agent_slug: str,
    pinned_provider_id: str | None,
    runtime: str,
    model: str,
    user_id: str,
) -> str | None:
    """No-usable-provider check shared with the kickoff roster pre-flight.

    ``model_provider=None`` is healthy in exactly one case: an OAuth
    subscription (CLI keychain, read out-of-band by the SDK) — either the
    agent's pin or the model-hosted fallback; check both before declaring a
    gap.
    """
    if db is not None:
        try:
            from valuz_agent.modules.providers.datastore import ProviderDatastore

            providers = ProviderDatastore(db)
            if pinned_provider_id:
                provider = await providers.get_by_id(user_id, pinned_provider_id)
                if provider is not None and provider.auth_type == "oauth":
                    # CLI-managed credentials — model_provider=None is
                    # the expected resolver output, not a gap.
                    return None
            if model:
                from valuz_agent.infra.eventbus import event_bus
                from valuz_agent.modules.providers.service import ProviderService

                match = await ProviderService(
                    datastore=providers, event_bus=event_bus
                ).resolve_provider_for_model(user_id, model)
                if match is not None and match.auth_type == "oauth":
                    return None
        except Exception:
            logger.warning(
                "credential_gap: provider lookup failed for agent %s — "
                "falling back to strict check.",
                agent_slug,
                exc_info=True,
            )

    model_part = f" for model '{model}'" if model else ""
    return (
        f"agent '{agent_slug}' has no usable model provider{model_part} "
        f"(runtime '{runtime}'). Pin a model channel on the agent, or enable "
        f"a provider that hosts this model."
    )


# ---------------------------------------------------------------------------
# Lead clone
# ---------------------------------------------------------------------------


def materialize_lead_clone(base_agent: Any) -> Any:  # returns the lead-clone AgentConfig
    """Materialize a per-task **lead clone** of *base_agent*.

    Tool surfaces ride the session's ``harness`` MCP entry
    (``build_member_session(is_lead=True)`` points it at the ``lead``
    toolset of the host toolkit MCP server) — the clone carries no tool
    declarations of its own. It survives as an identity stamp: the
    ``__lead__async`` id marks the embedded snapshot as a lead clone for
    queries/diagnostics. Stable per base; the clone exists only as the lead
    session's embedded snapshot — the kernel has no agents table.
    """
    clone_id = f"{base_agent.id}__lead__async"
    return replace(base_agent, id=clone_id, tools=())


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskProjectEnv:
    """Host project facts every task-session build needs.

    ``project_cwd`` is the MAIN project cwd — the anchor for brief spills and
    task narrative files even when the task itself runs in a worktree
    (design §5/R5: coordination files must never dirty the worktree).
    """

    project_row: Any
    project_cwd: Path
    instructions_md: str | None


@dataclass(frozen=True)
class ResolvedTaskSession:
    """A ready-to-create kernel session plus the facts callers persist.

    ``brief`` is the post-spill text (used as both the embedded brief and the
    initial ``/goal`` prompt). ``credential_gap`` is the fail-fast pre-flight
    result — the caller decides how to surface it (raise / error dict /
    ``subtask_failed`` event). ``agent_name`` is the durable display name
    members stamp into event payloads (leads don't need it).
    """

    session: Any
    agent_slug: str
    brief: str
    credential_gap: str | None
    agent_name: str | None = None


class TaskSessionResolver:
    """Resolve host definitions into task lead / member kernel sessions.

    Methods run inside the caller's unit of work (they take ``db``) so the
    extraction changes no transaction boundary. Resolution is fully awaited
    before the caller's synchronous spawn block — the §5.2 timing invariant
    (no ``await`` between registry add and ``create_task``) stays intact.
    """

    async def resolve_project_env(
        self, db: Any, *, user_id: str, project_id: str
    ) -> TaskProjectEnv | None:
        """Project row + main cwd + instructions, or ``None`` when missing."""
        ws_ds = ProjectDatastore(db)
        ws_row = await ws_ds.get_by_id(user_id, project_id)
        if ws_row is None:
            return None
        project_cwd = fs_registry.project_cwd(
            user_id,
            ws_row.id,
            cast(
                Literal["chat", "project"],
                ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
            ),
            ws_row.root_path,
        )
        ws_ctx = await ws_ds.get_context(user_id, project_id)
        return TaskProjectEnv(
            project_row=ws_row,
            project_cwd=project_cwd,
            instructions_md=ws_ctx.instructions_md if ws_ctx else None,
        )

    async def member_exists(
        self, db: Any, *, user_id: str, project_id: str, agent_slug: str
    ) -> bool:
        """True when *agent_slug* is deployed into *project_id* (membership)."""
        return await ProjectMemberDatastore(db).get(user_id, project_id, agent_slug) is not None

    async def preflight_member_providers(
        self, db: Any, *, user_id: str, project_id: str
    ) -> list[str]:
        """Pre-run model-config check over the project's full roster.

        The lead can dispatch to ANY member, so kickoff/commit verify every
        member resolves a usable provider (chat-parity fallback included)
        up front — an unconfigured member surfaces as an immediate,
        actionable kickoff error instead of minutes into the run as a
        dispatch-time ``subtask_failed``. Returns one human-readable line
        per member with a gap (empty = all clear). Members whose library
        agent can't be resolved are skipped — dispatch has its own error
        for orphaned slugs. Pure DB reads (provider model options resolve
        from stored rows / static descriptors — no network).
        """
        member_ds = ProjectMemberDatastore(db)
        rows = await member_ds.list_by_project(user_id, project_id)
        providers = _provider_resolver_deps(db).get("providers")
        gaps: list[str] = []
        for row in rows:
            agent = await _member_agent_config(row, member_ds, user_id=user_id)
            if agent is None:
                continue
            resolved = await _resolve_agent_provider(
                agent=agent, model=agent.model, providers=providers, user_id=user_id
            )
            if resolved is not None:
                continue
            gap = await _provider_gap(
                db,
                agent_slug=row.agent_slug,
                pinned_provider_id=(agent.metadata or {}).get("provider_id"),
                runtime=agent.runtime_provider or "",
                model=agent.model or "",
                user_id=user_id,
            )
            if gap is not None:
                gaps.append(gap)
        return gaps

    async def list_member_descriptors(
        self, db: Any, *, user_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Roster rows for the lead's ``list_members`` tool.

        slug / display name / runtime / source agent / role summary — the
        facts the lead needs to dispatch accurately (lead-dispatch-mvp §1.5).
        """
        member_ds = ProjectMemberDatastore(db)
        rows = await member_ds.list_by_project(user_id, project_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            agent_cfg = await _member_agent_config(row, member_ds, user_id=user_id)
            result.append(
                {
                    "slug": row.agent_slug,
                    "name": agent_cfg.name if agent_cfg else row.agent_slug,
                    "runtime": agent_cfg.runtime_provider if agent_cfg else "unknown",
                    "source_agent_slug": row.source_agent_slug,
                    "role_summary": summarize_role(agent_cfg.instructions) if agent_cfg else "",
                }
            )
        return result

    async def resolve_lead(
        self,
        db: Any,
        *,
        env: TaskProjectEnv,
        project_id: str,
        task_id: str,
        task_title: str,
        agent_slug: str,
        cwd: str,
        brief: str,
        user_id: str,
        plan_pre_committed: bool = False,
        worktree_notice: str | None = None,
    ) -> ResolvedTaskSession | Failure:
        """Resolve a task lead session (kickoff and commit share this path).

        Fetches the lead's membership, materializes the per-task lead clone,
        spills an over-cap brief (anchored at the MAIN project cwd), builds
        the kernel session and runs the credential pre-flight.
        """
        member_ds = ProjectMemberDatastore(db)
        lead_member = await member_ds.get(user_id, project_id, agent_slug)
        if lead_member is None:
            return Failure(
                f"lead agent {agent_slug!r} is not a member of project {project_id!r}"
            )
        lead_agent = await _member_agent_config(lead_member, member_ds, user_id=user_id)
        lead_clone = materialize_lead_clone(lead_agent) if lead_agent is not None else None

        brief = spill_goal_brief_if_too_long(
            brief,
            run_dir=str(env.project_cwd),
            task_id=task_id,
            label=agent_slug,
            is_lead=True,
        )

        session = await build_member_session(
            project_id=project_id,
            agent_slug=agent_slug,
            members=member_ds,
            is_lead=True,
            task_id=task_id,
            run_dir=cwd,
            brief=brief,
            project_name=env.project_row.name,
            project_instructions_md=env.instructions_md,
            # Lead runs the whole task in goal mode: the kernel auto-loops
            # until the task goal is met. ``finish_task`` remains the
            # authoritative terminal (it forces mode back to default).
            goal_mode=True,
            plan_pre_committed=plan_pre_committed,
            worktree_notice=worktree_notice,
            user_id=user_id,
            task_title=task_title,
            **_provider_resolver_deps(db),
        )
        if session is None:
            return Failure(f"could not build lead session for {agent_slug!r}")
        if lead_clone is not None:
            session = embed_agent_config(session, lead_clone)

        gap = await _credential_gap(session, agent_slug, db=db, user_id=user_id)
        return ResolvedTaskSession(
            session=session,
            agent_slug=agent_slug,
            brief=brief,
            credential_gap=gap,
        )

    async def resolve_member(
        self,
        db: Any,
        *,
        env: TaskProjectEnv,
        project_id: str,
        task_id: str,
        task_title: str,
        agent_slug: str,
        run_dir: str,
        brief: str,
        user_id: str,
        spill_label: str | None = None,
        lead_session_id: str | None = None,
        worktree_notice: str | None = None,
    ) -> ResolvedTaskSession | Failure:
        """Resolve a dispatched member's sub-run session.

        Members run in goal mode (self-loop until the scoped goal is met,
        then auto-exit and report ``member_done``); the lead's
        ``review_subtask`` authority is unchanged. The display name is
        captured here — where the agent is guaranteed resolvable — so events
        carry a durable ``agent_name`` instead of a racy frontend join.
        """
        member_ds = ProjectMemberDatastore(db)

        brief = spill_goal_brief_if_too_long(
            brief,
            run_dir=str(env.project_cwd),
            task_id=task_id,
            label=spill_label or agent_slug,
            is_lead=False,
        )

        session = await build_member_session(
            project_id=project_id,
            agent_slug=agent_slug,
            members=member_ds,
            is_lead=False,
            task_id=task_id,
            run_dir=run_dir,
            brief=brief,
            project_name=env.project_row.name,
            project_instructions_md=env.instructions_md,
            lead_session_id=lead_session_id,
            goal_mode=True,
            worktree_notice=worktree_notice,
            user_id=user_id,
            task_title=task_title,
            **_provider_resolver_deps(db),
        )
        if session is None:
            return Failure(f"agent {agent_slug!r} not found in project {project_id!r}")

        agent_name = await resolve_agent_display_name(project_id, agent_slug, user_id)
        gap = await _credential_gap(session, agent_slug, db=db, user_id=user_id)
        return ResolvedTaskSession(
            session=session,
            agent_slug=agent_slug,
            brief=brief,
            credential_gap=gap,
            agent_name=agent_name,
        )


task_session_resolver = TaskSessionResolver()

__all__ = [
    "ResolvedTaskSession",
    "TaskProjectEnv",
    "TaskSessionResolver",
    "_credential_gap",
    "_provider_gap",
    "_provider_resolver_deps",
    "materialize_lead_clone",
    "task_session_resolver",
]
