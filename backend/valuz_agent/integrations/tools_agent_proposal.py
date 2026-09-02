"""``propose_agent`` / ``update_agent`` / ``list_skills`` — natural-language
agent create + edit tools.

The agent calls ``propose_agent`` once it has gathered everything a new
Agent needs (name, instructions, brain, and *equipment* — skill slugs +
connector slugs). The handler does **not** write to the agent library —
that's the user's prerogative, applied via
``POST /v1/agents/proposals/{session_id}/confirm`` when they click
"创建并部署" on the proposal card the frontend renders in response to the
``tool_use`` event this call produces. Same "agent proposes, user
disposes" trust model as ``submit_skill``.

``update_agent`` is the edit counterpart for an agent that ALREADY exists.
It is a **direct write** (not a proposal): it shares propose_agent's spec
validation but applies the patch immediately through
``AgentService.update_agent``, which live-references into every project the
agent is deployed to. Only the fields passed are changed.

Why a validating no-op is enough
--------------------------------
The kernel records a ``tool_use`` event the moment any tool fires; the
frontend SSE subscriber for that session already knows ``session_id`` (it
owns the page). Pairing the event payload (the full agent spec) with the
session id at the UI layer gives the confirm endpoint everything it needs
— no server-side staging required (unlike skills, whose content lives on
disk). The handler's job is to *validate* the spec early so the model
fixes problems before the user is asked to confirm:

- skill slugs must already exist in ``valuz_skill_index`` — at session
  build ``capability_resolver.resolve_skill_slugs_to_paths`` silently
  drops unindexed slugs, so an unindexed slug would bind to nothing. The
  fix is the existing flow: author with ``skill-creator`` → ``submit_skill``
  → user saves → the slug becomes indexable.
- connector slugs must exist in ``valuz_connector`` (created via
  ``create_mcp``). OAuth connectors only work once authorized, but they
  are still bindable.

Why this lives in valuz, not the kernel
---------------------------------------
The agent library, project membership, skill index and connector catalog
are all host concerns. The kernel intentionally stays generic.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json
import logging
from typing import Any

# Side-effect import — surfaces ``src.core...`` on sys.path. Without this,
# the kernel package fails to resolve when this module is imported during
# app startup.
import valuz_agent.boot.kernel  # noqa: F401

from src.core.tools import ExecContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)

PROPOSE_AGENT_TOOL_NAME = "propose_agent"
UPDATE_AGENT_TOOL_NAME = "update_agent"
LIST_SKILLS_TOOL_NAME = "list_skills"
LIST_AGENTS_TOOL_NAME = "list_agents"
LIST_PROJECT_MEMBERS_TOOL_NAME = "list_project_members"
LIST_MODEL_OPTIONS_TOOL_NAME = "list_model_options"
DEPLOY_AGENT_TOOL_NAME = "deploy_agent"

# Mirrors the runtimes the agent library accepts (see AgentRow.runtime).
VALID_RUNTIMES = ("claude_agent", "codex", "deepagents", "deepseek_harness")
# Mirrors kernel EffortLevel / api EffortLevel.
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The model the confirm endpoint + frontend fall back to when ``propose_agent``
# omits one (api/routes/agents.py ProposeAgentConfirmRequest.model). Validation
# treats an omitted model AS this value, because that's what actually gets
# created — so an omitted model on a runtime this can't drive is still caught.
DEFAULT_MODEL = "claude-sonnet-4-6"


PROPOSE_AGENT_DESCRIPTION = (
    "Propose a NEW agent for the user to review, then create and deploy into "
    "the current project. Use this when the user describes an agent they want "
    "built in natural language. Call it ONCE, after you've assembled the "
    "agent's equipment.\n\n"
    "## Check for an existing agent FIRST (accuracy)\n"
    "Before proposing a NEW agent, call `list_agents` to see if a suitable one "
    "already exists in the library — and, in a project, `list_project_members` "
    "to see who's already deployed. If a fitting agent already exists, use "
    "`deploy_agent` to add it to the project instead of creating a duplicate. "
    "Only propose a new agent when none fits.\n\n"
    "The user is shown a card to confirm; nothing is written until they "
    "approve. After calling this, STOP — do not keep editing unless the user "
    "asks for changes.\n\n"
    "## Assemble equipment FIRST\n"
    "- skills: a list of skill slugs to bind. Each slug MUST already exist in "
    "the library (be indexed). To add a skill the user doesn't have yet, author "
    "it with the skill-creator skill and call `submit_skill`; once the user "
    "saves it, its slug becomes bindable. Use `list_skills` to see existing "
    "slugs. An unindexed slug is rejected with guidance — never bind one.\n"
    "- connectors: a list of connector slugs to bind. Create connectors with "
    "`create_mcp` first; OAuth connectors must be authorized by the user to "
    "actually work, but can still be bound now.\n\n"
    "## Brain — call `list_model_options` FIRST\n"
    "Before choosing a runtime + model, call `list_model_options` to see which "
    "runtimes are available on THIS host and which models are configured — each "
    "model lists the exact runtimes it can run on. The runtime and model you "
    "pass MUST be a real, compatible pair, or this tool rejects it.\n"
    "- runtime: one of claude_agent | codex | deepagents | deepseek_harness "
    "(default claude_agent). "
    "codex needs the codex binary installed; the other two are always available.\n"
    "- model: a model id from `list_model_options` whose runtime list includes "
    "the runtime you picked. Do NOT mix a Claude model with the codex runtime "
    "(or vice-versa) — they speak different wire protocols. If you pass a "
    "non-default runtime, ALWAYS pass an explicit compatible model (omitting it "
    "falls back to " + DEFAULT_MODEL + ", which only runs on claude_agent / "
    "deepagents).\n"
    "- effort: low | medium | high | xhigh | max (optional reasoning budget).\n\n"
    "Do NOT pass a slug — the backend derives a unique one from the name.\n\n"
    "Returns JSON with ok and, on success, the validated spec echoed back."
)

PROPOSE_AGENT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Display name of the agent. Required.",
        },
        "instructions": {
            "type": "string",
            "description": (
                "The agent's system prompt / working method (role, method, "
                "output discipline, boundaries). Required."
            ),
        },
        "description": {
            "type": "string",
            "description": "One-line description shown in the library.",
        },
        "runtime": {
            "type": "string",
            "enum": list(VALID_RUNTIMES),
            "description": "Runtime engine. Default claude_agent.",
        },
        "model": {
            "type": "string",
            "description": "Model id. Default claude-sonnet-4-6.",
        },
        "effort": {
            "type": "string",
            "enum": list(VALID_EFFORTS),
            "description": "Optional reasoning-effort budget. Omit for SDK default.",
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Skill slugs to bind. Each must already be indexed in the "
                "library (see the tool description)."
            ),
        },
        "connectors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Connector slugs to bind (created via create_mcp).",
        },
        "avatar": {
            "type": "string",
            "description": "Optional preset avatar key or asset URL.",
        },
    },
    "required": ["name", "instructions"],
}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]


async def _gather_model_options(db: Any, user_id: str) -> Any:
    """Fully-resolved model options for the user — the SAME read model the
    Settings → Model picker uses (``GET /v1/settings/model-options``), so the
    agent sees exactly the runtimes + models the host can actually run. Returns
    a ``ModelOptionsResponse``; ``groups`` is empty when no provider is
    configured."""
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.providers.datastore import ProviderDatastore
    from valuz_agent.modules.providers.service import ProviderService
    from valuz_agent.modules.settings.model_options import (
        CurrentDefault,
        build_model_options,
        to_option_input,
    )
    from valuz_agent.modules.settings.preferences import (
        get_default_model,
        get_default_provider_id,
        get_default_runtime,
    )

    svc = ProviderService(
        datastore=ProviderDatastore(db),
        event_bus=event_bus,
    )
    items = await svc.list_providers(user_id)
    inputs = [to_option_input(it) for it in items]
    current = CurrentDefault(
        runtime=await get_default_runtime(db, user_id=user_id),
        provider_id=await get_default_provider_id(db, user_id=user_id),
        model=await get_default_model(db, user_id=user_id),
    )
    return build_model_options(inputs, current)


def _model_runtime_index(opts: Any) -> dict[str, set[str]]:
    """model_id → the set of runtimes that can drive it, across every
    configured provider."""
    idx: dict[str, set[str]] = {}
    for group in opts.groups:
        for prov in group.providers:
            for m in prov.models:
                idx.setdefault(m.model_id, set()).update(m.runtimes)
    return idx


def _models_for_runtime(opts: Any, runtime: str) -> list[str]:
    """Configured model ids that can run on ``runtime``, in display order."""
    out: list[str] = []
    for group in opts.groups:
        for prov in group.providers:
            for m in prov.models:
                if runtime in m.runtimes and m.model_id not in out:
                    out.append(m.model_id)
    return out


def _default_model_runtimes() -> set[str]:
    """Runtimes that can drive ``DEFAULT_MODEL``.

    ``DEFAULT_MODEL`` (claude-sonnet-4-6) is an anthropic-wire model, so the
    runtimes that can run it are exactly those whose supported protocols include
    ``anthropic`` — derived from the registry, not hard-coded. Used to catch an
    *omitted* model on a runtime that can't drive the default (e.g. codex)."""
    from valuz_agent.adapters.runtime_registry import RUNTIME_REGISTRY

    return {
        rid for rid, spec in RUNTIME_REGISTRY.items() if "anthropic" in spec.supported_protocols
    }


