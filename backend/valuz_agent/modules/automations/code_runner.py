"""Host side of one code automation run.

The runner (``in_process_runner``) decides *when*; this module decides what a
program is given and what becomes of what it returns:

1. Resolve the project directory the same way a session gets its cwd
   (``fs_registry.project_cwd``), so a chat automation's lazy chat project and
   a real project both work, on the desktop and in a sandbox alike.
2. Check the entry stays inside that directory, lay out the platform-managed
   ``.valuz/automations/<id>/{workspace,runs/<run>}`` tree, write ``ctx.json``
   / ``input.json`` and a copy of the stdlib bootstrap into the run dir.
3. Hand a ``CodeRunSpec`` to ``ext.automation_code_executor`` and watch the
   run row for a cancel request meanwhile.
4. Read the wrapper the program wrote, validate the artifact against the
   result contract, deliver declared files as artifact rows, and report a
   ``CodeRunResult`` the runner writes onto the run row.

Nothing here touches the run row: the runner owns run-row writes (and the
execution lease that fences them), this module owns the program.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.automations.contracts import (
    ContractViolationError,
    artifact_summary,
    result_contract_of,
    validate_artifact,
)
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.ports.automation_code_executor import (
    CodeExecutorUnavailableError,
    CodeRunOutcome,
    CodeRunSpec,
)
from valuz_agent.ports.automation_result import (
    AutomationArtifactEvent,
    AutomationArtifactFile,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.resources.automation_runtime import BOOTSTRAP_PATH

logger = logging.getLogger(__name__)

#: Platform-managed tree inside the project (already ignored by the project
#: scanner, see ``modules/projects/service.py``).
AUTOMATIONS_DIRNAME = ".valuz/automations"
BOOTSTRAP_NAME = "_valuz_runner.py"
CANCEL_POLL_S = 2.0
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILES = 32


class CodeRunPreparationError(Exception):
    """The run cannot start; ``code`` is what the run row records."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CodeRunPaths:
    project_cwd: Path
    entry: Path
    root: Path
    workspace: Path
    run_dir: Path
    ctx: Path
    input: Path
    output: Path
    bootstrap: Path


@dataclass(slots=True)
class CodeRunResult:
    """What the runner writes onto the run row."""

    status: str
    error_code: str | None = None
    error_message: str | None = None
    artifact: dict[str, Any] | None = None
    files: list[AutomationArtifactFile] = field(default_factory=list)
    executor_ref: str | None = None
    log_tail: str | None = None
    exit_code: int | None = None

    @property
    def result_summary(self) -> str | None:
        if self.artifact is not None:
            return artifact_summary(self.artifact)
        return self.error_message


# ── Preparation ───────────────────────────────────────────────────────


async def resolve_project_cwd(db: Any, user_id: str, project_id: str) -> Path:
    """The directory a session in this project would get — same function,
    same answer, whichever plane runs it."""
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    if row is None:
        raise CodeRunPreparationError(
            "AutomationProjectNotFound", f"project {project_id} no longer exists"
        )
    if row.kind == "project":
        return Path(fs_registry.project_cwd(user_id, project_id, "project", row.root_path))
    return Path(fs_registry.project_cwd(user_id, project_id, "chat", row.root_path))


def resolve_paths(
    *, project_cwd: Path, automation_id: str, run_id: str, entry: str
) -> CodeRunPaths:
    """Where everything lives for this run; refuses an entry outside the project."""
    root_real = project_cwd.resolve()
    entry_path = (project_cwd / entry).resolve()
    if entry_path != root_real and root_real not in entry_path.parents:
        raise CodeRunPreparationError(
            "AUTOMATION_CODE_ENTRY_INVALID", f"entry escapes the project directory: {entry}"
        )
    if not entry_path.is_file():
        raise CodeRunPreparationError(
            "AUTOMATION_CODE_ENTRY_MISSING", f"entry file not found: {entry}"
        )
    root = project_cwd / AUTOMATIONS_DIRNAME / automation_id
    run_dir = root / "runs" / run_id
    return CodeRunPaths(
        project_cwd=project_cwd,
        entry=entry_path,
        root=root,
        workspace=root / "workspace",
        run_dir=run_dir,
        ctx=run_dir / "ctx.json",
        input=run_dir / "input.json",
        output=run_dir / "output.json",
        bootstrap=run_dir / BOOTSTRAP_NAME,
    )


