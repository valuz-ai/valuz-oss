"""Launch resolution, the managed dsh home, and per-session patches.

Valuz runs the **upstream dsh distribution as published** (``@deepseek-ai/dsh``
— launcher, app-boot, plugin manager, every shipped bundle) plus its own layer
as an ordinary dsh bundle (``valuz-dsh-bundle``). There is no hand-picked
plugin closure any more: a kernel session boots the managed profile ``valuz``
with ``dsh --profile valuz --patch <session.json>`` in the *session* role
(``VALUZ_DSH_ROLE=session``), so everything a user installs into that profile
the dsh way (``dsh plugin --profile valuz add …``, the Plugins page of the
resident manager host, the ``plugin_manager`` agent tool) is live in Valuz
sessions exactly as it is in dsh.

The managed home (``VALUZ_DSH_HOME``) is Valuz-owned and separate from the
user's own ``~/.dsh``. Its ``valuz`` profile lists, in order::

    @deepseek-ai/dsh-base · @deepseek-ai/dsh-web-app · @deepseek-ai/dsh-sdk-app
    · valuz-dsh-bundle · <bundles the user installed>

— the same composition the dsh desktop app boots (base + web), plus the SDK
app so one profile serves both process roles (the Valuz bundle switches the
web rows off in session children and the SDK rows off in the manager).

The per-session patch carries only what is genuinely per session — model
endpoint, MCP servers, the kernel toolkit, and the kernel bridge (Valuz
instructions, plan state, user questions, tool approvals). It never replaces
an upstream row's ``config`` (patches replace ``config`` wholesale, which
would silently drop upstream defaults on the next dsh release); it only
inserts Valuz rows and sets ``llm-deepseek``'s ``baseURL``, a row upstream
ships without config.

Launch resolution (mirrored by ``availability.probe_runtime_availability``):

1. ``VALUZ_DSH_RUNTIME_BIN`` — an executable dsh-compatible launcher (tests,
   or a launcher whose installation also resolves ``valuz-dsh-bundle``).
2. ``VALUZ_DSH_RUNTIME_ENTRY`` — the launcher (``valuz-dsh-bundle/bin/dsh.mjs``)
   of an installed runtime closure, run on Node. The packaged desktop's sidecar
   points this at the staged ``libexec/dsh-runtime`` tree and supplies
   ``VALUZ_NODE_PATH`` (+ ``VALUZ_NODE_IS_ELECTRON=1`` → the spawn gets
   ``ELECTRON_RUN_AS_NODE=1``).
3. Vendored closure auto-detect — ``backend/vendor/dsh-runtime`` after
   ``npm ci`` (dev checkouts; refresh with ``scripts/vendor-dsh-runtime.sh``).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    Session,
)
from src.runtimes.mcp_env import resolve_stdio_env

DSH_RUNTIME_BIN_ENV = "VALUZ_DSH_RUNTIME_BIN"
DSH_RUNTIME_ENTRY_ENV = "VALUZ_DSH_RUNTIME_ENTRY"
DSH_HOME_ENV = "VALUZ_DSH_HOME"
# Node resolution for JS-entry carriers — the same env contract the host's
# browser engine uses (packaged desktop: the sidecar sets VALUZ_NODE_PATH to
# its own Electron binary + VALUZ_NODE_IS_ELECTRON=1).
NODE_PATH_ENV = "VALUZ_NODE_PATH"
NODE_IS_ELECTRON_ENV = "VALUZ_NODE_IS_ELECTRON"

#: The launcher inside an installed closure: Valuz's thin wrapper over the
#: upstream ``runCli`` (bundled pnpm + managed-profile init), shipped in
#: valuz-dsh-bundle.
_DSH_BIN_REL = Path("valuz-dsh-bundle") / "bin" / "dsh.mjs"
# Dev-checkout vendored closure (backend/vendor/dsh-runtime after `npm ci`).
_VENDOR_DIR = Path(__file__).resolve().parents[4] / "vendor" / "dsh-runtime"

#: The profile every Valuz process boots. Users install into it the dsh way.
PROFILE_NAME = "valuz"
#: The Valuz layer's package name (resolved from the installation anchor).
VALUZ_BUNDLE = "valuz-dsh-bundle"
#: Asks the launcher to initialize/repair the named managed profile.
MANAGED_PROFILE_ENV = "VALUZ_DSH_MANAGED_PROFILE"
#: The host's always-on MCP server fronting dsh plugin tools for non-dsh runtimes.
DSH_PLUGINS_MCP_SERVER = "valuz-dsh-plugins"
#: The kernel bridge row valuz-dsh-bundle declares; sessions override its config.
KERNEL_BRIDGE_ROW = "valuz-kernel-bridge"
#: Process-role switch read by the Valuz bundle's patch.
ROLE_ENV = "VALUZ_DSH_ROLE"
SESSION_ROLE = "session"
MANAGER_ROLE = "manager"



@dataclass(frozen=True)
class DshLaunchSpec:
    #: The launcher argv; the runtime appends ``--profile`` / ``--patch``.
    argv: tuple[str, ...]
    cwd: str | None = None
    # Extra environment for the subprocess (e.g. ELECTRON_RUN_AS_NODE=1 when
    # the Node carrier is the desktop's own Electron binary).
    env: dict[str, str] = field(default_factory=dict)
    # The upstream distribution always ships plan mode, user questions and
    # ask_user_question (base + web presets), and the Valuz bundle always
    # ships the kernel bridge — so every real launcher is plan-capable. Test
    # carriers that cannot boot the bridge set this False.
    plan_capable: bool = True


def _resolve_node() -> tuple[str, dict[str, str]] | None:
    """The Node executable for JS-entry carriers, plus its extra env.

    ``VALUZ_NODE_PATH`` wins (the packaged desktop's sidecar points it at the
    app's own Electron binary and flags ``VALUZ_NODE_IS_ELECTRON=1``, which
    requires ``ELECTRON_RUN_AS_NODE=1`` on the spawn — without it Electron
    opens as a second GUI instance); otherwise a plain ``node`` from PATH.
    """
    override = os.environ.get(NODE_PATH_ENV, "").strip()
    if override and os.path.isfile(override):
        extra = (
            {"ELECTRON_RUN_AS_NODE": "1"}
            if os.environ.get(NODE_IS_ELECTRON_ENV, "").strip() == "1"
            else {}
        )
        return override, extra
    node = shutil.which("node")
    if node is not None:
        return node, {}
    return None


def dsh_entry() -> Path | None:
    """The ``dsh`` launcher (``bin.js``) of an installed runtime closure, or None.

    ``VALUZ_DSH_RUNTIME_ENTRY`` (staged libexec tree in the packaged desktop)
    wins over the dev checkout's ``backend/vendor/dsh-runtime`` auto-detect.
    """
    entry_override = os.environ.get(DSH_RUNTIME_ENTRY_ENV, "").strip()
    if entry_override:
        path = Path(entry_override)
        return path if path.is_file() else None
    vendored = _VENDOR_DIR / "node_modules" / _DSH_BIN_REL
    return vendored if vendored.is_file() else None


def resolve_launch() -> DshLaunchSpec | None:
    """Resolve how to spawn the dsh launcher on this machine, or None."""
    bin_override = os.environ.get(DSH_RUNTIME_BIN_ENV, "").strip()
    if bin_override:
        if not (shutil.which(bin_override) or os.path.isfile(bin_override)):
            return None
        return DshLaunchSpec(argv=(bin_override,))
    entry = dsh_entry()
    if entry is not None:
        node = _resolve_node()
        if node is not None:
            node_bin, extra_env = node
            return DshLaunchSpec(argv=(node_bin, str(entry)), env=extra_env)
    return None


def launch_unavailable_reason() -> str | None:
    """Human-readable availability diagnosis, None when launchable."""
    bin_override = os.environ.get(DSH_RUNTIME_BIN_ENV, "").strip()
    if bin_override:
        if shutil.which(bin_override) or os.path.isfile(bin_override):
            return None
        return f"{DSH_RUNTIME_BIN_ENV}={bin_override!r} is not executable"
    if dsh_entry() is not None:
        if _resolve_node() is not None:
            return None
        return "node (>= 22.19) not found for the installed dsh runtime closure"
    return (
        "install the vendored dsh runtime (scripts/vendor-dsh-runtime.sh), or set "
        f"{DSH_RUNTIME_ENTRY_ENV} to an installed closure's "
        "node_modules/valuz-dsh-bundle/bin/dsh.mjs"
    )


# -- managed home ----------------------------------------------------------------


def resolve_dsh_home(state_dir: str | os.PathLike[str] | None = None) -> Path:
    """The Valuz-managed ``DSH_HOME``.

    ``VALUZ_DSH_HOME`` wins (the host sets it next to kernel.db; the desktop
    sidecar to the app data dir). Otherwise ``dsh-home`` beside the transcript
    state dir, so a bare kernel never writes into the user's ``~/.dsh``. The
    profile inside it is initialized by the launcher (``managed-profile.json``
    in valuz-dsh-bundle is the one definition of its bundle layers).
    """
    override = os.environ.get(DSH_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    base = Path(state_dir).expanduser().resolve() if state_dir else Path.cwd() / "dsh_state"
    return base.parent / "dsh-home"


def process_env(
    *,
    home: Path,
    role: str,
    permission_mode: str | None = None,
) -> dict[str, str]:
    """Environment every Valuz dsh process gets, on top of the caller's."""
    env = {
        "DSH_HOME": str(home),
        # The launcher initializes/repairs this profile before boot.
        MANAGED_PROFILE_ENV: PROFILE_NAME,
        ROLE_ENV: role,
        # Any non-empty value opts out of OTel session telemetry (app-boot).
        "DSH_TELEMETRY_DISABLED": "1",
    }
    if permission_mode is not None:
        env["DSH_PERMISSION_MODE"] = dsh_permission_mode(permission_mode)
    return env