def _validate_runtime_model(
    runtime: str, model_arg: str, opts: Any
) -> tuple[str | None, list[str]]:
    """Check the (runtime, model) pair. Returns ``(error, warnings)``.

    ``error`` is non-None when the pair can't work — the handler rejects so the
    model re-proposes with a corrected, explicit pair (the frontend confirms
    with the model's *raw args*, so the args must be right, not just our echo).
    This is the fix for the codex/claude model mix-up. Two definite, catchable
    cases:

    1. The model IS configured but the chosen runtime isn't in its runtime set
       — e.g. a Claude model on the codex runtime (different wire protocols).
    2. The model was OMITTED on a runtime that can't drive ``DEFAULT_MODEL``
       (the value confirm falls back to) — e.g. codex, which speaks
       openai-response, not anthropic.

    An explicit but unknown model is allowed with a warning — it may be a
    gateway/custom id the server can't enumerate.
    """
    idx = _model_runtime_index(opts)
    explicit = bool(model_arg)
    effective = model_arg or DEFAULT_MODEL
    supported = idx.get(effective)
    runtime_models = _models_for_runtime(opts, runtime)

    # 1) Definite mismatch — the heart of the bug.
    if supported is not None and runtime not in supported:
        fix = (
            "; or pick a model for this runtime: " + ", ".join(runtime_models)
            if runtime_models
            else " — pick a runtime it can run on"
        )
        return (
            f"propose_agent: model '{effective}' cannot run on runtime "
            f"'{runtime}' — it runs on {', '.join(sorted(supported))}. Set "
            f"runtime to one of those{fix}. Call list_model_options to see "
            "configured models and the runtimes each one supports."
        ), []

    # 2) Omitted model on a runtime that can't drive the default.
    if not explicit and runtime not in _default_model_runtimes():
        picks = (
            "one of: " + ", ".join(runtime_models)
            if runtime_models
            else "a model from list_model_options"
        )
        return (
            f"propose_agent: no model was given and the default '{DEFAULT_MODEL}' "
            f"can't run on runtime '{runtime}'. Pass an explicit model — {picks}."
        ), []

    # 3) Explicit, unknown model — may be a valid gateway/custom id. Allow + warn.
    if explicit and supported is None and idx:
        return None, [
            f"model '{effective}' isn't among your configured models — make sure "
            "a provider serves it, or pick one from list_model_options."
        ]

    return None, []


