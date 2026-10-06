"""The resident dsh manager host — plugin management the dsh way.

Valuz runs dsh sessions on one managed profile (``valuz`` under
``VALUZ_DSH_HOME``; see the kernel's ``deepseek_harness.composition``). This
module keeps one long-lived dsh **web host** on that same profile in the
*manager* role (``VALUZ_DSH_ROLE=manager``): the upstream Plugins page, the
``pluginManager`` Remote service (inspect / install / enable / configure /
version exemptions / remove — guided installs with build approvals and
rollback), and HMR, all exactly as the dsh desktop app has them. Whatever is
installed here is live in the next Valuz dsh session, because sessions boot
the same profile.

The host binds loopback only and is authenticated by its launch token; the
token URL is the one thing the UI needs to open the native dsh pages.

Launch inputs mirror the kernel's dsh launch contract (no kernel import — the
host may not reach into ``src.runtimes``):

* ``VALUZ_DSH_RUNTIME_ENTRY`` — the closure launcher
  (``node_modules/valuz-dsh-bundle/bin/dsh.mjs``); else the dev checkout's
  vendored closure;
* ``VALUZ_NODE_PATH`` / ``VALUZ_NODE_IS_ELECTRON`` — the Node carrier;
* ``VALUZ_DSH_HOME`` — the managed home (set at boot next to kernel.db).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from valuz_agent.modules.dsh_plugins.remote import DshRemoteClient

logger = logging.getLogger(__name__)

PROFILE_NAME = "valuz"
_BACKEND_DIR = Path(__file__).resolve().parents[3]
_VENDORED_LAUNCHER = (
    _BACKEND_DIR / "vendor" / "dsh-runtime" / "node_modules" / "valuz-dsh-bundle" / "bin"
) / "dsh.mjs"
_URL_LINE = re.compile(r"dsh web: (https?://\S+)")
_START_TIMEOUT_SECONDS = 90.0

#: pluginManager Remote methods the API proxies (dsh plugin-manager service).
PLUGIN_MANAGER_METHODS = frozenset(
    {
        "listBundles",
        "listPlugins",
        "registries",
        "inspect",
        "installBundle",
        "waitForInstall",
        "cancelInstall",
        "removeBundle",
        "setPluginEnabled",
        "setBundleEnabled",
        "listVersionExemptions",
        "setVersionExemption",
    }
)


class DshManagerUnavailableError(RuntimeError):
    """The manager host cannot run here (disabled, no closure, or no Node)."""


class DshManagedBundleError(RuntimeError):
    """A change that would switch off or remove what Valuz's dsh sessions run on."""