def dsh_permission_mode(permission_mode: str) -> str:
    """Kernel ``permission_mode`` → dsh's permission preset.

    ``full_access`` keeps the long-standing unattended behavior (dsh
    ``danger-full-access``: no sandbox, approvals ``never``). ``default`` runs
    dsh's own ``workspace-write`` preset — writes confined to the workspace,
    anything else asks, and the kernel bridge parks each ask as a Valuz
    approval card.
    """
    return "danger-full-access" if permission_mode == "full_access" else "workspace-write"


# ``EffortLevel`` → the dsh DeepSeek adapter's ``reasoningEffort`` ids
# (``off | low | high | max`` since dsh 0.2; ``medium`` has no spelling and
# rounds up, ``xhigh`` maps to ``max``). Passed through SDK ``initialize``.
_EFFORT_MAP = {"low": "low", "medium": "high", "high": "high", "xhigh": "max", "max": "max"}


def dsh_reasoning_effort(effort: str | None) -> str | None:
    return _EFFORT_MAP.get(effort) if effort else None


# -- per-session patch -----------------------------------------------------------

# The kernel's ``/mcp/toolkit/{session_id}`` bridge — kernel-owned ToolDefs
# (e.g. PTC's execute_code). The env name keeps the legacy codex spelling:
# the sandbox provisioner already exports it (sandbox_seatbelt), so dsh
# inherits the correct callback base in every deployment for free.
KERNEL_TOOLKIT_BASE_URL_ENV = "CODEX_TOOLKIT_BASE_URL"
KERNEL_TOOLKIT_BASE_URL_DEFAULT = "http://127.0.0.1:8000"
KERNEL_TOOLKIT_SERVER_NAME = "harness_toolkit"