def _runtime_availability_warning(runtime: str) -> str | None:
    """A soft warning when the picked runtime can't currently run on this host
    (e.g. codex with no binary). The agent can still be created — it just won't
    run until the runtime is installed."""
    from valuz_agent.adapters.runtime_registry import is_runtime_available

    available, reason = is_runtime_available(runtime)
    if available:
        return None
    return f"runtime '{runtime}' is not available on this host: {reason}"


async def _propose_agent_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    """Validate the proposed agent spec; never write. The frontend renders a
    confirmation card from the ``tool_use`` event and the user's confirm call
    does the actual create + deploy."""
    name = str(args.get("name") or "").strip()
    instructions = str(args.get("instructions") or "").strip()
    if not name:
        return _err("propose_agent: 'name' is required")
    if not instructions:
        return _err("propose_agent: 'instructions' is required")

    runtime = str(args.get("runtime") or "claude_agent").strip()
    if runtime not in VALID_RUNTIMES:
        return _err(
            f"propose_agent: invalid runtime '{runtime}' — must be one of "
            f"{', '.join(VALID_RUNTIMES)}"
        )

    effort = args.get("effort")
    if effort is not None and str(effort).strip() and str(effort) not in VALID_EFFORTS:
        return _err(
            f"propose_agent: invalid effort '{effort}' — must be one of {', '.join(VALID_EFFORTS)}"
        )

    model_arg = str(args.get("model") or "").strip()
    skills = _as_str_list(args.get("skills"))
    connectors = _as_str_list(args.get("connectors"))

    # Validate equipment exists. Unindexed skills are a hard error (they would
    # silently bind to nothing); missing connectors are a soft warning
    # (mirrors create_mcp's credentials_required guidance) since the user may
    # be about to create them. The configured-model catalog is gathered in the
    # same unit of work so the runtime/model pair can be validated below.
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.skills.datastore import SkillDatastore

    user_id = context.user_id
    missing_skills: list[str] = []
    missing_connectors: list[str] = []
    async with async_unit_of_work(commit=False) as db:
        if skills:
            indexed = {r.slug for r in await SkillDatastore(db).list_skills(user_id)}
            missing_skills = [s for s in skills if s not in indexed]
        if connectors:
            cds = ConnectorDatastore(db)
            for slug in connectors:
                if await cds.get_by_slug(user_id, slug) is None:
                    missing_connectors.append(slug)
        model_opts = await _gather_model_options(db, user_id)

    if missing_skills:
        return _err(
            "propose_agent: these skill slugs are not in the library yet, so "
            "they can't be bound: "
            + ", ".join(missing_skills)
            + ". Author each one with the skill-creator skill and call "
            "submit_skill; once the user saves it, retry. Use list_skills to "
            "see available slugs."
        )

    # The brain check — reject an unrunnable (runtime, model) pair so the model
    # re-proposes with a corrected, explicit pair (see _validate_runtime_model).
    model_error, model_warnings = _validate_runtime_model(runtime, model_arg, model_opts)
    if model_error:
        return _err(model_error)

    warnings: list[str] = []
    if missing_connectors:
        warnings.append(
            "These connector slugs don't exist yet; create them with create_mcp "
            "before the user confirms: " + ", ".join(missing_connectors)
        )
    warnings.extend(model_warnings)
    runtime_warning = _runtime_availability_warning(runtime)
    if runtime_warning:
        warnings.append(runtime_warning)

    spec = {
        "name": name,
        "instructions": instructions,
        "description": str(args.get("description") or ""),
        "runtime": runtime,
        "model": model_arg or DEFAULT_MODEL,
        "effort": (str(effort).strip() or None) if effort is not None else None,
        "skills": skills,
        "connectors": connectors,
        "avatar": (str(args.get("avatar")).strip() or None) if args.get("avatar") else None,
    }
    logger.info(
        "propose_agent: name=%s runtime=%s model=%s skills=%d connectors=%d (missing_conn=%s)",
        name,
        runtime,
        spec["model"],
        len(skills),
        len(connectors),
        missing_connectors,
    )
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "spec": spec,
                "warnings": warnings,
                "next_step": (
                    "Proposed for the user's review. They will see a card to "
                    "create and deploy this agent into the current project. "
                    "Stop here — do not keep editing unless the user asks."
                ),
            },
            ensure_ascii=False,
        )
    )


