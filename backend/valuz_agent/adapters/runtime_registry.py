"""Runtime Agent metadata + availability checks.

The kernel exposes three runtimes via ``Session.runtime_provider``:
``claude_agent`` / ``codex`` / ``deepagents``. Valuz lets the user pick
which one drives a new session at creation time. This module is the
single source of truth for:

* the human-readable display name shown in the picker;
* which API protocols a runtime can dispatch (used to filter compatible
  channels for the model dropdown);
* whether the runtime is *currently* runnable on the host (codex needs a
  binary on PATH; the other two are pure Python deps and always available
  once ``backend/pyproject.toml`` resolves).

Design notes
------------
* This file does **not** own the model catalogue. Models live on the
  channel — see ``ProviderRow.model_ids`` and
  ``BUILTIN_PROVIDERS[*].model_options``. Runtime selection narrows the
  pool of providers the user can pick from; the provider itself supplies
  the model ids.
* ``ApiProtocol`` here is the valuz user-facing hyphen form
  (``anthropic`` / ``openai-completion`` / ``openai-response`` /
  ``gemini``). The kernel uses underscored equivalents
  (``openai_completion`` / ``openai_response``); ``provider_resolver``
  bridges between the two.
* Mirrors ``src.runtimes.factory.ALLOWED_PROTOCOLS_BY_RUNTIME``. When the
  kernel adds a runtime/protocol, update both this map and the
  frontend's ``runtime-protocols.ts`` in lock-step.
* Adding a new runtime is a 2-step change: (1) extend the kernel's
  factory + provider enum, (2) add an entry here. Anything beyond this
  module is downstream — the API + UI read from here.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Literal

ApiProtocol = Literal["anthropic", "openai-completion", "openai-response", "gemini"]
RuntimeId = Literal["claude_agent", "codex", "deepagents"]


@dataclass(frozen=True)
class RuntimeSpec:
    id: RuntimeId
    display_name: str
    supported_protocols: tuple[ApiProtocol, ...]
    # Path-resolvable binary the runtime invokes as a subprocess. ``None``
    # for runtimes that are pure Python (claude-agent-sdk, deepagents).
    requires_binary: str | None
    # Env var that overrides ``requires_binary`` lookup (codex honours
    # ``CODEX_BIN_OVERRIDE`` so a project can pin a non-PATH install).
    binary_env_override: str | None = None


RUNTIME_REGISTRY: dict[str, RuntimeSpec] = {
    "claude_agent": RuntimeSpec(
        id="claude_agent",
        display_name="Claude Code",
        supported_protocols=("anthropic",),
        requires_binary=None,
    ),
    "codex": RuntimeSpec(
        id="codex",
        display_name="OpenAI Codex",
        supported_protocols=("openai-response",),
        requires_binary="codex",
        binary_env_override="CODEX_BIN_OVERRIDE",
    ),
    "deepagents": RuntimeSpec(
        id="deepagents",
        display_name="Deep Agents",
        supported_protocols=("anthropic", "openai-completion", "gemini"),
        requires_binary=None,
    ),
}


def list_runtimes() -> list[RuntimeSpec]:
    """Return every registered runtime. Order is stable for the UI."""
    return list(RUNTIME_REGISTRY.values())


def get_runtime(runtime_id: str) -> RuntimeSpec | None:
    """Look up a runtime by id; ``None`` if the id isn't registered."""
    return RUNTIME_REGISTRY.get(runtime_id)


def _resolve_bundled_binary(runtime_id: str) -> str | None:
    """Resolve a runtime binary that ships inside a Python dependency.

    Only ``codex`` has one: the ``codex_cli_bin`` package (installed by the
    ``openai-codex`` SDK) vendors a per-platform, version-pinned codex binary.
    The kernel codex runtime already prefers it
    (``src.runtimes.codex.runtime._bundled_codex_bin``); we mirror that lookup
    here so the host's availability probe agrees with what the runtime will
    actually launch. Without it, a host that has codex bundled but not on PATH
    reported the runtime as unavailable even though sessions ran fine.

    Returns the absolute path, or ``None`` when the package is absent or its
    binary is missing (the caller then falls back to a PATH lookup).
    """
    if runtime_id != "codex":
        return None
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError:
        return None
    try:
        path = bundled_codex_path()
    except FileNotFoundError:
        return None
    return str(path) if path.exists() else None


def is_runtime_available(runtime_id: str) -> tuple[bool, str | None]:
    """Check whether the runtime can actually run on this host.

    Returns ``(available, unavailable_reason)``. ``unavailable_reason`` is
    ``None`` when ``available`` is ``True``. The wording of the reason is
    user-facing — keep it short and actionable so the UI can show it as a
    hover tooltip without wrapping awkwardly.
    """
    spec = RUNTIME_REGISTRY.get(runtime_id)
    if spec is None:
        return False, f"unknown runtime {runtime_id!r}"

    if spec.requires_binary is None:
        return True, None

    # Honour the env override before falling back to PATH lookup so
    # developers can point at a non-default install (e.g. a fresh build
    # from source) without messing with their shell PATH.
    if spec.binary_env_override:
        override = os.environ.get(spec.binary_env_override, "").strip()
        if override:
            if shutil.which(override) or os.path.isfile(override):
                return True, None
            return False, (
                f"{spec.binary_env_override}={override!r} but the path is not "
                "executable; check the override or unset it to fall back to PATH"
            )

    # Some runtimes ship their binary inside a Python dependency (codex via
    # ``codex_cli_bin``). Prefer that over a PATH lookup, mirroring the kernel
    # runtime's resolution order (override → bundled → PATH) so this probe
    # agrees with what the runtime will actually launch.
    if _resolve_bundled_binary(spec.id) is not None:
        return True, None

    if shutil.which(spec.requires_binary):
        return True, None

    return False, (
        f"{spec.requires_binary!r} binary not found on PATH; install it or "
        f"set {spec.binary_env_override} to a custom location"
        if spec.binary_env_override
        else f"{spec.requires_binary!r} binary not found on PATH; install it first"
    )


def supports_protocol(runtime_id: str, protocol: ApiProtocol) -> bool:
    """Whether ``runtime_id`` can dispatch the given API protocol.

    Used by the API layer to validate that the channel the user picked
    is compatible with the runtime they picked.
    """
    spec = RUNTIME_REGISTRY.get(runtime_id)
    if spec is None:
        return False
    return protocol in spec.supported_protocols


__all__ = [
    "ApiProtocol",
    "RuntimeId",
    "RuntimeSpec",
    "RUNTIME_REGISTRY",
    "list_runtimes",
    "get_runtime",
    "is_runtime_available",
    "supports_protocol",
]
