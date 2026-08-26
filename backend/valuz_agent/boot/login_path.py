"""Login-shell PATH enrichment for GUI-launched backends.

A backend launched from Finder / the Dock / a launchd autostart plist never
goes through the user's shell, so it inherits launchd's minimal environment
(``PATH=/usr/bin:/bin:/usr/sbin:/sbin``) instead of the PATH the shell's init
files build (``/etc/zprofile`` → ``path_helper``, ``~/.zprofile`` — Homebrew /
uv / cargo, ``~/.zshrc`` — nvm). Everything in this process that resolves
executables by name is blinded by that: stdio MCP connectors (``npx -y …``,
``uvx``, the ``uv run``-based bundled finance servers), the CLI login probe,
and the browser engine's dev ``npx`` fallback.

This step reproduces the shell's PATH once at boot — the fix-path / shell-env
pattern: run ``$SHELL -i -l -c`` (``-l`` sources the profile chain, ``-i`` the
rc chain; passed as separate flags for shells that don't group short options)
printing ``$PATH`` between unique delimiters so dotfile noise (banners, motd)
can't corrupt the extraction, then APPEND the entries we don't already have.
Append-only keeps system directories' precedence — ``which()`` only needs
presence, and a login-shell entry must never shadow a binary the app relies on.

Fail-open by design: no shell, a hung dotfile (timeout), or garbled output
just leaves PATH untouched. ``VALUZ_DISABLE_LOGIN_PATH=1`` opts out entirely.
Windows is skipped — GUI processes inherit the user PATH there. A split
kernel (``VALUZ_KERNEL_MODE=http``) spawns stdio children in ITS own process;
this enriches only the host (which is also the in-process-kernel default).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Unique delimiters around the PATH value make the extraction immune to
# anything the user's dotfiles print before or after it.
_DELIM = "__VALUZ_LOGIN_PATH__"
# ``printenv`` is an external binary, so the same command works in zsh, bash
# and fish alike (fish would word-split a quoted ``$PATH`` expansion).
_PRINT_PATH_CMD = f'echo "{_DELIM}"; printenv PATH; echo "{_DELIM}"'
# A hung dotfile (prompted input, network call) must never stall boot.
_TIMEOUT_S = 5.0


def _default_shell() -> str:
    shell = (os.environ.get("SHELL") or "").strip()
    if shell:
        return shell
    return "/bin/zsh" if sys.platform == "darwin" else "/bin/bash"


async def _login_shell_path(*, timeout_s: float = _TIMEOUT_S) -> str | None:
    """The PATH the user's interactive login shell builds, or ``None``."""
    shell = _default_shell()
    try:
        proc = await asyncio.create_subprocess_exec(
            shell,
            "-i",
            "-l",
            "-c",
            _PRINT_PATH_CMD,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            logger.info("login-shell PATH probe timed out (%s) — skipped", shell)
            return None
    except OSError as exc:
        logger.info("login-shell PATH probe unavailable (%s): %s", shell, exc)
        return None
    out = out_b.decode(errors="replace")
    start = out.find(_DELIM)
    end = out.rfind(_DELIM)
    if start == -1 or end <= start:
        logger.info("login-shell PATH probe produced no delimited output (%s)", shell)
        return None
    value = out[start + len(_DELIM) : end].strip()
    return value or None


def merge_paths(current: str, login: str) -> str:
    """Append ``login`` entries missing from ``current`` (order-preserving)."""
    entries = [e for e in current.split(os.pathsep) if e]
    seen = set(entries)
    for entry in login.split(os.pathsep):
        if entry and entry not in seen:
            entries.append(entry)
            seen.add(entry)
    return os.pathsep.join(entries)


async def enrich_login_shell_path(*, timeout_s: float = _TIMEOUT_S) -> None:
    """Merge the user's login-shell PATH into ``os.environ["PATH"]``.

    Must run before anything that resolves executables or snapshots the
    environment for a child process (kernel init, tool registration, the
    browser CLI wrapper — which prepends its own PATH entry independently).
    """
    if sys.platform == "win32":
        return
    if os.environ.get("VALUZ_DISABLE_LOGIN_PATH") == "1":
        logger.info("login-shell PATH enrichment disabled (VALUZ_DISABLE_LOGIN_PATH=1)")
        return
    login = await _login_shell_path(timeout_s=timeout_s)
    if not login:
        return
    current = os.environ.get("PATH", "")
    merged = merge_paths(current, login)
    if merged == current:
        logger.debug("login-shell PATH adds no new entries")
        return
    added = len(merged.split(os.pathsep)) - len([e for e in current.split(os.pathsep) if e])
    os.environ["PATH"] = merged
    logger.info("PATH enriched from login shell (+%d entries)", added)