UPDATE_AGENT_DESCRIPTION = (
    "Modify an EXISTING agent in the library, in place. Use this when the user "
    "asks to change an agent they already have — its instructions, name, "
    "description, brain (runtime/model/effort), or equipment (skills/connectors).\n\n"
    "## This applies IMMEDIATELY — it is NOT a proposal\n"
    "Unlike `propose_agent` (which only drafts a NEW agent for the user to "
    "confirm), `update_agent` writes the change straight to the library the "
    "moment you call it. It is a **live reference**: the edit propagates to every "
    "project the agent is deployed to, picked up by every NEW session there. "
    "Sessions already running keep the config snapshot they started with. Because "
    "it's a direct write, only call it when the user has actually asked for the "
    "change — confirm the specifics with them first if anything is ambiguous.\n\n"
    "## Partial update — only what you pass changes\n"
    "Pass `agent_slug` (from `list_agents`) plus ONLY the fields you want to "
    "change; omitted fields are left exactly as they are. For list/clearable "
    "fields, an explicit value REPLACES the old one wholesale:\n"
    "- skills / connectors: the list you pass becomes the agent's full set "
    "(pass the complete intended list, not just additions; `[]` clears it). Same "
    "existence rules as propose_agent — every skill slug must already be indexed "
    "(author with skill-creator → submit_skill first), connectors created via "
    "create_mcp. Use `list_skills` / `list_agents` to check current state.\n"
    "- effort / avatar: an empty string clears the override.\n"
    "- name / instructions CANNOT be blanked — omit them to keep the current "
    "value.\n\n"
    "## Changing the brain — keep runtime + model compatible\n"
    "If you change `runtime` OR `model`, the resulting pair must be valid (the "
    "model's runtimes must include the runtime). When you change only one side, "
    "this checks it against the agent's CURRENT value for the other side and "
    "rejects an unrunnable pair — call `list_model_options` first and pass a "
    "compatible pair (e.g. switching to the codex runtime needs a codex model "
    "too). Omit both to leave the brain untouched.\n\n"
    "Returns JSON with ok, the slug, the list of changed fields, and any warnings."
)

