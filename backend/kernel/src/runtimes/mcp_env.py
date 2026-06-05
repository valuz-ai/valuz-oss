"""Shared MCP stdio env resolution for Claude + DeepAgents runtimes.

Codex resolves ``env_vars`` itself (against the codex process env), so it
never imports this module. The other two SDKs only accept a flat ``env``
dict, so the harness must materialize the passthrough allowlist before
handing the config off — this is the single place that does it.

See ``docs/design/MCP-SERVERS.md`` §5 for the full semantics.
"""

from __future__ import annotations

import logging
import os

from src.core.types import McpStdioServerConfig

logger = logging.getLogger(__name__)

# Parent env keys auto-included alongside any user-supplied ``env`` /
# ``env_vars``. Without these, a non-empty ``env`` dict replaces the
# parent env entirely for the spawned MCP child, breaking common
# scenarios: ``npx`` needs ``HOME`` to find ``~/.npm``; many tools rely
# on ``USER`` / ``LANG`` / ``TMPDIR``.
_IMPLICIT_PARENT_ENV: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TMPDIR")


def resolve_stdio_env(cfg: McpStdioServerConfig) -> dict[str, str] | None:
    """Build the env dict the SDK passes to the stdio child.

    Returns ``None`` when the user supplied no env config — the caller
    must omit the ``env`` key entirely so the SDK / CLI inherits the
    parent process env naturally. This is the common case for
    ``npx``-style MCPs that need a full env (HOME, etc.) without the
    user having to enumerate every implicit dependency.

    When the user *does* supply ``env_vars`` or explicit ``env``,
    resolves both and folds in :data:`_IMPLICIT_PARENT_ENV` so PATH /
    HOME / USER / LANG / TMPDIR survive — once we hand the SDK an env
    dict, that dict replaces the parent env, so the implicit additions
    are what keep the child runnable.

    Merge order: parent env passthrough (``env_vars``) + explicit
    ``env`` (the latter wins on collision because the user typed it
    deliberately) + implicit parent env (only fills in keys not already
    set, never overrides).
    """
    if not cfg.env_vars and not cfg.env:
        return None

    final: dict[str, str] = {}
    for name in cfg.env_vars:
        value = os.environ.get(name)
        if value is None:
            logger.warning(
                "stdio MCP server '%s' references env_vars[%s] but the harness "
                "process env does not have it — skipping",
                cfg.name,
                name,
            )
            continue
        final[name] = value
    final.update(cfg.env)
    for implicit in _IMPLICIT_PARENT_ENV:
        if implicit not in final and (value := os.environ.get(implicit)) is not None:
            final[implicit] = value
    return final