def build_ctx(
    *,
    row: AutomationRow,
    run: AutomationRunRow,
    paths: CodeRunPaths,
    effective_input: str | Mapping[str, Any] | None,
    previous: AutomationRunRow | None,
    timezone: str,
    locale: str,
) -> dict[str, Any]:
    """The ``ctx`` handed to ``run(ctx)`` — plain JSON, documented in the
    ``automation`` skill; keys are camelCase there, so they are here."""
    return {
        "automationId": row.id,
        "automationName": row.name,
        "runId": run.id,
        "projectId": row.project_id,
        "projectDir": str(paths.project_cwd),
        "workspaceDir": str(paths.workspace),
        "runDir": str(paths.run_dir),
        "input": (
            dict(effective_input) if isinstance(effective_input, Mapping) else effective_input
        ),
        "trigger": {
            "type": run.trigger_type,
            "invokedBy": run.invoked_by_ref,
            "invokedBySessionId": run.invoked_by_session_id,
        },
        "triggeredAt": run.triggered_at,
        "scheduledAt": run.triggered_at if run.trigger_type in ("cron", "interval") else None,
        "timezone": timezone,
        "locale": locale,
        "previous": (
            {
                "runId": previous.id,
                "completedAt": previous.completed_at,
                "artifact": previous.artifact_json,
            }
            if previous is not None
            else None
        ),
    }


def build_env(paths: CodeRunPaths, *, row: AutomationRow, run: AutomationRunRow) -> dict[str, str]:
    return {
        "VALUZ_AUTOMATION_ID": row.id,
        "VALUZ_AUTOMATION_RUN_ID": run.id,
        "VALUZ_AUTOMATION_PROJECT_DIR": str(paths.project_cwd),
        "VALUZ_AUTOMATION_RUN_DIR": str(paths.run_dir),
        "VALUZ_AUTOMATION_WORKSPACE_DIR": str(paths.workspace),
        "VALUZ_AUTOMATION_CTX_FILE": str(paths.ctx),
        "VALUZ_AUTOMATION_INPUT_FILE": str(paths.input),
        "VALUZ_AUTOMATION_OUTPUT_FILE": str(paths.output),
    }


