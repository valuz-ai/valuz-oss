"""Run one code automation's entry program in the right environment.

The host decides *what* runs — entry file, effective input, timeout, the run
and workspace directories — and records the outcome on the run row. The
executor decides *where*: a subprocess on the desktop, a sandbox on a shared
deployment. OSS ships the desktop executor. A cloud deployment binds its own
on ``ext.automation_code_executor``; the local one refuses to start there, so a
missing binding fails the run instead of running a user's program inside the
web / worker process (the same refusal a site preview applies).

Contract for every executor:

- Start the program with ``spec.project_cwd`` as its working directory and
  ``spec.env`` merged over a minimal base environment (no inherited secrets).
- Enforce ``spec.timeout_s`` by killing the whole process group.
- Stop the program when ``cancel`` is set.
- On return, ``spec.run_dir`` on the HOST holds ``stdout.log`` / ``stderr.log``
  and, when the program produced one, ``output.json`` (a remote executor pulls
  them back). ``output_json`` carries the same bytes so the host never has to
  race a mounted filesystem for them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

CodeRunStatus = Literal["success", "failed", "timeout", "cancelled"]

#: Bytes of each log kept on the run row (the full files stay in the run dir).
LOG_TAIL_BYTES = 64 * 1024

#: Runtimes the host knows how to launch. ``python`` goes through the stdlib
#: bootstrap (``run(ctx)`` protocol); ``shell`` runs the entry with bash and
#: expects it to write the output file itself.
CODE_RUNTIMES: tuple[str, ...] = ("python", "shell")


@dataclass(frozen=True, slots=True)
class CodeRunSpec:
    """Everything an executor needs, and nothing it must look up."""

    user_id: str
    automation_id: str
    run_id: str
    runtime: str
    #: Absolute path of the author's entry file, already verified to sit
    #: inside ``project_cwd``.
    entry_path: str
    project_cwd: str
    run_dir: str
    workspace_dir: str
    #: Absolute path of the stdlib bootstrap copy (python runtime only).
    bootstrap_path: str
    ctx_path: str
    output_path: str
    timeout_s: int
    #: ``VALUZ_AUTOMATION_*`` variables; executors merge these over their own
    #: minimal base env.
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CodeRunOutcome:
    status: CodeRunStatus
    exit_code: int | None
    #: Where it ran — ``local:<pid>`` / ``sandbox:<instance id>`` — recorded on
    #: the run row so a failure can be traced to an environment.
    executor_ref: str
    #: Contents of ``output.json`` when the program wrote one.
    output_json: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error_code: str | None = None
    error_message: str | None = None


class CodeExecutorUnavailableError(RuntimeError):
    """No executor may run code in this process (see module docstring)."""


@runtime_checkable
class AutomationCodeExecutor(Protocol):
    async def execute(self, spec: CodeRunSpec, *, cancel: asyncio.Event) -> CodeRunOutcome: ...


def read_tail(path: str | Path, limit: int = LOG_TAIL_BYTES) -> str:
    """Last ``limit`` bytes of a log file, decoded leniently; ``""`` if absent."""
    try:
        p = Path(path)
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def command_for(spec: CodeRunSpec, *, interpreter: str) -> list[str]:
    """The argv both executors launch — one place so they cannot drift."""
    if spec.runtime == "shell":
        return [interpreter, spec.entry_path]
    return [
        interpreter,
        spec.bootstrap_path,
        "--ctx",
        spec.ctx_path,
        "--entry",
        spec.entry_path,
        "--output",
        spec.output_path,
    ]


def base_environment() -> dict[str, str]:
    """The minimal env a program starts with — never the host's whole one.

    ``PATH`` / ``HOME`` / ``LANG`` / ``TMPDIR`` are what an interpreter needs
    to start and write temp files; everything else the host process carries
    (tokens, database URLs) stays out of the program's reach.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")
    out = {k: v for k, v in os.environ.items() if k in keep}
    out.setdefault("PATH", os.defpath)
    out.setdefault("LANG", "C.UTF-8")
    out["PYTHONUNBUFFERED"] = "1"
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    return out


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class LocalSubprocessCodeExecutor:
    """Desktop executor: the program runs as a child of this process.

    Refuses to exist on a ``cloud`` deployment — there the program belongs in
    a sandbox, and a deployment that forgot to bind one must fail the run
    rather than execute it here.
    """

    def __init__(
        self,
        *,
        python: str | None = None,
        shell: str | None = None,
        deployment_type: str | None = None,
    ) -> None:
        self._python = python
        self._shell = shell
        self._deployment_type = deployment_type

    def _deployment(self) -> str:
        if self._deployment_type is not None:
            return self._deployment_type
        from valuz_agent.infra.config import settings

        return settings.deployment_type

    def resolve_interpreter(self, runtime: str) -> str | None:
        if runtime == "shell":
            return self._shell or shutil.which("bash") or shutil.which("sh")
        if self._python:
            return self._python
        from valuz_agent.infra.config import settings

        configured = getattr(settings, "automation_python", None)
        if configured:
            return str(configured)
        return shutil.which("python3") or shutil.which("python")

    async def execute(self, spec: CodeRunSpec, *, cancel: asyncio.Event) -> CodeRunOutcome:
        if self._deployment() == "cloud":
            # Fail closed: a shared deployment never runs user programs in the
            # host process. The runner turns this into a failed run with
            # ``AUTOMATION_CODE_EXECUTOR_UNAVAILABLE``.
            raise CodeExecutorUnavailableError(
                "the local subprocess executor is not allowed on a cloud deployment"
            )
        interpreter = self.resolve_interpreter(spec.runtime)
        if not interpreter:
            return CodeRunOutcome(
                status="failed",
                exit_code=None,
                executor_ref="local:none",
                error_code="AUTOMATION_RUNTIME_UNAVAILABLE",
                error_message=(
                    f"no interpreter for runtime {spec.runtime!r}; set "
                    "VALUZ_AUTOMATION_PYTHON or put python3 on PATH"
                ),
            )
        env = base_environment()
        env.update(dict(spec.env))
        run_dir = Path(spec.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        with stdout_path.open("wb") as out_fh, stderr_path.open("wb") as err_fh:
            proc = await asyncio.create_subprocess_exec(
                *command_for(spec, interpreter=interpreter),
                cwd=spec.project_cwd,
                env=env,
                stdout=out_fh,
                stderr=err_fh,
                # Own process group so a timeout / cancel can kill the whole
                # tree, not just the interpreter.
                start_new_session=(os.name == "posix"),
            )
            ref = f"local:{proc.pid}"
            waiter = asyncio.ensure_future(proc.wait())
            canceller = asyncio.ensure_future(cancel.wait())
            try:
                done, _pending = await asyncio.wait(
                    {waiter, canceller},
                    timeout=spec.timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if waiter in done:
                    status: CodeRunStatus = "success" if proc.returncode == 0 else "failed"
                elif canceller in done:
                    _kill_group(proc)
                    await proc.wait()
                    status = "cancelled"
                else:
                    _kill_group(proc)
                    await proc.wait()
                    status = "timeout"
            finally:
                for task in (waiter, canceller):
                    if not task.done():
                        task.cancel()
        output_json: str | None = None
        try:
            output_json = Path(spec.output_path).read_text(encoding="utf-8")
        except OSError:
            output_json = None
        outcome = CodeRunOutcome(
            status=status,
            exit_code=proc.returncode,
            executor_ref=ref,
            output_json=output_json,
            stdout_tail=read_tail(stdout_path),
            stderr_tail=read_tail(stderr_path),
        )
        if status == "timeout":
            outcome.error_code = "AUTOMATION_CODE_TIMEOUT"
            outcome.error_message = f"program exceeded {spec.timeout_s}s and was killed"
        elif status == "cancelled":
            outcome.error_code = "AUTOMATION_CANCELLED"
            outcome.error_message = "cancelled while running"
        return outcome


__all__ = [
    "CODE_RUNTIMES",
    "LOG_TAIL_BYTES",
    "AutomationCodeExecutor",
    "CodeExecutorUnavailableError",
    "CodeRunOutcome",
    "CodeRunSpec",
    "CodeRunStatus",
    "LocalSubprocessCodeExecutor",
    "base_environment",
    "command_for",
    "read_tail",
]
