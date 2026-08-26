"""Authoritative CLI-subscription login probe (host-side).

The Claude Pro·Max / Codex·ChatGPT channels authenticate out-of-band through
their CLI's own keychain (``claude /login`` / ``codex /login``). Only the CLIs
themselves know whether that auth is actually usable, so — exactly like the
desktop's ``cli_login`` IPC (``apps/desktop/.../ipc/cli-login.ts``) — we shell
out to the CLI's status command and parse its authoritative answer rather than
sniffing credential files (which read as "logged in" for stale or
half-provisioned tokens).

**Why the host probes too** (the desktop already does): the headless / WebUI
host has no Electron layer, so the server is the ONLY place that can gate the
model picker on login state there. In the desktop the server is co-located with
the keychain as well, so the same probe is valid. A cloud-deployed host can't
reach any user's keychain — but subscription login is disabled there
(``settings.subscription_login_enabled`` off), so this never runs.

Results are cached per tool with a short TTL: ``list_providers`` /
``get_provider`` are hot and a subprocess spawn per call would be wasteful.
``invalidate`` is called right after a login is materialized so the next list
reflects it without waiting out the TTL.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Literal

logger = logging.getLogger(__name__)

CliTool = Literal["claude", "codex"]

# Mirror the desktop's strict markers so host and desktop agree on what
# "logged in" means (see ``cli-login.ts``).
#   claude — ``claude auth status`` prints JSON; require BOTH markers.
#   codex  — ``codex login status`` prints "Logged in using <method>" for ANY
#            auth method (ChatGPT / API key / access token), "Not logged in"
#            otherwise — so the shared prefix is the marker (can't match the
#            negative line).
_CLAUDE_MARKERS: tuple[str, ...] = ('"loggedIn": true,', '"authMethod": "claude.ai",')
_CODEX_MARKER = "Logged in using"

_STATUS_ARGS: dict[str, list[str]] = {
    "claude": ["auth", "status"],
    "codex": ["login", "status"],
}

# A hung CLI must never stall a provider-list request.
_PROBE_TIMEOUT_S = 8.0
# Login state changes only through explicit login/logout flows, and the
# app-internal login path invalidates this cache directly
# (``_invalidate_login_cache``), so the TTL is just a backstop for changes
# made OUTSIDE the app (e.g. ``claude logout`` in a terminal). Keep it long:
# each expiry makes the next gated provider fetch pay a CLI subprocess spawn
# (seconds — Node CLI cold start), which used to stall every composer open
# when this was 10s. A stale "logged in" during the window is acceptable —
# picking such a model fails loudly at session creation.
_CACHE_TTL_S = 600.0

_cache: dict[str, tuple[float, bool]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(tool: str) -> asyncio.Lock:
    lock = _locks.get(tool)
    if lock is None:
        lock = asyncio.Lock()
        _locks[tool] = lock
    return lock


def invalidate(tool: CliTool | None = None) -> None:
    """Drop cached login state for ``tool`` (or all tools when ``None``).

    Called after a login is materialized (``enable_provider``) so the next
    provider list re-probes instead of serving a stale logged-out result.
    """
    if tool is None:
        _cache.clear()
    else:
        _cache.pop(tool, None)


def _resolve_binary(tool: CliTool) -> str | None:
    """Locate the CLI binary: global install first (matches the desktop probe),
    the SDK-bundled binary otherwise — the same one the runtime will launch."""
    found = shutil.which(tool)
    if found:
        return found
    try:
        if tool == "codex":
            from codex_cli_bin import bundled_codex_path

            path = bundled_codex_path()
            return str(path) if path else None
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("claude_agent_sdk")
        if spec is not None and spec.submodule_search_locations:
            cand = Path(next(iter(spec.submodule_search_locations))) / "_bundled" / "claude"
            return str(cand) if cand.exists() else None
    except Exception as exc:  # noqa: BLE001 — best-effort discovery, never fatal
        logger.debug("bundled %s binary lookup failed: %s", tool, exc)
    return None


async def _probe(tool: CliTool) -> bool:
    binary = _resolve_binary(tool)
    if not binary:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *_STATUS_ARGS[tool],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            return False
    except (OSError, ValueError) as exc:
        logger.debug("cli login probe for %s failed: %s", tool, exc)
        return False
    # Some CLIs print status on stderr — search both streams.
    out = f"{out_b.decode(errors='replace')}\n{err_b.decode(errors='replace')}"
    if tool == "claude":
        return all(m in out for m in _CLAUDE_MARKERS)
    return _CODEX_MARKER in out


async def detect_cli_login(tool: CliTool) -> bool:
    """Whether ``tool`` (``claude`` / ``codex``) is logged in. Cached per tool.

    Any error / timeout / missing binary degrades to ``False`` (treated as
    logged out) — the conservative answer: hide models we can't prove are
    runnable rather than offer a pick that 422s at session creation.
    """
    now = time.monotonic()
    hit = _cache.get(tool)
    if hit is not None and hit[0] > now:
        return hit[1]
    async with _lock_for(tool):
        # Re-check under the lock — a concurrent caller may have just filled it.
        hit = _cache.get(tool)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        ok = await _probe(tool)
        _cache[tool] = (time.monotonic() + _CACHE_TTL_S, ok)
        return ok


__all__ = ["CliTool", "detect_cli_login", "invalidate"]
