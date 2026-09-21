"""Host side of a code run: paths, wrapper parsing, file rules, the run itself."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations import code_runner as cr
from valuz_agent.modules.automations.contracts import ContractViolationError
from valuz_agent.ports.automation_code_executor import (
    CodeExecutorUnavailableError,
    CodeRunOutcome,
    CodeRunSpec,
)
from valuz_agent.ports.extensions import ext


def _row(**over: Any) -> Any:
    base = dict(
        id="auto-1",
        name="daily",
        project_id="proj-1",
        user_id="u1",
        execution_kind="code",
        code_runtime="python",
        code_entry="automations/daily/automation.py",
        code_timeout_s=30,
        input_kind="json",
        result_kind="artifact",
        result_schema={"type": "object", "required": ["summary"]},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _run(**over: Any) -> Any:
    base = dict(
        id="run-1",
        user_id="u1",
        trigger_type="manual",
        invoked_by_ref=None,
        invoked_by_session_id=None,
        triggered_at=1000,
        input_json={"symbol": "SH:600519"},
        extra_input=None,
        completed_at=None,
        artifact_json=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "automations" / "daily").mkdir(parents=True)
    (project / "automations" / "daily" / "automation.py").write_text(
        "def run(ctx):\n    return {'artifact': {'summary': 'ok'}}\n", encoding="utf-8"
    )
    return project


class TestResolvePaths:
    def test_lays_out_platform_tree(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        paths = cr.resolve_paths(
            project_cwd=project,
            automation_id="auto-1",
            run_id="run-1",
            entry="automations/daily/automation.py",
        )
        assert paths.entry == (project / "automations/daily/automation.py").resolve()
        assert paths.run_dir == project / ".valuz/automations/auto-1/runs/run-1"
        assert paths.workspace == project / ".valuz/automations/auto-1/workspace"
        assert paths.bootstrap.name == cr.BOOTSTRAP_NAME

    def test_missing_entry(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        with pytest.raises(cr.CodeRunPreparationError) as excinfo:
            cr.resolve_paths(project_cwd=project, automation_id="a", run_id="r", entry="nope.py")
        assert excinfo.value.code == "AUTOMATION_CODE_ENTRY_MISSING"

    def test_entry_escaping_via_symlink_is_refused(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        outside = tmp_path / "outside.py"
        outside.write_text("def run(ctx): return {'artifact': {}}\n", encoding="utf-8")
        (project / "link.py").symlink_to(outside)
        with pytest.raises(cr.CodeRunPreparationError) as excinfo:
            cr.resolve_paths(project_cwd=project, automation_id="a", run_id="r", entry="link.py")
        assert excinfo.value.code == "AUTOMATION_CODE_ENTRY_INVALID"


class TestCtxAndInputs:
    def test_ctx_and_files_written(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        row, run = _row(), _run()
        paths = cr.resolve_paths(
            project_cwd=project, automation_id=row.id, run_id=run.id, entry=row.code_entry
        )
        previous = _run(id="run-0", completed_at=900, artifact_json={"summary": "old"})
        ctx = cr.build_ctx(
            row=row,
            run=run,
            paths=paths,
            effective_input={"symbol": "SH:600519"},
            previous=previous,
            timezone="Asia/Shanghai",
            locale="zh-CN",
        )
        assert ctx["input"] == {"symbol": "SH:600519"}
        assert ctx["previous"] == {
            "runId": "run-0",
            "completedAt": 900,
            "artifact": {"summary": "old"},
        }
        assert ctx["projectDir"] == str(project)
        assert ctx["trigger"]["type"] == "manual"
        cr.write_run_inputs(paths, ctx=ctx, effective_input=ctx["input"])
        assert json.loads(paths.ctx.read_text())["runId"] == "run-1"
        assert json.loads(paths.input.read_text()) == {"symbol": "SH:600519"}
        assert paths.bootstrap.read_text().startswith("#!/usr/bin/env python3")
        env = cr.build_env(paths, row=row, run=run)
        assert env["VALUZ_AUTOMATION_OUTPUT_FILE"] == str(paths.output)
        assert env["VALUZ_AUTOMATION_RUN_ID"] == "run-1"


class TestWrapperAndFiles:
    def test_parse_wrapper(self) -> None:
        assert cr.parse_wrapper(None)[0] is None
        assert cr.parse_wrapper("{not json")[0] is None
        assert cr.parse_wrapper('{"artifact": 1}')[0] is None
        wrapper, problem = cr.parse_wrapper('{"artifact": {"a": 1}}')
        assert wrapper == {"artifact": {"a": 1}} and problem is None

    def test_collect_files_rules(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        (run_dir / "files").mkdir(parents=True)
        good = run_dir / "files" / "report.csv"
        good.write_text("a,b\n", encoding="utf-8")
        declared = cr.collect_files(
            {
                "artifact": {},
                "files": [
                    {"sourcePath": str(good)},
                    {"sourcePath": "files/report.csv", "name": "r.csv", "mimeType": "text/csv"},
                ],
            },
            run_dir,
        )
        assert [d.name for d in declared] == ["report.csv", "r.csv"]
        assert declared[1].mime_type == "text/csv"
        outside = tmp_path / "secret.txt"
        outside.write_text("x", encoding="utf-8")
        with pytest.raises(ContractViolationError):
            cr.collect_files({"files": [{"sourcePath": str(outside)}]}, run_dir)
        with pytest.raises(ContractViolationError):
            cr.collect_files({"files": [{"sourcePath": "files/missing.txt"}]}, run_dir)
        with pytest.raises(ContractViolationError):
            cr.collect_files({"files": "nope"}, run_dir)

    def test_trim_run_dirs_keeps_newest(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        runs = root / "runs"
        import os
        import time

        for i in range(5):
            d = runs / f"run-{i}"
            d.mkdir(parents=True)
            stamp = time.time() - (5 - i) * 10
            os.utime(d, (stamp, stamp))
        cr.trim_run_dirs(root, keep=2)
        assert sorted(p.name for p in runs.iterdir()) == ["run-3", "run-4"]


class _FakeExecutor:
    def __init__(
        self,
        outcome: CodeRunOutcome | Exception,
        *,
        write_output: str | None = None,
        write_files: dict[str, bytes] | None = None,
    ):
        self._outcome = outcome
        self._write = write_output
        self._write_files = write_files or {}
        self.spec: CodeRunSpec | None = None

    async def execute(self, spec: CodeRunSpec, *, cancel: asyncio.Event) -> CodeRunOutcome:
        self.spec = spec
        for rel, data in self._write_files.items():  # what the program "wrote" into the run dir
            target = Path(spec.run_dir) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if self._write is not None:
            Path(spec.output_path).write_text(self._write, encoding="utf-8")
        if isinstance(self._outcome, Exception):
            raise self._outcome
        self._outcome.output_json = self._write
        return self._outcome


@asynccontextmanager
async def _fake_uow(*args: Any, **kwargs: Any):
    yield Mock()


@pytest.fixture
def quiet_cancel_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(**kwargs: Any) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(cr, "watch_cancel", _noop)


@pytest.mark.asyncio
async def test_run_code_automation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)
    executor = _FakeExecutor(
        CodeRunOutcome(status="success", exit_code=0, executor_ref="local:1", stdout_tail="hi"),
        write_output=json.dumps({"artifact": {"summary": "ok", "n": 1}}),
    )
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input={"symbol": "X"},
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "success", result
    assert result.artifact == {"summary": "ok", "n": 1}
    assert result.executor_ref == "local:1"
    assert result.log_tail == "hi"
    assert result.result_summary == "ok"
    assert executor.spec is not None
    assert executor.spec.project_cwd == str(project)
    assert executor.spec.timeout_s == 30
    assert Path(executor.spec.ctx_path).exists()


@pytest.mark.asyncio
async def test_run_code_automation_artifact_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)
    executor = _FakeExecutor(
        CodeRunOutcome(status="success", exit_code=0, executor_ref="local:1"),
        write_output=json.dumps({"artifact": {"nope": 1}}),
    )
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "failed"
    assert result.error_code == "AUTOMATION_ARTIFACT_INVALID"
    assert "summary" in (result.error_message or "")


@pytest.mark.asyncio
async def test_run_code_automation_executor_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        ext, "automation_code_executor", _FakeExecutor(CodeExecutorUnavailableError("cloud"))
    )
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "failed"
    assert result.error_code == "AUTOMATION_CODE_EXECUTOR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_run_code_automation_timeout_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)
    executor = _FakeExecutor(
        CodeRunOutcome(
            status="timeout",
            exit_code=None,
            executor_ref="local:9",
            error_code="AUTOMATION_CODE_TIMEOUT",
            error_message="too slow",
            stderr_tail="killed",
        )
    )
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "timeout"
    assert result.error_code == "AUTOMATION_CODE_TIMEOUT"
    assert "stderr" in (result.log_tail or "")


@pytest.mark.asyncio
async def test_run_code_automation_missing_entry_fails_before_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)
    executor = _FakeExecutor(CodeRunOutcome(status="success", exit_code=0, executor_ref="x"))
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(code_entry="missing.py"),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "failed"
    assert result.error_code == "AUTOMATION_CODE_ENTRY_MISSING"
    assert executor.spec is None


@pytest.mark.asyncio
async def test_fire_artifact_hooks_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Good:
        async def on_artifact(self, event: Any) -> None:
            seen.append(event.run_id)

    class _Bad:
        async def on_artifact(self, event: Any) -> None:
            raise RuntimeError("nope")

    monkeypatch.setattr(ext, "automation_result_hooks", [_Bad(), _Good()])
    event = cr.artifact_event(
        row=_row(), run=_run(), artifact={"summary": "x"}, files=[], producer="code"
    )
    await cr.fire_artifact_hooks(event)
    assert seen == ["run-1"]


class _Delivered:
    def __init__(
        self, *, ok: bool, artifact_id: str | None = None, status: str = "recorded", detail=None
    ):
        self.ok = ok
        self.artifact_id = artifact_id
        self.status = status
        self.detail = detail


@pytest.mark.asyncio
async def test_declared_files_are_delivered_with_the_project_as_owner_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    """Desktop, 2026-09-21: every declared file came back ``not_owned`` because
    the owner boundary was handed no roots — the project IS the root."""
    project = _project(tmp_path)
    seen: dict[str, Any] = {}

    async def fake_deliver(db, *, scope, scope_cwd, owner_roots, request, **kw):
        seen["owner_roots"] = owner_roots
        seen["scope_cwd"] = scope_cwd
        seen["abs_path"] = request.abs_path
        return _Delivered(ok=True, artifact_id="art-1")

    monkeypatch.setattr("valuz_agent.modules.artifacts.service.deliver_artifact", fake_deliver)
    run_dir = project / ".valuz/automations/auto-1/runs/run-1"
    executor = _FakeExecutor(
        CodeRunOutcome(status="success", exit_code=0, executor_ref="local:1"),
        write_output=json.dumps(
            {
                "artifact": {"summary": "ok"},
                "files": [{"sourcePath": "files/report.json", "name": "report.json"}],
            }
        ),
        write_files={"files/report.json": b"{}"},
    )
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "success", result
    assert seen["owner_roots"] == [project]
    assert seen["scope_cwd"] == project
    assert Path(seen["abs_path"]).resolve() == (run_dir / "files/report.json").resolve()
    assert [f.artifact_id for f in result.files] == ["art-1"]
    assert result.files[0].error is None


@pytest.mark.asyncio
async def test_a_file_the_host_cannot_record_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiet_cancel_watch: None
) -> None:
    project = _project(tmp_path)

    async def fake_deliver(db, **kw):
        return _Delivered(ok=False, status="not_owned")

    monkeypatch.setattr("valuz_agent.modules.artifacts.service.deliver_artifact", fake_deliver)
    executor = _FakeExecutor(
        CodeRunOutcome(status="success", exit_code=0, executor_ref="local:1", stdout_tail="hi"),
        write_output=json.dumps(
            {
                "artifact": {"summary": "ok"},
                "files": [{"sourcePath": "files/report.json", "name": "report.json"}],
            }
        ),
        write_files={"files/report.json": b"{}"},
    )
    monkeypatch.setattr(ext, "automation_code_executor", executor)
    monkeypatch.setattr(cr, "resolve_project_cwd", AsyncMock(return_value=project))
    with patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow):
        result = await cr.run_code_automation(
            user_id="u1",
            row=_row(),
            run=_run(),
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
    assert result.status == "success", result
    assert result.error_code is None
    assert result.artifact == {"summary": "ok"}
    assert result.files[0].artifact_id is None
    assert result.files[0].error == "not_owned"
    assert "could not record file 'report.json'" in (result.log_tail or "")
    assert result.log_tail.startswith("hi\n[host]")
    assert cr.files_to_json(result.files)[0]["error"] == "not_owned"