def kernel_toolkit_url(session_id: str) -> str:
    base = (
        os.environ.get(KERNEL_TOOLKIT_BASE_URL_ENV, "").strip() or KERNEL_TOOLKIT_BASE_URL_DEFAULT
    )
    return f"{base.rstrip('/')}/mcp/toolkit/{session_id}"


# Base of the kernel's dsh user-questions route as reachable from a local
# subprocess. Same convention as ``VALUZ_PTC_ENDPOINT``: the default
# matches the in-process desktop backend and includes the frozen route
# prefix (``src`` must not import ``app.routes.KERNEL_API_PREFIX``).
USER_QUESTIONS_ENDPOINT_ENV = "VALUZ_DSH_UQ_ENDPOINT"
USER_QUESTIONS_ENDPOINT_DEFAULT = "http://127.0.0.1:8000/kernel/v1/dsh/user-questions"


def user_questions_endpoint(token: str) -> str:
    base = (
        os.environ.get(USER_QUESTIONS_ENDPOINT_ENV, "").strip() or USER_QUESTIONS_ENDPOINT_DEFAULT
    )
    return f"{base.rstrip('/')}/{token}"


def build_session_patch(
    session: Session,
    *,
    model_base_url: str | None = None,
    kernel_toolkit: bool = False,
    plan_capable: bool = True,
    user_questions_url: str | None = None,
) -> list[dict[str, Any]]:
    """The ``--patch`` layer for one kernel session (pure; unit-testable).

    Upstream rows are only toggled or given config they ship without; Valuz
    behavior arrives as inserted rows. Tools, persona, skills discovery
    (``<cwd>/.agents/skills``, where the kernel materializes session skills)
    and plan mode come from the profile's agent preset, as in dsh itself.
    """
    patch: list[dict[str, Any]] = []
    if model_base_url:
        # ``llm-deepseek`` ships with no config; its baseURL wins over the
        # trusted-env fallback and the public endpoint.
        patch.append({"id": "llm-deepseek", "config": {"baseURL": model_base_url}})

    bridge_config: dict[str, Any] = {}
    if session.instructions.strip():
        bridge_config["instructions"] = session.instructions
    if plan_capable:
        bridge_config["planActive"] = session.mode == "plan"
        if user_questions_url:
            bridge_config["userQuestionsEndpoint"] = user_questions_url
    if bridge_config:
        # The bridge row is declared by valuz-dsh-bundle (session role only);
        # overriding a Valuz-owned row's config never drifts from upstream.
        patch.append({"id": KERNEL_BRIDGE_ROW, "config": bridge_config})

    inserted: list[dict[str, Any]] = []
    inserted.extend(_mcp_rows(session))
    if kernel_toolkit:
        # Kernel ToolDefs (registered by the runtime in mcp_bridge) surface
        # like any other MCP server: ``mcp__harness_toolkit__<tool>``.
        inserted.append(
            {
                "id": "kernel-toolkit",
                "name": "@deepseek-ai/dsh-mcp-client",
                "config": {
                    "serverName": KERNEL_TOOLKIT_SERVER_NAME,
                    "transport": "streamable-http",
                    "url": kernel_toolkit_url(session.id),
                },
            }
        )
    if inserted:
        patch.append({"insert": inserted})
    return patch


