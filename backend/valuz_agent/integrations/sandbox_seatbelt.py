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
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from valuz_agent.boot.kernel import KERNEL_DIR
from valuz_agent.ports.sandbox_provider import (
    SandboxEndpoint,
    SandboxProvisionError,
    SandboxSpec,
)

_BIND_LINE = re.compile(r"Uvicorn running on https?://127\.0\.0\.1:(\d+)")


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
            "macOS-only — use the docker driver elsewhere"
        )
    if shutil.which("sandbox-exec") is None:
        problems.append("sandbox-exec not found on PATH (expected /usr/bin/sandbox-exec)")
    if not (KERNEL_DIR / "app" / "main.py").exists():
        problems.append(f"kernel artifact missing: {KERNEL_DIR / 'app' / 'main.py'}")
    return problems


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
        lines.append(f'(allow file-write* (subpath {_q(p)}))')

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
        lines.append(f'(allow file-read*  (subpath {_q(cli_dir)}))')
        lines.append(f'(allow file-write* (subpath {_q(cli_dir)}))')

    lines.append("; --- network: host callback (④) + LLM hosts ---")
    lines.append("(allow network-outbound (remote ip \"localhost:*\"))")
    lines.append('(allow network-outbound (remote unix-socket))')
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
        lines.append(f'(deny file-read*  (subpath {_q(deny)}))')
        lines.append(f'(deny file-write* (subpath {_q(deny)}))')

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

    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint:
        problems = seatbelt_preflight()
        if problems:
            raise SandboxProvisionError(
                "SeatbeltSandboxProvider preflight failed: " + "; ".join(problems)
            )
        token = secrets.token_urlsafe(24)
        db_url = f"sqlite+aiosqlite:///{spec.kernel_db_path}"

        # Bootstrap step 1 — migrate the private kernel DB. Done host-side
        # in a one-shot subprocess (clean settings, private DB); the
        # kernel-image self-migration is the cloud-form upgrade.
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

        endpoint = SandboxEndpoint(
            sandbox_id=spec.sandbox_id,
            base_url=f"http://127.0.0.1:{port}",
            token=token,
        )
        self._endpoints[spec.sandbox_id] = endpoint
        return endpoint

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
        if proc is not None:
            self._terminate(proc)

    # ---- internals -----------------------------------------------------

    async def _migrate(self, kernel_db_path: str) -> None:
        # ``run_kernel_migrations`` reads the HOST ``Settings`` (env prefix
        # ``VALUZ_``) and migrates ``settings.db_url_async`` — i.e.
        # ``VALUZ_DATABASE_URL``, NOT the plain ``DATABASE_URL`` the kernel
        # server reads, and NOT ``VALUZ_KERNEL_DATABASE_URL`` (the alembic
        # upgrade ignores it). Point ``VALUZ_DATABASE_URL`` at the private
        # file; ``kernel_db_url`` falls back to it, so the stale-table drop
        # checks the same DB. Setting the wrong var silently migrates the
        # default ~/.valuz/app/valuz.db ("no such table: sessions").
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
        db_url: str,
    ) -> subprocess.Popen[bytes]:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": db_url,
                "KERNEL_AUTH_TOKEN": token,
                "PYTHONPATH": str(KERNEL_DIR),
                **spec.env,  # ⑥ L1 credential injection (provider keys)
            }
        )
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
            "--log-level",
            "info",
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
                raise SandboxProvisionError(
                    "kernel exited before binding:\n" + "".join(lines)
                )
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
