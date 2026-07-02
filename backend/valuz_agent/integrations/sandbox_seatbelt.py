"""SeatbeltSandboxProvider — the minimal local kernel sandbox (macOS).

The default OSS driver for the ① supply face (see
``docs/design/kernel-sandbox-deployment.md`` §B.5). It runs ``valuz-server``
as a ``sandbox-exec``-wrapped subprocess on the same machine: zero
virtualization, nothing to install on macOS, the same PyInstaller/uv
artifact the host already runs. The subprocess inherits the Seatbelt
profile, so any process the runtime forks (codex/claude CLIs, arbitrary
bash) is confined too.

Two responsibilities:

1. ``build_seatbelt_profile`` — a PURE function turning a provision
   manifest into a ``sandbox-exec`` profile. This is the security-load-
   bearing core and is fully unit-tested: write allowlist = project cwd +
   kernel data dir + TMPDIR; explicit denies for the host business DB dir
   and secret store (the RED LINE); read allow for CLI login state
   (``~/.claude`` / ``~/.codex``); outbound network = host callback + LLM.

2. ``SeatbeltSandboxProvider`` — migrates a private kernel DB, spawns the
   sandboxed server on an OS-assigned port, waits for ``/health``, and
   returns the ``(base_url, token)`` handoff. ``destroy`` terminates it.

Isolation note: Seatbelt is **policy** isolation, not virtualization —
same kernel, same filesystem view minus the deny rules. It defends
against an agent over-reaching (a stray ``rm``, a prompt-injected
exfiltration of ``~/.ssh``), not against a kernel-level adversary. That is
the right threat model for a single-user desktop; stronger isolation is
the docker / microVM drivers (design §3.6).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import httpx

from valuz_agent.boot.kernel import KERNEL_DIR
from valuz_agent.ports.sandbox_provider import (
    MountGrant,
    MountSpec,
    SandboxBootContext,
    SandboxBootResult,
    SandboxEndpoint,
    SandboxProvisionError,
    SandboxSpec,
)

logger = logging.getLogger(__name__)

_BIND_LINE = re.compile(r"Uvicorn running on https?://127\.0\.0\.1:(\d+)")

# Empirically-supported macOS floor for the Seatbelt driver. ``sandbox-exec``
# is deprecated and the sandbox-extension SPI behaviour varies by release, so
# the driver only runs on a known-good range. This is the floor we've verified
# (Ventura); override with VALUZ_SEATBELT_MIN_MACOS when a different release is
# validated. The current dev host (macOS 26) is well above it.
_SEATBELT_MIN_MACOS_MAJOR = 13


def _macos_major() -> int | None:
    """Major macOS version (e.g. 26 for ``26.4``), or None if undetectable."""
    import platform

    ver = platform.mac_ver()[0]
    if not ver:
        return None
    try:
        return int(ver.split(".")[0])
    except ValueError:
        return None


# Standard macOS sandbox-extension classes, keyed by mount mode. These match
# the ``(extension "...")`` class the profile pre-declares; the values are
# the ``APP_SANDBOX_READ[_WRITE]`` symbol values exported by libsandbox.
_EXTENSION_CLASS = {
    "rw": b"com.apple.app-sandbox.read-write",
    "ro": b"com.apple.app-sandbox.read",
}


class _IssueSpi:
    """Lazy ctypes binding to ``sandbox_extension_issue_file`` (host side).

    The host is unsandboxed, so it can issue path-bound tokens for any path
    it can access. ``available`` is False off macOS / if the SPI is absent.
    """

    _loaded = False
    available = False
    _issue: Any = None

    @classmethod
    def load(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        if sys.platform != "darwin":
            return
        try:
            lib = ctypes.CDLL(None)
            issue = lib.sandbox_extension_issue_file
            issue.restype = ctypes.c_void_p  # raw ptr (token is malloc'd char*)
            issue.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint64]
            cls._issue = issue
            cls.available = True
        except (OSError, AttributeError):
            cls.available = False


def issue_extension(host_path: str, mode: Literal["rw", "ro"] = "rw") -> str:
    """Issue a sandbox-extension token granting ``mode`` access to
    ``host_path`` to whoever consumes it inside a sandbox.

    Returns the opaque token string. Raises ``SandboxProvisionError`` if the
    SPI is unavailable or the issue fails.

    Two load-bearing details learned empirically:

    - **flags MUST be 0.** ``flags=1`` makes the SPI return the literal
      string ``"invalid"`` (a 7-byte non-token) — silently useless.
    - **realpath the path.** Seatbelt and the extension match on the REAL
      path; ``/var`` → ``/private/var`` etc. must be resolved first or the
      consumed extension won't match the actual file access.
    """
    _IssueSpi.load()
    if not _IssueSpi.available:
        raise SandboxProvisionError(
            "sandbox_extension_issue_file unavailable (not macOS or SPI missing)"
        )
    real = os.path.realpath(str(Path(host_path).expanduser()))
    ptr = _IssueSpi._issue(_EXTENSION_CLASS[mode], real.encode(), 0)
    if not ptr:
        raise SandboxProvisionError(f"failed to issue extension for {real!r}")
    token = ctypes.cast(ptr, ctypes.c_char_p).value
    if token is None or token == b"invalid":
        raise SandboxProvisionError(f"extension issue returned no valid token for {real!r}")
    return token.decode()


def seatbelt_preflight() -> list[str]:
    """Return the reasons this host can't run the Seatbelt driver (empty =
    OK). A pure check — call it BEFORE spawning so the failure is upfront
    and actionable instead of a cryptic mid-provision error.

    Validates the three hard requirements: macOS, the ``sandbox-exec``
    binary, and a reachable kernel artifact (``app/main.py`` under
    ``KERNEL_DIR``). Credential/CLI-login checks are per-session, not
    boot-level, so they're out of scope here.
    """
    problems: list[str] = []
    if sys.platform != "darwin":
        problems.append(
            f"not macOS (sys.platform={sys.platform!r}); sandbox-exec is "
            "macOS-only — use a cloud/overlay driver elsewhere"
        )
    else:
        # Version gate: Seatbelt only runs on a known-good macOS range (the
        # sandbox-exec / extension-SPI behaviour is version-dependent).
        floor = _SEATBELT_MIN_MACOS_MAJOR
        try:
            floor = int(os.environ.get("VALUZ_SEATBELT_MIN_MACOS", floor))
        except ValueError:
            pass
        major = _macos_major()
        if major is not None and major < floor:
            problems.append(
                f"macOS {major} is below the supported floor for the Seatbelt "
                f"sandbox (>= {floor}); sandbox-exec / the extension SPI behave "
                "differently on older releases. Set VALUZ_SEATBELT_MIN_MACOS if "
                "you've verified this release."
            )
    if shutil.which("sandbox-exec") is None:
        problems.append("sandbox-exec not found on PATH (expected /usr/bin/sandbox-exec)")
    if not (KERNEL_DIR / "app" / "main.py").exists():
        problems.append(f"kernel artifact missing: {KERNEL_DIR / 'app' / 'main.py'}")
    return problems


def host_sandbox_rw_mounts() -> tuple[MountSpec, ...]:
    """The writable dirs a host-wide kernel sandbox needs — enumerated from
    ``fs_registry``/``settings`` so the manifest stays complete as paths
    evolve, rather than hardcoded in the boot wiring.

    Skill materialization writes symlinks into ``<cwd>/.agents/skills`` and
    ``<cwd>/.claude/skills`` under each project root, and skill *creation*
    writes new packs into the user/official skill roots — all of which must
    be writable or the runtime fails with "Operation not permitted" the
    moment it tries to set a skill up (the reported bug).

    "Dynamic" coverage by construction: we write-allow the ROOTS (the resolved
    user project root, the chat-cwd root, every skill root), so any project or
    skill created UNDER them after provision works without re-provisioning.
    A project bound OUTSIDE these roots (an arbitrary folder) is the one
    case a single host-wide sandbox can't reach — that needs a per-project
    sandbox (design §3.5) and is logged, not silently dropped.

    Read access to skill *sources* is already granted by the profile's
    broad ``(allow file-read*)``; this list is strictly the write set.
    """
    from valuz_agent.infra.fs_registry import fs_registry as fr
    from valuz_agent.infra.local_identity import resolve_local_user_id

    user_id = resolve_local_user_id()
    data_dir = fr.data_dir(user_id)

    dirs: list[Path] = [
        data_dir / "sandbox",  # the kernel's private DB
        data_dir / "projects",  # legacy managed chat/project cwds
        fr.project_root(user_id),  # real/managed projects + .agents|.claude/skills
        fr.official_skill_root(),  # official skill bootstrap
        fr.user_skill_root("claude"),  # user skill creation / submit
        *fr.legacy_user_skill_roots(),  # ~/.claude/skills, ~/.codex/skills
    ]
    seen: set[str] = set()
    mounts: list[MountSpec] = []
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        key = os.path.realpath(str(d))
        if key in seen:
            continue
        seen.add(key)
        mounts.append(MountSpec(target=str(d), source=str(d), mode="rw"))
    return tuple(mounts)


def build_seatbelt_profile(spec: SandboxSpec) -> str:
    """Translate a provision manifest into a ``sandbox-exec`` profile.

    Pure function — no IO, fully testable. The policy:

    - deny by default; allow process fork/exec (the runtime spawns CLIs);
    - read allowed broadly, then DENIED for the host business DB dir and
      secret store (``spec.deny_paths``) — the RED LINE;
    - write allowed only under the project cwd, the kernel data dir, and
      TMPDIR (rw mounts); everything else read-only;
    - read allowed for CLI login state so codex/claude can authenticate;
    - outbound network limited to the host callback (④) and the LLM hosts
      (``spec.allowed_domains``); 127.0.0.1 callback is always allowed.

    ``deny`` rules are emitted AFTER the broad ``allow file-read*`` so they
    take precedence (Seatbelt is last-match-wins within an operation).
    """
    rw_subpaths: list[str] = []
    ro_subpaths: list[str] = []
    for m in spec.mounts:
        (rw_subpaths if m.mode == "rw" else ro_subpaths).append(m.source)

    tmpdir = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    home = str(Path.home())

    lines: list[str] = [
        "(version 1)",
        "(deny default)",
        "; --- process: the runtime forks codex/claude CLIs + bash ---",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow signal (target self))",
        "; --- listen: the kernel binds an HTTP port on loopback ---",
        '(allow network-bind (local ip "localhost:*"))',
        '(allow network-inbound (local ip "localhost:*"))',
        "; --- read: broad (RED-LINE denies emitted LAST so they win) ---",
        "(allow file-read*)",
    ]

    lines.append("; --- write: project cwd + kernel data dir + tmp only ---")
    for p in (*rw_subpaths, tmpdir):
        lines.append(f"(allow file-write* (subpath {_q(p)}))")

    # Individual writable FILES (not trees): the shared kernel.db + its WAL/SHM
    # sidecars, which live next to valuz.db outside every rw mount. A per-file
    # ``literal`` allow keeps the grant tight — the sandbox can write these
    # three paths but nothing else in the data dir (valuz.db / secrets stay
    # denied below). ``literal`` (not ``subpath``) so it matches the exact file.
    if spec.rw_files:
        lines.append("; --- write: shared kernel.db + WAL/SHM (files, not tree) ---")
        for f in spec.rw_files:
            lines.append(f"(allow file-write* (literal {_q(f)}))")

    # Dynamic mount: pre-declare that this long-lived sandbox HONOURS macOS
    # sandbox-extension tokens of the standard read-write class. This grants
    # NOTHING on its own (no token = no access); it only lets the host
    # extend the live sandbox to a NEW external path after boot — a project
    # bound to a folder outside the static mounts above — by issuing a
    # path-bound token the kernel consumes (see kernel/app/sandbox_control.py
    # and integrations/sandbox_runtime.py). Without this line a consumed
    # token is inert, so it is the load-bearing half of the no-restart
    # dynamic-mount path. Backward-compatible: a sandbox that never receives
    # a token behaves exactly as before.
    lines.append("; --- dynamic mount: honour rw sandbox-extension tokens ---")
    lines.append('(allow file-read* file-write* (extension "com.apple.app-sandbox.read-write"))')

    # The agent CLIs need their own state dirs READ AND WRITE: codex
    # writes ``~/.codex/state_*.sqlite`` (and claude its caches), so a
    # read-only allow makes the runtime fail with "attempt to write a
    # readonly database". These are the agent's own dirs (login state +
    # runtime state), not host business data — writable is the right call
    # for the minimal form; the host business DB / secrets stay denied
    # below regardless.
    lines.append("; --- agent CLI state dirs (login + runtime, rw) ---")
    for d in (".claude", ".codex"):
        cli_dir = str(Path(home) / d)
        lines.append(f"(allow file-read*  (subpath {_q(cli_dir)}))")
        lines.append(f"(allow file-write* (subpath {_q(cli_dir)}))")

    lines.append("; --- network: host callback (④) + LLM hosts ---")
    lines.append('(allow network-outbound (remote ip "localhost:*"))')
    lines.append("(allow network-outbound (remote unix-socket))")
    # Outbound to arbitrary TLS hosts — the minimal form keeps this broad;
    # tightening to spec.allowed_domains is the NetworkPolicy upgrade (S1+).
    lines.append('(allow network-outbound (remote tcp "*:443"))')
    lines.append('(allow network-outbound (remote tcp "*:80"))')

    # RED LINE — denies emitted LAST so they override ANY allow above
    # (Seatbelt is last-match-wins per operation): the host business DB
    # and secret store stay unreadable/unwritable even when they sit under
    # a directory the manifest write-allows.
    lines.append("; --- RED LINE: host business DB + secrets (deny wins) ---")
    for deny in spec.deny_paths:
        lines.append(f"(deny file-read*  (subpath {_q(deny)}))")
        lines.append(f"(deny file-write* (subpath {_q(deny)}))")

    return "\n".join(lines) + "\n"


def _q(path: str) -> str:
    """Quote a path for a Seatbelt subpath literal.

    Canonicalises symlinks: Seatbelt matches on the REAL path, and on
    macOS ``/tmp`` → ``/private/tmp`` and ``/var`` → ``/private/var`` are
    symlinks. A rule written against the un-resolved path silently fails
    to match the actual file access (the cause of an early "unable to open
    database file" under the sandbox). ``realpath`` resolves existing
    parents even when the leaf doesn't exist yet.
    """
    resolved = os.path.realpath(str(Path(path).expanduser()))
    return '"' + resolved.replace("\\", "\\\\").replace('"', '\\"') + '"'


class SeatbeltSandboxProvider:
    """``SandboxProvider`` backed by ``sandbox-exec`` + a uv subprocess."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._endpoints: dict[str, SandboxEndpoint] = {}
        self._log_drainers: dict[str, threading.Thread] = {}

    @classmethod
    def from_existing(cls, sandbox_id: str, base_url: str, token: str) -> SeatbeltSandboxProvider:
        """A provider that does NOT own the sandbox process — it only knows
        the endpoint, enough to issue/deliver dynamic grants.

        The dynamic-mount path runs in whichever host process serves the API
        (under uvicorn ``--reload`` that is a child that never called
        ``provision``). Issuing a token needs only the host's privilege (any
        unsandboxed process), and delivering it needs only the endpoint — so
        this seeds ``_endpoints`` from the env the provisioner published,
        with no ``_procs`` entry (``destroy`` here is a no-op).
        """
        self = cls()
        self._endpoints[sandbox_id] = SandboxEndpoint(
            sandbox_id=sandbox_id, base_url=base_url, token=token
        )
        return self

    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint:
        problems = seatbelt_preflight()
        if problems:
            raise SandboxProvisionError(
                "SeatbeltSandboxProvider preflight failed: " + "; ".join(problems)
            )
        token = secrets.token_urlsafe(24)
        # Model A: the sandbox ALWAYS keeps a private local kernel SQLite
        # (local-first reads + availability). When ``KERNEL_STORE=remote`` it
        # ADDITIONALLY write-throughs to a trusted data API (the durable
        # backend) over HTTP — the sandbox holds only a JWT + the data-API URL
        # (KERNEL_STORE / VALUZ_DATA_API_* carried in ``spec.env``), never a PG
        # credential. The local DB is present in both modes.
        db_url = f"sqlite+aiosqlite:///{spec.kernel_db_path}"
        Path(spec.kernel_db_path).parent.mkdir(parents=True, exist_ok=True)
        if not spec.rw_files:
            spec = replace(
                spec,
                rw_files=(
                    spec.kernel_db_path,
                    spec.kernel_db_path + "-wal",
                    spec.kernel_db_path + "-shm",
                ),
            )
        # Bootstrap step 1 — migrate the private kernel DB. Done host-side in a
        # one-shot subprocess (clean settings, private DB); the kernel-image
        # self-migration is the cloud-form upgrade.
        await self._migrate(spec.kernel_db_path)

        # Bootstrap step 2 — spawn the sandboxed server on port 0.
        profile = build_seatbelt_profile(spec)
        proc = self._spawn(spec, profile, token, db_url)
        self._procs[spec.sandbox_id] = proc

        try:
            port = await self._await_bind(proc)
        except Exception as exc:
            self._terminate(proc)
            self._procs.pop(spec.sandbox_id, None)
            raise SandboxProvisionError(f"kernel sandbox failed to start: {exc}") from exc

        # CRITICAL: keep draining the kernel's stdout for the process's
        # lifetime. ``_await_bind`` only reads up to the bind line; if nothing
        # consumes the pipe afterwards, uvicorn's per-request access logs fill
        # the ~64 KiB OS pipe buffer and the *next* log ``write()`` blocks
        # forever — on the kernel's main thread, freezing its asyncio loop
        # mid-write. The host then sees every kernel-proxied call (sessions,
        # runs, the WS run channel) hang. Drain on a daemon thread so the pipe
        # never backs up; the thread exits on EOF when the process dies.
        self._start_log_drain(spec.sandbox_id, proc)

        endpoint = SandboxEndpoint(
            sandbox_id=spec.sandbox_id,
            base_url=f"http://127.0.0.1:{port}",
            token=token,
        )
        self._endpoints[spec.sandbox_id] = endpoint
        return endpoint

    def _start_log_drain(self, sandbox_id: str, proc: subprocess.Popen[bytes]) -> None:
        """Continuously read the kernel subprocess's stdout so its log writes
        never block on a full pipe. Forwards each line to the host log (the
        kernel's output was otherwise discarded after bind). Runs on a daemon
        thread that ends when ``readline`` hits EOF (process exit)."""
        if proc.stdout is None:
            return

        stdout = proc.stdout

        def _pump() -> None:
            try:
                for raw in iter(stdout.readline, b""):
                    logger.debug(
                        "[sandbox %s] %s",
                        sandbox_id,
                        raw.decode(errors="replace").rstrip(),
                    )
            except (ValueError, OSError):
                # Pipe closed under us on teardown — nothing left to drain.
                pass

        t = threading.Thread(target=_pump, name=f"sandbox-log-{sandbox_id}", daemon=True)
        t.start()
        self._log_drainers[sandbox_id] = t

    async def health(self, sandbox_id: str) -> bool:
        ep = self._endpoints.get(sandbox_id)
        proc = self._procs.get(sandbox_id)
        if ep is None or proc is None or proc.poll() is not None:
            return False
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{ep.base_url}/health", timeout=2.0)
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def destroy(self, sandbox_id: str) -> None:
        proc = self._procs.pop(sandbox_id, None)
        self._endpoints.pop(sandbox_id, None)
        # The drain thread exits on its own once the process dies (readline
        # hits EOF); just drop our handle to it.
        self._log_drainers.pop(sandbox_id, None)
        if proc is not None:
            self._terminate(proc)

    async def bind_workspace(
        self,
        sandbox_id: str,
        host_path: str,
        mode: Literal["rw", "ro"] = "rw",
        *,
        owner_user_id: str = "",
    ) -> MountGrant:
        """Issue a sandbox-extension token for ``host_path`` and have the
        running kernel consume it — extending the live sandbox to that path
        without a restart. ``kernel_cwd == host_path`` (host and sandbox
        share a filesystem locally), so the caller need not rewrite cwd.

        ``owner_user_id`` is unused locally (host and sandbox share one
        filesystem and one user); it is part of the protocol for cloud drivers
        that stage files under the owner's subtree.
        """
        ep = self._endpoints.get(sandbox_id)
        if ep is None:
            raise SandboxProvisionError(f"unknown sandbox {sandbox_id!r} — provision first")
        real = os.path.realpath(str(Path(host_path).expanduser()))
        token = issue_extension(real, mode)  # host-side, ctypes
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{ep.base_url}/internal/sandbox/grant",
                    headers={"Authorization": f"Bearer {ep.token}"},
                    json={"token": token, "path": real, "mode": mode},
                    timeout=10.0,
                )
                r.raise_for_status()
                handle = r.json()["data"]["handle"]
        except httpx.HTTPError as exc:
            raise SandboxProvisionError(
                f"kernel rejected workspace grant for {real!r}: {exc}"
            ) from exc
        # grant_id encodes the kernel-side handle for unbind.
        return MountGrant(
            grant_id=str(handle),
            kernel_cwd=host_path,  # unchanged locally — same filesystem
            host_path=real,
            mode=mode,
        )

    async def unbind_workspace(self, sandbox_id: str, grant_id: str) -> None:
        ep = self._endpoints.get(sandbox_id)
        if ep is None:
            return
        try:
            async with httpx.AsyncClient() as c:
                await c.post(
                    f"{ep.base_url}/internal/sandbox/revoke",
                    headers={"Authorization": f"Bearer {ep.token}"},
                    json={"handle": int(grant_id)},
                    timeout=10.0,
                )
        except (httpx.HTTPError, ValueError):
            pass  # best-effort revoke; sandbox teardown reclaims everything

    # ---- internals -----------------------------------------------------

    async def _migrate(self, kernel_db_path: str) -> None:
        # ``run_kernel_migrations`` reads the HOST ``Settings`` (env prefix
        # ``VALUZ_``) and migrates the runtime DB URL — i.e.
        # ``VALUZ_DATABASE_URL``, NOT the plain ``DATABASE_URL`` the kernel
        # server reads, and NOT ``VALUZ_KERNEL_DATABASE_URL`` (the alembic
        # upgrade ignores it). Point ``VALUZ_DATABASE_URL`` at the private
        # file; ``kernel_db_url`` falls back to it, so the stale-table drop
        # checks the same DB. Setting the wrong var silently migrates the
        # default ~/.valuz-oss/valuz.db ("no such table: sessions").
        env = dict(os.environ)
        env["VALUZ_DATABASE_URL"] = f"sqlite:///{kernel_db_path}"
        env.pop("VALUZ_KERNEL_DATABASE_URL", None)
        env.setdefault("PYTHONPATH", str(KERNEL_DIR))
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import valuz_agent.boot.kernel as k; k.run_kernel_migrations()",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxProvisionError(
                f"kernel DB migration failed:\n{out.decode(errors='replace')}"
            )

    def _spawn(
        self,
        spec: SandboxSpec,
        profile: str,
        token: str,
        db_url: str | None,
    ) -> subprocess.Popen[bytes]:
        env = dict(os.environ)
        env.update(
            {
                "KERNEL_AUTH_TOKEN": token,
                "PYTHONPATH": str(KERNEL_DIR),
                # Mount the self-extension control plane so the host can
                # grant new external paths into this live sandbox after boot
                # (see kernel/app/sandbox_control.py).
                "KERNEL_SANDBOX_CONTROL": "1",
                **spec.env,  # ⑥ L1 credential injection (provider keys + store mode)
            }
        )
        # Model A: the sandbox always holds its private local SQLite DSN; remote
        # mode adds the data-API coordinates (in ``spec.env``) for write-through
        # on top of it, never instead of it.
        if db_url is not None:
            env["DATABASE_URL"] = db_url
            if "KERNEL_STORE" not in spec.env:
                env["KERNEL_STORE"] = "local"
            if "VALUZ_DURABLE_DATABASE_URL" not in spec.env:
                env["VALUZ_DURABLE_DATABASE_URL"] = db_url
        if spec.host_callback_url:
            # ④ harness MCP callback target for the codex runtime.
            env["CODEX_TOOLKIT_BASE_URL"] = spec.host_callback_url
        # sandbox-exec -p '<profile>' <cmd...> applies the inline profile.
        cmd = [
            "sandbox-exec",
            "-p",
            profile,
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            # Silence uvicorn's per-request access firehose (the stream that
            # filled the undrained pipe and froze the loop) with
            # ``--no-access-log`` — NOT by lowering ``--log-level``. The level
            # also gates the startup "Uvicorn running on http://127.0.0.1:<port>"
            # line, which ``_await_bind`` parses to learn the OS-assigned port;
            # suppressing it hangs the bind probe and boot never finishes.
            # ``--no-access-log`` keeps that INFO line while dropping the
            # per-request noise.
            "--no-access-log",
        ]
        return subprocess.Popen(
            cmd,
            cwd=str(KERNEL_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    async def _await_bind(self, proc: subprocess.Popen[bytes], deadline_s: float = 30.0) -> int:
        """Read the kernel's stdout until the uvicorn bind line, then
        confirm /health. Returns the bound port."""
        assert proc.stdout is not None
        loop = asyncio.get_event_loop()
        port: int | None = None
        lines: list[str] = []
        # readline blocks; run it off the loop so health polling can interleave.
        while port is None:
            raw = await loop.run_in_executor(None, proc.stdout.readline)
            if not raw:
                raise SandboxProvisionError("kernel exited before binding:\n" + "".join(lines))
            text = raw.decode(errors="replace")
            lines.append(text)
            m = _BIND_LINE.search(text)
            if m:
                port = int(m.group(1))
        # Confirm health before declaring success.
        async with httpx.AsyncClient() as c:
            import time

            end = time.monotonic() + deadline_s
            while time.monotonic() < end:
                try:
                    r = await c.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
                    if r.status_code == 200:
                        return port
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
        raise SandboxProvisionError("kernel bound a port but never became healthy")

    @staticmethod
    def _terminate(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


class SeatbeltDriver:
    """The OSS built-in local-sandbox driver — registered as ``seatbelt``.

    Owns the boot wiring that used to live inline in ``main.py``: preflight,
    the writable-mount manifest, the RED-LINE deny set, and the
    ``SandboxBootResult`` (endpoint + provider + static roots). ``main.py`` and
    ``sandbox_runtime`` drive this through the ``SandboxDriver`` protocol and
    never name Seatbelt directly.
    """

    name = "seatbelt"

    def preflight(self) -> list[str]:
        return seatbelt_preflight()

    async def provision_for_boot(self, ctx: SandboxBootContext) -> SandboxBootResult:
        from valuz_agent.infra.db_urls import (
            db_url,
            kernel_db_url,
            sqlite_path_from_url,
        )
        from valuz_agent.infra.fs_registry import fs_registry
        from valuz_agent.infra.local_identity import resolve_local_user_id

        user_id = resolve_local_user_id()
        data_dir = fs_registry.data_dir(user_id)
        sandbox_dir = data_dir / "sandbox"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        host_db = sqlite_path_from_url(db_url())
        # The kernel uses the runtime ``kernel.db`` resolved by ``db_urls`` so
        # local mode lands under ``<data_dir>/<user_id>/`` while explicit DB
        # config keeps its configured location. Write-allow the file + its
        # WAL/SHM sidecars explicitly; the host migrates this file before spawn
        # and the sandbox is its sole writer at runtime.
        kernel_db = sqlite_path_from_url(kernel_db_url())
        if kernel_db is None:
            raise RuntimeError("seatbelt sandbox requires a SQLite kernel database path")

        deny_paths = [
            str(fs_registry.secrets_dir(user_id)),
        ]
        if host_db is not None:
            deny_paths.extend((str(host_db), str(host_db) + "-wal", str(host_db) + "-shm"))

        mounts = host_sandbox_rw_mounts()
        env = dict(ctx.passthrough_env)  # ⑥ L1 credential injection
        # Model A: the sandbox ALWAYS keeps a local kernel.db (grant its write
        # files in every mode). When the user has configured a durable
        # (Postgres-backed) data service, the sandbox must NOT hold the DSN — it
        # write-throughs to the HOST-mounted DataService (/internal/data) over
        # HTTP, carrying only a freshly minted JWT. "Who holds the credential" is
        # resolved here: in-process holds the DSN; the sandbox holds a token.
        rw_files: tuple[str, ...] = (
            str(kernel_db),
            str(kernel_db) + "-wal",
            str(kernel_db) + "-shm",
        )
        # Store tier comes purely from the environment (KERNEL_STORE), loaded at
        # boot — same source the kernel's AppConfig reads. A Postgres-backed data
        # service (pg/remote) means the sandbox reaches the host DataService over
        # HTTP+JWT and never holds the DSN.
        # Owner is the local device user here; the multi-tenant cloud driver
        # passes the request principal instead (same helper — see
        # ``boot/data_service_inject``). The helper no-ops on a local store.
        if ctx.host_callback_url:
            from valuz_agent.boot.data_service_inject import data_service_env

            env.update(
                data_service_env(
                    owner_user_id=user_id,
                    host_callback_url=ctx.host_callback_url,
                )
            )
        spec = SandboxSpec(
            sandbox_id="host-kernel",
            kernel_db_path=str(kernel_db),
            mounts=mounts,
            rw_files=rw_files,
            env=env,
            host_callback_url=ctx.host_callback_url,
            # Empty on the local boot path (single-user); a multi-tenant driver
            # threads the request principal here (see SandboxBootContext).
            owner_user_id=ctx.owner_user_id,
            # RED LINE: host business DB (+ wal/shm) and secret store.
            deny_paths=tuple(deny_paths),
        )
        provider = SeatbeltSandboxProvider()
        endpoint = await provider.provision(spec)
        return SandboxBootResult(
            endpoint=endpoint,
            provider=provider,
            static_roots=tuple(m.source for m in mounts),
        )

    def attach(self, ctx: SandboxBootContext, endpoint: SandboxEndpoint) -> SandboxBootResult:
        provider = SeatbeltSandboxProvider.from_existing(
            "host-kernel", endpoint.base_url, endpoint.token
        )
        return SandboxBootResult(
            endpoint=endpoint,
            provider=provider,
            static_roots=tuple(m.source for m in host_sandbox_rw_mounts()),
        )
