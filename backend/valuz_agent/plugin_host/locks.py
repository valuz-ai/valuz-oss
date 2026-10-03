"""Toggle safety: which plugins a user may not switch off.

A plugin is **locked** when switching it off would stop the process from booting
or would break a plugin that must run:

* it is ``required``; or
* a locked plugin ``needs`` a service that only it can still provide.

The second rule is what makes the OSS features safe to make optional: the host
refuses to start when a *required* plugin's need has no active provider, so a
required commercial plugin that needs ``oss.knowledge`` locks ``oss-knowledge``
(and, transitively, whatever ``oss-knowledge`` needs).

"Only it can still provide" is evaluated against the user's requested ``disabled``
set: with two optional providers of one need either may be switched off, but not
both -- once one is off the other is locked. A need that no plugin provides is
satisfied by the base (an ``ext`` port) and locks nothing, matching
``PluginHost.order``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from valuz_agent.plugin_host.plugin import BackendPlugin


def compute_locks(
    plugins: Mapping[str, BackendPlugin], disabled: Collection[str] = ()
) -> dict[str, list[str]]:
    """``locked plugin id -> sorted ids of locked plugins that need it``.

    A plugin that is locked only because it is required maps to ``[]`` unless some
    locked plugin also needs what it provides. Ids absent from the result are free
    to toggle.
    """
    providers: dict[str, list[str]] = {}
    for pid, plugin in plugins.items():
        for name in plugin.provides:
            providers.setdefault(name, []).append(pid)

    locked: set[str] = {pid for pid, p in plugins.items() if getattr(p, "required", False)}
    changed = True
    while changed:
        changed = False
        for lid in sorted(locked):
            for need in plugins[lid].needs:
                makers = [m for m in providers.get(need, []) if m != lid]
                if not makers or any(m in locked for m in makers):
                    continue
                alive = [m for m in makers if m not in disabled]
                if len(alive) == 1:
                    pinned = alive  # the only thing still holding the need up
                elif not alive:
                    pinned = makers  # every provider was switched off: keep them all on
                else:
                    pinned = []  # several remain; any one may still be switched off
                for maker in pinned:
                    if maker not in locked:
                        locked.add(maker)
                        changed = True

    required_by: dict[str, list[str]] = {pid: [] for pid in locked}
    for lid in locked:
        for need in plugins[lid].needs:
            for maker in providers.get(need, []):
                if maker != lid and maker in locked and lid not in required_by[maker]:
                    required_by[maker].append(lid)
    return {pid: sorted(ids) for pid, ids in required_by.items()}