UPDATE_AGENT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "agent_slug": {
            "type": "string",
            "description": "Slug of the library agent to modify (from list_agents). Required.",
        },
        "name": {
            "type": "string",
            "description": "New display name. Omit to keep current; cannot be blank.",
        },
        "instructions": {
            "type": "string",
            "description": (
                "New system prompt / working method. Omit to keep current; cannot be blank."
            ),
        },
        "description": {
            "type": "string",
            "description": "New one-line description. Empty string clears it.",
        },
        "runtime": {
            "type": "string",
            "enum": list(VALID_RUNTIMES),
            "description": "New runtime engine. Must stay compatible with the model.",
        },
        "model": {
            "type": "string",
            "description": "New model id. Must stay compatible with the runtime.",
        },
        "effort": {
            "type": "string",
            "description": (
                "New reasoning-effort budget (one of "
                + ", ".join(VALID_EFFORTS)
                + "). Empty string clears the override."
            ),
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Replacement skill-slug set (full intended list; [] clears). Each "
                "slug must already be indexed."
            ),
        },
        "connectors": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Replacement connector-slug set (full intended list; [] clears). "
                "Created via create_mcp."
            ),
        },
        "avatar": {
            "type": "string",
            "description": "New preset avatar key or asset URL. Empty string clears it.",
        },
    },
    "required": ["agent_slug"],
}


async def _update_agent_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    """Apply a partial edit to an existing library agent and write it straight
    away. Mirrors ``propose_agent``'s validation (skills indexed, connectors
    exist, runtime/model compatible) but — unlike propose — there is no confirm
    card: the change lands via ``AgentService.update_agent`` and live-references
    into every project the agent is deployed to."""
    slug = str(args.get("agent_slug") or "").strip()
    if not slug:
        return _err("update_agent: 'agent_slug' is required")

    # Build the patch from ONLY the keys the caller supplied. An absent key
    # leaves the field untouched; a present key applies (mirroring
    # AgentService.update_agent's clear-on-empty semantics for nullable fields).
    patch: dict[str, Any] = {}
    if "name" in args:
        v = str(args.get("name") or "").strip()
        if not v:
            return _err("update_agent: 'name' cannot be blanked — omit it to keep the current name")
        patch["name"] = v
    if "instructions" in args:
        v = str(args.get("instructions") or "").strip()
        if not v:
            return _err("update_agent: 'instructions' cannot be blanked — omit it to keep current")
        patch["instructions"] = v
    if "description" in args:
        patch["description"] = str(args.get("description") or "").strip()
    if "avatar" in args:
        patch["avatar"] = str(args.get("avatar") or "").strip() or None
    if "runtime" in args and str(args.get("runtime") or "").strip():
        runtime = str(args.get("runtime")).strip()
        if runtime not in VALID_RUNTIMES:
            return _err(
                f"update_agent: invalid runtime '{runtime}' — must be one of "
                f"{', '.join(VALID_RUNTIMES)}"
            )
        patch["runtime"] = runtime
    if "model" in args and str(args.get("model") or "").strip():
        patch["model"] = str(args.get("model")).strip()
    if "effort" in args:
        effort = str(args.get("effort") or "").strip()
        if effort and effort not in VALID_EFFORTS:
            return _err(
                f"update_agent: invalid effort '{effort}' — must be one of "
                f"{', '.join(VALID_EFFORTS)}"
            )
        patch["effort"] = effort or None
    if "skills" in args:
        patch["skills"] = _as_str_list(args.get("skills"))
    if "connectors" in args:
        patch["connector_types"] = _as_str_list(args.get("connectors"))

    if not patch:
        return _err(
            "update_agent: no editable fields provided — pass at least one of "
            "name, description, instructions, runtime, model, effort, skills, "
            "connectors, avatar (besides agent_slug)."
        )

    # Validate equipment + gather the model catalog in one read-only unit of
    # work, exactly like propose_agent. Capture the current runtime/model inside
    # the block so the brain check can validate the EFFECTIVE pair even when only
    # one side is being changed.
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.datastore import AgentDatastore
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.skills.datastore import SkillDatastore

    user_id = context.user_id
    needs_brain_check = "runtime" in patch or "model" in patch
    missing_skills: list[str] = []
    missing_connectors: list[str] = []
    model_opts: Any = None
    async with async_unit_of_work(commit=False) as db:
        existing = await AgentDatastore(db).get_agent(user_id, slug)
        if existing is None:
            return _err(
                f"update_agent: no library agent with slug '{slug}'. Call "
                "list_agents to see valid slugs."
            )
        existing_runtime = existing.runtime
        existing_model = existing.model
        if patch.get("skills"):
            indexed = {r.slug for r in await SkillDatastore(db).list_skills(user_id)}
            missing_skills = [s for s in patch["skills"] if s not in indexed]
        if patch.get("connector_types"):
            cds = ConnectorDatastore(db)
            for cslug in patch["connector_types"]:
                if await cds.get_by_slug(user_id, cslug) is None:
                    missing_connectors.append(cslug)
        if needs_brain_check:
            model_opts = await _gather_model_options(db, user_id)

    if missing_skills:
        return _err(
            "update_agent: these skill slugs are not in the library yet, so they "
            "can't be bound: "
            + ", ".join(missing_skills)
            + ". Author each one with the skill-creator skill and call "
            "submit_skill; once the user saves it, retry. Use list_skills to see "
            "available slugs."
        )

    warnings: list[str] = []
    if needs_brain_check:
        effective_runtime = patch.get("runtime", existing_runtime)
        effective_model = patch.get("model") or existing_model or DEFAULT_MODEL
        model_error, model_warnings = _validate_runtime_model(
            effective_runtime, effective_model, model_opts
        )
        if model_error:
            return _err(model_error.replace("propose_agent:", "update_agent:", 1))
        warnings.extend(model_warnings)
        runtime_warning = _runtime_availability_warning(effective_runtime)
        if runtime_warning:
            warnings.append(runtime_warning)
    if missing_connectors:
        warnings.append(
            "These connector slugs don't exist yet; create them with create_mcp "
            "so the binding resolves: " + ", ".join(missing_connectors)
        )

    from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService

    async with async_unit_of_work() as db:
        svc = AgentService(db)
        try:
            row = await svc.update_agent(user_id, slug, patch)
        except AgentNotFoundError:
            return _err(
                f"update_agent: no library agent with slug '{slug}'. Call "
                "list_agents to see valid slugs."
            )
        agent_name = row.name

    changed = sorted(patch.keys())
    logger.info(
        "update_agent: slug=%s changed=%s (missing_conn=%s)",
        slug,
        changed,
        missing_connectors,
    )
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "slug": slug,
                "name": agent_name,
                "changed": changed,
                "warnings": warnings,
                "next_step": (
                    "Saved. The change is live across every project this agent is "
                    "deployed to and applies to new sessions there. Stop here — "
                    "do not keep editing unless the user asks for more changes."
                ),
            },
            ensure_ascii=False,
        )
    )