def manager_enabled() -> bool:
    """Whether this deployment may run a dsh manager host.

    Plugin code runs unsandboxed with the user's privileges, so this is a
    local-workstation capability: on by default for a ``local`` deployment
    with a local kernel store; off for ``cloud`` deployments
    (``VALUZ_DEPLOYMENT_TYPE``) and shared/remote kernel stores (``KERNEL_STORE``
    pg/remote). Explicit ``VALUZ_DSH_MANAGER_ENABLED`` (``1``/``0``) wins.
    """
    explicit = os.environ.get("VALUZ_DSH_MANAGER_ENABLED", "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    from valuz_agent.infra.config import settings

    if getattr(settings, "deployment_type", "local") != "local":
        return False
    return os.environ.get("KERNEL_STORE", "local").strip().lower() in {"", "local"}


def resolve_launcher() -> Path | None:
    override = os.environ.get("VALUZ_DSH_RUNTIME_ENTRY", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    return _VENDORED_LAUNCHER if _VENDORED_LAUNCHER.is_file() else None


def _resolve_node() -> tuple[str, dict[str, str]] | None:
    override = os.environ.get("VALUZ_NODE_PATH", "").strip()
    if override and os.path.isfile(override):
        electron = os.environ.get("VALUZ_NODE_IS_ELECTRON", "").strip() == "1"
        return override, ({"ELECTRON_RUN_AS_NODE": "1"} if electron else {})
    node = shutil.which("node")
    return (node, {}) if node else None


def resolve_dsh_home() -> Path:
    override = os.environ.get("VALUZ_DSH_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    data_dir = os.environ.get("VALUZ_DATA_DIR", "").strip()
    base = Path(data_dir).expanduser() if data_dir else Path.home() / ".valuz-oss"
    return base / "dsh-home"


VALUZ_BUNDLE = "valuz-dsh-bundle"

#: Bundles the launcher keeps in the managed profile (valuz-dsh-bundle's
#: managed-profile.json); anything else in the list is a user install.
MANAGED_BUNDLES = frozenset(
    {
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        "@deepseek-ai/dsh-sdk-app",
        "valuz-dsh-bundle",
    }
)

#: Installation-provided bundles that define a process surface of their own
#: (stdio ACP server, one-shot runner, a second minimal SDK). Valuz's session
#: processes already serve SDK JSON-RPC on stdout from the same profile, so
#: switching one of these on would put two protocol servers on one stdout.
#: test_dsh_upstream_compat checks each still ships, so a rename cannot
#: silently drop the guard.
SURFACE_BUNDLES = frozenset(
    {
        "@deepseek-ai/dsh-acp-app",
        "@deepseek-ai/dsh-headless",
        "@deepseek-ai/dsh-sdk-minimal",
    }
)


def has_user_bundles() -> bool:
    """Whether the managed profile selects any bundle a user installed."""
    manifest = resolve_dsh_home() / "profiles" / PROFILE_NAME / "package.json"
    try:
        bundles = json.loads(manifest.read_text(encoding="utf-8"))["dsh"]["profile"]["bundles"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(bundles, list) and any(name not in MANAGED_BUNDLES for name in bundles)


def unavailable_reason() -> str | None:
    if not manager_enabled():
        return "dsh plugin management is disabled in this deployment"
    if resolve_launcher() is None:
        return "the dsh runtime closure is not installed (scripts/vendor-dsh-runtime.sh)"
    if _resolve_node() is None:
        return "node (>= 22.19) not found"
    return None


@dataclass(frozen=True)
class ManagerStatus:
    enabled: bool
    available: bool
    running: bool
    unavailable_reason: str | None
    home: str
    profile: str
    ui_url: str | None
    #: Bundles Valuz keeps in the profile; they cannot be removed or switched off.
    managed_bundles: list[str] = field(default_factory=lambda: sorted(MANAGED_BUNDLES))
    #: Bundles that cannot be switched on in this profile (SURFACE_BUNDLES).
    incompatible_bundles: list[str] = field(default_factory=lambda: sorted(SURFACE_BUNDLES))


class DshManagerHost:
    """One resident dsh web host on the managed profile, started on demand."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._remote: DshRemoteClient | None = None
        self._lock = asyncio.Lock()
        self._drain_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def status(self) -> ManagerStatus:
        reason = unavailable_reason()
        return ManagerStatus(
            enabled=manager_enabled(),
            available=reason is None,
            running=self.running,
            unavailable_reason=reason,
            home=str(resolve_dsh_home()),
            profile=PROFILE_NAME,
            ui_url=self._url if self.running else None,
        )

    async def ensure_started(self) -> str:
        """Start the host if needed; returns its authenticated (token) URL."""
        async with self._lock:
            if self.running and self._url is not None:
                return self._url
            await self._stop_locked()
            reason = unavailable_reason()
            if reason is not None:
                raise DshManagerUnavailableError(reason)
            launcher = resolve_launcher()
            node = _resolve_node()
            assert launcher is not None and node is not None
            node_bin, node_env = node
            home = resolve_dsh_home()
            home.mkdir(parents=True, exist_ok=True)
            env = {
                **os.environ,
                **node_env,
                "DSH_HOME": str(home),
                # Same as the session launch (composition.process_env): skills
                # come from the Valuz library, not the user's ~/.agents/skills.
                "DSH_AGENTS_HOME": str(home / "agents"),
                "VALUZ_DSH_MANAGED_PROFILE": PROFILE_NAME,
                "VALUZ_DSH_ROLE": "manager",
                "DSH_TELEMETRY_DISABLED": "1",
            }
            process = await asyncio.create_subprocess_exec(
                node_bin,
                # Same flag as the kernel's session launch (deepseek_harness
                # composition.NODE_FLAGS): dsh's HMR and plugin loader reach
                # Node's internal loader through it first.
                "--expose-internals",
                str(launcher),
                "--profile",
                PROFILE_NAME,
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--no-open",
                cwd=str(home),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self._process = process
            try:
                url = await asyncio.wait_for(self._read_url(process), _START_TIMEOUT_SECONDS)
            except BaseException:
                await self._stop_locked()
                raise
            self._url = url
            self._remote = DshRemoteClient(url)
            self._drain_task = asyncio.create_task(self._drain(process))
            logger.info("dsh manager host started on %s", url.split("?", 1)[0])
            return url

    async def _read_url(self, process: asyncio.subprocess.Process) -> str:
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                stderr = b""
                if process.stderr is not None:
                    with contextlib.suppress(Exception):
                        stderr = await asyncio.wait_for(process.stderr.read(), 2)
                raise DshManagerUnavailableError(
                    "the dsh manager host exited during startup: "
                    + stderr.decode(errors="replace")[-800:]
                )
            match = _URL_LINE.search(raw.decode(errors="replace"))
            if match:
                return match.group(1)

    async def _drain(self, process: asyncio.subprocess.Process) -> None:
        """Keep the pipes flowing so the host never blocks on a full buffer."""

        async def pump(stream: asyncio.StreamReader | None, level: int) -> None:
            if stream is None:
                return
            while line := await stream.readline():
                logger.log(level, "dsh manager: %s", line.decode(errors="replace").rstrip())

        await asyncio.gather(
            pump(process.stdout, logging.DEBUG), pump(process.stderr, logging.DEBUG)
        )

    async def call(self, method: str, args: dict[str, Any] | None = None) -> Any:
        if method not in PLUGIN_MANAGER_METHODS:
            raise ValueError(f"unsupported dsh pluginManager method {method!r}")
        args = args or {}
        await self.ensure_started()
        assert self._remote is not None
        await self._refuse_managed_change(method, args)
        return await self._remote.call(f"pluginManager/{method}", **args)

    async def _refuse_managed_change(self, method: str, args: dict[str, Any]) -> None:
        """dsh reports the managed bundles as ordinary profile dependencies, so
        its PluginManager would remove or switch them off. The settings page
        locks them; this keeps the API and ``valuz ext`` from doing it either.
        dsh's own required rows stay dsh's to refuse; Valuz guards its own."""
        switching_off = args.get("enabled") is False
        if method == "setBundleEnabled" and args.get("enabled") is True:
            if args.get("name") in SURFACE_BUNDLES:
                raise DshManagedBundleError(
                    f"{args.get('name')!r} runs its own process surface and would break "
                    "Valuz's dsh sessions on this profile"
                )
        if method == "removeBundle" or (method == "setBundleEnabled" and switching_off):
            if args.get("name") in MANAGED_BUNDLES:
                raise DshManagedBundleError(
                    f"{args.get('name')!r} is part of Valuz's managed dsh profile"
                )
        if method == "setPluginEnabled" and switching_off:
            assert self._remote is not None
            bundles = await self._remote.call("pluginManager/listBundles")
            for bundle in bundles if isinstance(bundles, list) else []:
                if not isinstance(bundle, dict) or bundle.get("name") != VALUZ_BUNDLE:
                    continue
                rows = bundle.get("rows") if isinstance(bundle.get("rows"), list) else []
                if any(isinstance(r, dict) and r.get("entryId") == args.get("id") for r in rows):
                    raise DshManagedBundleError(
                        f"{args.get('id')!r} wires Valuz's dsh sessions and cannot be switched off"
                    )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        process, self._process = self._process, None
        remote, self._remote = self._remote, None
        self._url = None
        if remote is not None:
            with contextlib.suppress(Exception):
                await remote.aclose()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(BaseException):
                await self._drain_task
            self._drain_task = None


_manager: DshManagerHost | None = None


def get_dsh_manager() -> DshManagerHost:
    global _manager
    if _manager is None:
        _manager = DshManagerHost()
    return _manager
