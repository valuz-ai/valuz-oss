"""Per-runtime launchability probe.

The kernel is the process that actually launches runtimes and owns their
binaries, so it is the source of truth for "can this runtime run here". Hosts
read it through ``KernelClient.runtime_availability`` so the answer reflects
wherever the kernel runs — the local process (bundled desktop) or a cloud
sandbox — not the API host's PATH. See
``docs/design/runtime-model-compat-single-source.md`` §3.3.
"""

from __future__ import annotations

import os
import shutil


def _bundled_codex_path() -> str | None:
    """Path to the codex binary vendored by ``codex_cli_bin``, or ``None`` when
    the package is absent or its binary is missing. Split out so it's patchable
    and so a host without the package degrades gracefully."""
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError:
        return None
    try:
        path = bundled_codex_path()
    except FileNotFoundError:
        return None
    return str(path) if path and path.exists() else None


def _codex_available() -> tuple[bool, str | None]:
    """Resolve the codex binary in the same order the runtime launches with:
    ``CODEX_BIN_OVERRIDE`` → bundled ``codex_cli_bin`` → PATH. Mirrors
    ``src.runtimes.codex.runtime._resolve_codex_bin`` but imports only the
    lightweight binary package (never the ``openai-codex`` SDK), so the probe
    is robust wherever it runs."""
    override = os.environ.get("CODEX_BIN_OVERRIDE", "").strip()
    if override:
        if shutil.which(override) or os.path.isfile(override):
            return True, None
        return False, f"CODEX_BIN_OVERRIDE={override!r} is not executable"
    if _bundled_codex_path():
        return True, None
    if shutil.which("codex"):
        return True, None
    return False, "codex binary not found; install it first"


def probe_runtime_availability() -> dict[str, dict[str, object]]:
    """Return ``{runtime_id: {"available": bool, "unavailable_reason": str|None}}``.

    ``claude_agent`` / ``deepagents`` are pure-Python SDKs → always available.
    ``codex`` needs its binary (see ``_codex_available``); ``deepseek_harness``
    needs a launchable dsh runtime (exe or source checkout — see
    ``src.runtimes.deepseek_harness.composition``).
    """
    from src.runtimes.deepseek_harness.composition import launch_unavailable_reason

    codex_ok, codex_reason = _codex_available()
    dsh_reason = launch_unavailable_reason()
    return {
        "claude_agent": {"available": True, "unavailable_reason": None},
        "deepagents": {"available": True, "unavailable_reason": None},
        "codex": {"available": codex_ok, "unavailable_reason": codex_reason},
        "deepseek_harness": {
            "available": dsh_reason is None,
            "unavailable_reason": dsh_reason,
        },
    }