LIST_SKILLS_DESCRIPTION = (
    "List the skills already in the user's library (slug, name, description, "
    "scope, version, whether it is editable). Call this BEFORE creating a new "
    "skill so you can tell the user when a similar one already exists and let "
    "them choose between improving it and creating a new one; also for binding "
    "existing skills by slug when proposing an agent. "
    "This listing is metadata only — it does NOT give you a skill's contents "
    "or its path. To modify one, call `prepare_skill_edit(slug=...)`: it puts "
    "the library's current version in your staging directory so you edit the "
    "real thing instead of rewriting it from memory. Read-only."
)

LIST_SKILLS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_skills_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.skills.datastore import SkillDatastore

    from valuz_agent.modules.artifacts.service import get_head_revision

    user_id = context.user_id
    items: list[dict[str, Any]] = []
    async with async_unit_of_work(commit=False) as db:
        rows = await SkillDatastore(db).list_skills(user_id)
        for r in rows:
            if getattr(r, "status", "available") != "available":
                continue
            version: int | None = None
            artifact_id = getattr(r, "artifact_id", None)
            if artifact_id:
                head = await get_head_revision(db, user_id, artifact_id)
                version = head.version_no if head is not None else None
            items.append(
                {
                    "slug": r.slug,
                    "name": r.name,
                    "description": (r.description or "")[:200],
                    "scope": r.scope,
                    "version": version,
                    "editable": not (
                        bool(getattr(r, "readonly", False))
                        or bool(getattr(r, "is_locked", False))
                        or bool(getattr(r, "protected", False))
                    ),
                    "creation_origin": getattr(r, "creation_origin", None),
                }
            )
    return ToolResult(content=json.dumps({"ok": True, "skills": items}, ensure_ascii=False))


LIST_AGENTS_DESCRIPTION = (
    "List the agents already in the library (slug, name, description, source). "
    "Call this BEFORE proposing a new agent so you can reuse an existing one "
    "(via deploy_agent) instead of creating a duplicate. Read-only."
)

LIST_AGENTS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_agents_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.datastore import AgentDatastore

    user_id = context.user_id
    async with async_unit_of_work(commit=False) as db:
        rows = await AgentDatastore(db).list_agents(user_id)
    items = [
        {
            "slug": r.slug,
            "name": r.name,
            "description": (r.description or "")[:200],
            "source": r.source,
        }
        for r in rows
    ]
    return ToolResult(content=json.dumps({"ok": True, "agents": items}, ensure_ascii=False))


