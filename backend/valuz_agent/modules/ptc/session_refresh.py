"""Per-turn PTC convergence for a session row.

Converges three machine-managed facets with the user's ``ptc_enabled``
preference and the session's current data connectors:

- the generated ``ptc-tools-*`` skill path in ``session.skills``;
- the kernel opt-in signal ``metadata["ptc"] = {"servers": [...]}`` (the
  runtime factory exposes ``execute_code`` exactly while this resolves to
  ≥1 live server — see ``kernel/src/ptc/executor.maybe_expose_execute_code``);
- the ``<ptc-policy>`` dispatch-rule block in ``session.instructions``.

Same shape as the citation-policy refresher: idempotent, all-or-nothing per
facet set, and a no-op writes nothing so the prompt cache stays warm.
Invoked from ``modules/sessions/pre_turn.chat_capability_hook`` AFTER the
always-on MCP re-stamp, so the decision reads the turn's final server set.
"""

from __future__ import annotations

import logging

from app.schemas import UpdateSessionRequest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.schemas
from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.system_prompt_builder import (
    ensure_ptc_system_policy,
    remove_ptc_system_policy,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.ptc.service import (
    code_face_server_names,
    ensure_ptc_skill,
    is_ptc_skill_path,
)
from valuz_agent.modules.settings.preferences import get_ptc_enabled

logger = logging.getLogger(__name__)

PTC_METADATA_KEY = "ptc"  # mirrors kernel/src/ptc/executor.PTC_METADATA_KEY


async def refresh_ptc_for_session(session_id: str, user_id: str) -> bool:
    """Converge the PTC facets on one session. Returns True when the row
    changed. Safe to call repeatedly; never raises into the turn path."""
    session = await kernel_client.get_session(user_id, session_id)
    if session is None or session.status in ("terminated",):
        return False

    async with async_unit_of_work(commit=False) as db:
        enabled = await get_ptc_enabled(db, user_id=user_id)

    names = code_face_server_names(session.mcp_servers or ())
    want = enabled and bool(names)

    skill_path: str | None = None
    if want:
        by_name = {getattr(m, "name", ""): m for m in session.mcp_servers or ()}
        configs = [by_name[n] for n in names if n in by_name]
        built = await ensure_ptc_skill(user_id, configs)
        if built is None:
            # Fail closed: without wrappers the code face would advertise
            # nothing callable — keep the session fully native this turn.
            want = False
        else:
            skill_path = str(built.resolve(strict=False))

    current_skills = list(session.skills or ())
    metadata = dict(session.metadata or {})
    instructions = session.instructions or ""

    kept_skills = [p for p in current_skills if not is_ptc_skill_path(p)]
    if want and skill_path is not None:
        new_skills = [*kept_skills, skill_path]
        new_metadata = {**metadata, PTC_METADATA_KEY: {"servers": names}}
        new_instructions = ensure_ptc_system_policy(instructions)
    else:
        new_skills = kept_skills
        new_metadata = {k: v for k, v in metadata.items() if k != PTC_METADATA_KEY}
        new_instructions = remove_ptc_system_policy(instructions)

    if (
        new_skills == current_skills
        and new_metadata == metadata
        and new_instructions == instructions
    ):
        return False

    await kernel_client.update_session(
        user_id,
        session_id,
        UpdateSessionRequest(
            skills=new_skills,
            instructions=new_instructions,
            metadata=new_metadata,
        ),
    )
    logger.info(
        "ptc: refreshed session %s (enabled=%s servers=%s skill=%s)",
        session_id,
        enabled,
        names,
        skill_path is not None,
    )
    return True