def write_session_patch(session: Session, **kwargs: Any) -> str:
    """Write this session's patch file; returns its path.

    JSON — a JSON document is valid YAML, which sidesteps quoting/injection
    concerns for instruction text and MCP header values. The caller owns
    cleanup (``cleanup_session_patch``).
    """
    config_dir = Path(tempfile.gettempdir()) / f"valuz-dsh-{session.id}-{uuid.uuid4().hex[:8]}"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "session.patch.json"
    patch = build_session_patch(session, **kwargs)
    path.write_text(json.dumps(patch, ensure_ascii=False, indent=1))
    path.chmod(0o600)
    return str(path)


def cleanup_session_patch(patch_path: str | None) -> None:
    if not patch_path:
        return
    shutil.rmtree(Path(patch_path).parent, ignore_errors=True)


def _mcp_rows(session: Session) -> list[dict[str, Any]]:
    """One ``dsh-mcp-client`` row per session MCP server.

    dsh registers each server's tools as ``mcp__<serverName>__<tool>`` — the
    same shape the other runtimes consume.
    """
    rows: list[dict[str, Any]] = []
    for index, server in enumerate(session.mcp_servers):
        if server.name == DSH_PLUGINS_MCP_SERVER:
            # dsh plugin tools reach other runtimes through this server; a dsh
            # session loads the same plugins natively from the profile.
            continue
        if isinstance(server, McpHttpServerConfig):
            config: dict[str, Any] = {
                "serverName": _server_name(server.name, index),
                "transport": "streamable-http",
                "url": server.url,
            }
            if server.headers:
                config["headers"] = dict(server.headers)
            rows.append(
                {"id": f"mcp-{index}", "name": "@deepseek-ai/dsh-mcp-client", "config": config}
            )
        elif isinstance(server, McpStdioServerConfig):
            config = {
                "serverName": _server_name(server.name, index),
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
            }
            env = resolve_stdio_env(server)
            if env is not None:
                config["env"] = env
            rows.append(
                {"id": f"mcp-{index}", "name": "@deepseek-ai/dsh-mcp-client", "config": config}
            )
    return rows


def _server_name(name: str, index: int) -> str:
    """dsh requires ``[A-Za-z0-9_-]{1,32}`` server names, unique per instance."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)[:32]
    return cleaned or f"server_{index}"