LIST_MODEL_OPTIONS_DESCRIPTION = (
    "List the runtimes available on THIS host and the models the user has "
    "configured, so you can pick a valid (runtime, model) brain when proposing "
    "an agent. Call this BEFORE propose_agent whenever the user wants a "
    "specific runtime/model, or to confirm a Claude vs Codex choice.\n\n"
    "Returns:\n"
    "- runtimes: each {id, display_name, available, unavailable_reason}. An "
    "unavailable runtime (e.g. codex without its binary) can still be assigned "
    "but won't run until installed.\n"
    "- providers: each configured channel and its models; every model lists the "
    "exact runtimes it can run on. PICK A MODEL WHOSE runtimes INCLUDE THE "
    "runtime YOU CHOOSE — never pair a Claude model with the codex runtime or a "
    "codex model with claude_agent.\n"
    "- current_default: the user's default {runtime, provider_id, model}.\n\n"
    "Read-only. Subscription channels report status=client_resolved (their "
    "login lives in a local keychain the server can't see)."
)

LIST_MODEL_OPTIONS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_model_options_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.adapters.runtime_registry import is_runtime_available, list_runtimes
    from valuz_agent.infra.db import async_unit_of_work

    user_id = context.user_id
    async with async_unit_of_work(commit=False) as db:
        opts = await _gather_model_options(db, user_id)

    runtimes = []
    for spec in list_runtimes():
        available, reason = is_runtime_available(spec.id)
        runtimes.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "available": available,
                "unavailable_reason": reason,
            }
        )

    providers = []
    for group in opts.groups:
        for prov in group.providers:
            providers.append(
                {
                    "provider_id": prov.provider_id,
                    "label": prov.label,
                    "kind": prov.kind,
                    "source": prov.source,
                    "status": prov.status,
                    "models": [
                        {
                            "model_id": m.model_id,
                            "label": m.label,
                            "runtimes": list(m.runtimes),
                            "is_current_default": m.is_current_default,
                        }
                        for m in prov.models
                    ],
                }
            )

    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "runtimes": runtimes,
                "providers": providers,
                "current_default": {
                    "runtime": opts.current.runtime,
                    "provider_id": opts.current.provider_id,
                    "model": opts.current.model,
                },
                "hint": (
                    "When proposing an agent, set runtime + model to a compatible "
                    "pair: the model's 'runtimes' must include the chosen runtime. "
                    "No configured providers? The user must add a model channel in "
                    "Settings → Model first."
                ),
            },
            ensure_ascii=False,
        )
    )


LIST_PROJECT_MEMBERS_DESCRIPTION = (
    "List the agents already deployed into THIS project (their project-local "
    "handle + the library agent each references). Project sessions only — "
    "returns an error in a quick chat / agent-only conversation. Call this "
    "before deploying so you don't deploy a duplicate. Read-only."
)

LIST_PROJECT_MEMBERS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_project_members_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore

    user_id = context.user_id
    project_id = await _resolve_project_id(context.session_id, context.user_id)
    if not project_id:
        return _err(
            "list_project_members: this session has no project — members can "
            "only be listed inside a project. In a quick chat, use list_agents "
            "to browse the library."
        )
    async with async_unit_of_work(commit=False) as db:
        members = await ProjectMemberDatastore(db).list_by_project(user_id, project_id)
        ads = AgentDatastore(db)
        items = []
        for m in members:
            name = None
            if m.source_agent_slug:
                src = await ads.get_agent(user_id, m.source_agent_slug)
                name = src.name if src else None
            items.append(
                {
                    "agent_slug": m.agent_slug,
                    "source_agent_slug": m.source_agent_slug,
                    "name": name,
                }
            )
    return ToolResult(
        content=json.dumps(
            {"ok": True, "project_id": project_id, "members": items}, ensure_ascii=False
        )
    )


DEPLOY_AGENT_DESCRIPTION = (
    "Deploy an EXISTING library agent into THIS project (派驻 — a live "
    "reference, not a copy). Use this to reuse an agent that already exists "
    "(found via list_agents) instead of creating a new one with propose_agent. "
    "Project sessions only. Pass the library agent's slug. Idempotent: an "
    "agent already deployed to this project is reported as such, not "
    "duplicated."
)

DEPLOY_AGENT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "agent_slug": {
            "type": "string",
            "description": "Slug of the library agent to deploy (from list_agents).",
        }
    },
    "required": ["agent_slug"],
}