def write_run_inputs(paths: CodeRunPaths, *, ctx: Mapping[str, Any], effective_input: Any) -> None:
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    (paths.run_dir / "files").mkdir(exist_ok=True)
    paths.ctx.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.input.write_text(
        json.dumps(effective_input, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copyfile(BOOTSTRAP_PATH, paths.bootstrap)
    if paths.output.exists():
        paths.output.unlink()


def build_spec(
    paths: CodeRunPaths, *, row: AutomationRow, run: AutomationRunRow, env: Mapping[str, str]
) -> CodeRunSpec:
    return CodeRunSpec(
        user_id=run.user_id,
        automation_id=row.id,
        run_id=run.id,
        runtime=row.code_runtime or "python",
        entry_path=str(paths.entry),
        project_cwd=str(paths.project_cwd),
        run_dir=str(paths.run_dir),
        workspace_dir=str(paths.workspace),
        bootstrap_path=str(paths.bootstrap),
        ctx_path=str(paths.ctx),
        output_path=str(paths.output),
        timeout_s=int(row.code_timeout_s or 600),
        env=dict(env),
    )


def trim_run_dirs(root: Path, *, keep: int) -> None:
    """Keep the newest ``keep`` run directories; the rows keep their own limit."""
    runs = root / "runs"
    if not runs.is_dir() or keep <= 0:
        return
    dirs = [p for p in runs.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in dirs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


# ── Output ────────────────────────────────────────────────────────────


def parse_wrapper(output_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """``(wrapper, problem)`` — exactly one is set."""
    if not output_json or not output_json.strip():
        return None, "the program produced no output.json"
    try:
        wrapper = json.loads(output_json)
    except ValueError as exc:
        return None, f"output.json is not valid JSON: {exc}"
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("artifact"), dict):
        return None, "output must be an object with an 'artifact' object"
    return wrapper, None


@dataclass(frozen=True, slots=True)
class DeclaredFile:
    path: Path
    name: str
    mime_type: str | None


def collect_files(wrapper: Mapping[str, Any], run_dir: Path) -> list[DeclaredFile]:
    """The files the wrapper declares, each checked to sit inside the run dir."""
    raw = wrapper.get("files")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ContractViolationError("'files' must be a list", path="files")
    if len(raw) > MAX_FILES:
        raise ContractViolationError(f"at most {MAX_FILES} files per run", path="files")
    run_real = run_dir.resolve()
    out: list[DeclaredFile] = []
    total = 0
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("sourcePath"), str):
            raise ContractViolationError(
                "each file needs a string 'sourcePath'", path=f"files.{index}"
            )
        source = Path(entry["sourcePath"])
        if not source.is_absolute():
            source = run_dir / source
        source = source.resolve()
        if run_real not in source.parents:
            raise ContractViolationError(
                "sourcePath must be inside the run directory", path=f"files.{index}.sourcePath"
            )
        if not source.is_file():
            raise ContractViolationError("file not found", path=f"files.{index}.sourcePath")
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ContractViolationError(
                f"file exceeds {MAX_FILE_BYTES} bytes; leave it in the workspace instead",
                path=f"files.{index}.sourcePath",
            )
        total += size
        if total > MAX_FILES_TOTAL_BYTES:
            raise ContractViolationError(
                f"files exceed {MAX_FILES_TOTAL_BYTES} bytes in total", path="files"
            )
        raw_name = entry.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else source.name
        raw_mime = entry.get("mimeType")
        mime = raw_mime if isinstance(raw_mime, str) else None
        out.append(DeclaredFile(path=source, name=name, mime_type=mime))
    return out


async def deliver_files(
    db: Any,
    *,
    user_id: str,
    project_id: str,
    project_cwd: Path,
    run_id: str,
    files: list[DeclaredFile],
) -> list[AutomationArtifactFile]:
    """Record each declared file as an artifact row (the run's ``files_json``)."""
    if not files:
        return []
    from valuz_agent.modules.artifacts.models import SHARED_CWD, ArtifactKind
    from valuz_agent.modules.artifacts.scope import Scope
    from valuz_agent.modules.artifacts.service import DeliveryRequest, deliver_artifact

    out: list[AutomationArtifactFile] = []
    for declared in files:
        delivered = await deliver_artifact(
            db,
            scope=Scope(user_id=user_id, project_id=project_id, worktree=SHARED_CWD),
            scope_cwd=project_cwd,
            owner_roots=[],
            request=DeliveryRequest(
                abs_path=declared.path,
                display_name=declared.name,
                kind=ArtifactKind.FILE,
                mime_type=declared.mime_type or mimetypes.guess_type(declared.name)[0],
            ),
        )
        if not delivered.ok or not delivered.artifact_id:
            raise ContractViolationError(
                f"could not record file {declared.name!r} ({delivered.status}: {delivered.detail})",
                path="files",
            )
        out.append(
            AutomationArtifactFile(
                artifact_id=delivered.artifact_id,
                name=declared.name,
                mime_type=declared.mime_type,
                size_bytes=declared.path.stat().st_size,
            )
        )
    return out


def compose_log_tail(outcome: CodeRunOutcome) -> str | None:
    parts: list[str] = []
    if outcome.stdout_tail.strip():
        parts.append(outcome.stdout_tail.rstrip())
    if outcome.stderr_tail.strip():
        parts.append("--- stderr ---\n" + outcome.stderr_tail.rstrip())
    return "\n".join(parts) if parts else None


def files_to_json(files: list[AutomationArtifactFile]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": f.artifact_id,
            "name": f.name,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
        }
        for f in files
    ]


async def fire_artifact_hooks(event: AutomationArtifactEvent) -> None:
    """Best-effort fan-out; a failing hook is logged, never raised."""
    for hook in list(ext.automation_result_hooks):
        try:
            await hook.on_artifact(event)
        except Exception:  # noqa: BLE001 — observers must not fail the run
            logger.exception(
                "automation result hook %s failed for run %s", type(hook).__name__, event.run_id
            )


# ── Cancel watch ──────────────────────────────────────────────────────


async def watch_cancel(
    *, user_id: str, automation_id: str, run_id: str, cancel: asyncio.Event
) -> None:
    """Poll the run row until a cancel request appears (or we are cancelled).

    The web process and the worker may be different processes, so the request
    travels through the row (``cancel_requested_at``), not memory.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.automations.datastore import AutomationDatastore

    while not cancel.is_set():
        await asyncio.sleep(CANCEL_POLL_S)
        try:
            async with async_unit_of_work(commit=False) as db:
                run = await AutomationDatastore(db).get_run(user_id, automation_id, run_id)
                if run is not None and run.cancel_requested_at is not None:
                    cancel.set()
                    return
        except Exception:  # noqa: BLE001 — a poll failure is not a cancel
            logger.exception("cancel watch for run %s failed; retrying", run_id)


# ── The run ───────────────────────────────────────────────────────────


async def run_code_automation(
    *,
    user_id: str,
    row: AutomationRow,
    run: AutomationRunRow,
    effective_input: str | Mapping[str, Any] | None,
    previous: AutomationRunRow | None,
    timezone: str,
    locale: str,
) -> CodeRunResult:
    """Prepare, execute, and interpret one code run. Never raises for a
    program's own failure — every outcome is a ``CodeRunResult``."""
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.db import async_unit_of_work

    try:
        async with async_unit_of_work(commit=False) as db:
            project_cwd = await resolve_project_cwd(db, user_id, row.project_id)
        paths = resolve_paths(
            project_cwd=project_cwd,
            automation_id=row.id,
            run_id=run.id,
            entry=row.code_entry or "",
        )
        ctx = build_ctx(
            row=row,
            run=run,
            paths=paths,
            effective_input=effective_input,
            previous=previous,
            timezone=timezone,
            locale=locale,
        )
        write_run_inputs(paths, ctx=ctx, effective_input=ctx["input"])
    except CodeRunPreparationError as exc:
        return CodeRunResult(status="failed", error_code=exc.code, error_message=exc.message)
    except OSError as exc:
        return CodeRunResult(
            status="failed",
            error_code="AUTOMATION_CODE_PREPARE_FAILED",
            error_message=str(exc)[:500],
        )

    spec = build_spec(paths, row=row, run=run, env=build_env(paths, row=row, run=run))
    cancel = asyncio.Event()
    watcher = asyncio.create_task(
        watch_cancel(user_id=user_id, automation_id=row.id, run_id=run.id, cancel=cancel)
    )
    try:
        outcome = await ext.automation_code_executor.execute(spec, cancel=cancel)
    except CodeExecutorUnavailableError as exc:
        return CodeRunResult(
            status="failed",
            error_code="AUTOMATION_CODE_EXECUTOR_UNAVAILABLE",
            error_message=str(exc)[:500],
        )
    except Exception as exc:  # noqa: BLE001 — the executor itself broke
        logger.exception("code executor failed for run %s", run.id)
        return CodeRunResult(
            status="failed",
            error_code="AUTOMATION_CODE_EXECUTOR_FAILED",
            error_message=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
    finally:
        watcher.cancel()

    result = CodeRunResult(
        status=outcome.status,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        executor_ref=outcome.executor_ref,
        log_tail=compose_log_tail(outcome),
        exit_code=outcome.exit_code,
    )
    if outcome.status != "success":
        if result.error_code is None:
            result.error_code = "AUTOMATION_CODE_EXIT"
            result.error_message = f"program exited with code {outcome.exit_code}"
        _trim(paths, settings.automation_run_dirs_keep)
        return result

    wrapper, problem = parse_wrapper(outcome.output_json)
    if wrapper is None:
        result.status = "failed"
        result.error_code = "AUTOMATION_OUTPUT_INVALID"
        result.error_message = problem
        _trim(paths, settings.automation_run_dirs_keep)
        return result
    try:
        artifact = validate_artifact(result_contract_of(row), wrapper["artifact"])
        declared = collect_files(wrapper, paths.run_dir)
        async with async_unit_of_work() as db:
            delivered = await deliver_files(
                db,
                user_id=user_id,
                project_id=row.project_id,
                project_cwd=paths.project_cwd,
                run_id=run.id,
                files=declared,
            )
    except ContractViolationError as exc:
        result.status = "failed"
        result.error_code = "AUTOMATION_ARTIFACT_INVALID"
        result.error_message = f"{exc.path}: {exc}" if exc.path else str(exc)
        _trim(paths, settings.automation_run_dirs_keep)
        return result
    result.artifact = artifact
    result.files = delivered
    _trim(paths, settings.automation_run_dirs_keep)
    return result


def _trim(paths: CodeRunPaths, keep: int) -> None:
    try:
        trim_run_dirs(paths.root, keep=keep)
    except OSError:
        logger.exception("could not trim run directories under %s", paths.root)


def artifact_event(
    *,
    row: AutomationRow,
    run: AutomationRunRow,
    artifact: Mapping[str, Any],
    files: list[AutomationArtifactFile],
    producer: str,
) -> AutomationArtifactEvent:
    return AutomationArtifactEvent(
        user_id=run.user_id,
        automation_id=row.id,
        run_id=run.id,
        project_id=row.project_id,
        artifact=dict(artifact),
        files=tuple(files),
        produced_at=now_ms(),
        producer=producer,
    )


__all__ = [
    "AUTOMATIONS_DIRNAME",
    "BOOTSTRAP_NAME",
    "CodeRunPaths",
    "CodeRunPreparationError",
    "CodeRunResult",
    "DeclaredFile",
    "artifact_event",
    "build_ctx",
    "build_env",
    "build_spec",
    "collect_files",
    "compose_log_tail",
    "deliver_files",
    "files_to_json",
    "fire_artifact_hooks",
    "parse_wrapper",
    "resolve_paths",
    "resolve_project_cwd",
    "run_code_automation",
    "trim_run_dirs",
    "watch_cancel",
    "write_run_inputs",
]