async def _deploy_agent_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    slug = str(args.get("agent_slug") or "").strip()
    if not slug:
        return _err("deploy_agent: 'agent_slug' is required")

    project_id = await _resolve_project_id(context.session_id, context.user_id)
    if not project_id:
        return _err(
            "deploy_agent: this session has no project — an agent can only be "
            "deployed inside a project. In a quick chat, use propose_agent to "
            "create a new agent (no deployment)."
        )

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.service import (
        AgentNotFoundError,
        AgentService,
        MemberAlreadyExistsError,
    )

    user_id = context.user_id
    async with async_unit_of_work() as db:
        svc = AgentService(db)
        try:
            result = await svc.deploy_agent(user_id, project_id, slug)
        except AgentNotFoundError:
            return _err(
                f"deploy_agent: no library agent with slug '{slug}'. Call "
                "list_agents to see valid slugs."
            )
        except MemberAlreadyExistsError:
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "already_deployed": True,
                        "next_step": f"Agent '{slug}' is already deployed to this project.",
                    },
                    ensure_ascii=False,
                )
            )
        member = result["member"]
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "deployed": True,
                "project_id": project_id,
                "agent_slug": member.agent_slug,
                "source_agent_slug": member.source_agent_slug,
                "next_step": "Deployed into the project; it's now an active member.",
            },
            ensure_ascii=False,
        )
    )


async def _resolve_project_id(session_id: str, user_id: str) -> str | None:
    """REAL project id for the calling session, or None.

    A session always carries ``metadata.valuz.project_id``, but a quick chat /
    新对话 binds to an ephemeral ``ProjectRow(kind="chat")`` that is NOT a
    deployable project. So we resolve the id then confirm ``kind == "project"``
    — chat / temp / missing all resolve to None, gating member-listing and
    deploy to real projects only."""
    if not session_id:
        return None
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.sessions import project_index

    # session→project is a host fact (``valuz_project_session``) — no kernel
    # round-trip (DataService design §5). Then confirm ``kind == "project"``.
    project_id = await project_index.project_of(session_id)
    if not project_id:
        return None
    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    return project_id if (row is not None and row.kind == "project") else None


def _err(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


def build_agent_proposal_tool_defs() -> tuple[ToolDef, ...]:
    """Return the agent-creation toolset for the host toolkit MCP:
    propose_agent (create new, with confirm card) + update_agent (edit an
    existing one in place) + the discovery/reuse tools list_skills /
    list_agents / list_project_members / deploy_agent."""
    return (
        ToolDef(
            name=PROPOSE_AGENT_TOOL_NAME,
            description=PROPOSE_AGENT_DESCRIPTION,
            parameters=PROPOSE_AGENT_PARAMETERS,
            handler=_propose_agent_handler,
            read_only=False,
        ),
        ToolDef(
            name=UPDATE_AGENT_TOOL_NAME,
            description=UPDATE_AGENT_DESCRIPTION,
            parameters=UPDATE_AGENT_PARAMETERS,
            handler=_update_agent_handler,
            read_only=False,
        ),
        ToolDef(
            name=LIST_SKILLS_TOOL_NAME,
            description=LIST_SKILLS_DESCRIPTION,
            parameters=LIST_SKILLS_PARAMETERS,
            handler=_list_skills_handler,
            read_only=True,
        ),
        ToolDef(
            name=LIST_AGENTS_TOOL_NAME,
            description=LIST_AGENTS_DESCRIPTION,
            parameters=LIST_AGENTS_PARAMETERS,
            handler=_list_agents_handler,
            read_only=True,
        ),
        ToolDef(
            name=LIST_MODEL_OPTIONS_TOOL_NAME,
            description=LIST_MODEL_OPTIONS_DESCRIPTION,
            parameters=LIST_MODEL_OPTIONS_PARAMETERS,
            handler=_list_model_options_handler,
            read_only=True,
        ),
        ToolDef(
            name=LIST_PROJECT_MEMBERS_TOOL_NAME,
            description=LIST_PROJECT_MEMBERS_DESCRIPTION,
            parameters=LIST_PROJECT_MEMBERS_PARAMETERS,
            handler=_list_project_members_handler,
            read_only=True,
        ),
        ToolDef(
            name=DEPLOY_AGENT_TOOL_NAME,
            description=DEPLOY_AGENT_DESCRIPTION,
            parameters=DEPLOY_AGENT_PARAMETERS,
            handler=_deploy_agent_handler,
            read_only=False,
        ),
    )


__all__ = [
    "PROPOSE_AGENT_TOOL_NAME",
    "UPDATE_AGENT_TOOL_NAME",
    "LIST_SKILLS_TOOL_NAME",
    "LIST_AGENTS_TOOL_NAME",
    "LIST_MODEL_OPTIONS_TOOL_NAME",
    "LIST_PROJECT_MEMBERS_TOOL_NAME",
    "DEPLOY_AGENT_TOOL_NAME",
    "build_agent_proposal_tool_defs",
]
