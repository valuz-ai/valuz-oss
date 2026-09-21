"""The desktop executor: real subprocesses, real bootstrap, real kills."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

from valuz_agent.ports.automation_code_executor import (
    CodeExecutorUnavailableError,
    CodeRunSpec,
    LocalSubprocessCodeExecutor,
    base_environment,
    command_for,
)
from valuz_agent.resources.automation_runtime import BOOTSTRAP_PATH

pytestmark = pytest.mark.asyncio


def _spec(
    tmp_path: Path, entry_body: str, *, runtime: str = "python", timeout_s: int = 30
) -> CodeRunSpec:
    project = tmp_path / "project"
    run_dir = project / ".valuz" / "automations" / "auto-1" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    entry = project / ("automation.py" if runtime == "python" else "automation.sh")
    entry.write_text(entry_body, encoding="utf-8")
    bootstrap = run_dir / "_valuz_runner.py"
    shutil.copyfile(BOOTSTRAP_PATH, bootstrap)
    ctx = run_dir / "ctx.json"
    ctx.write_text(json.dumps({"input": {"n": 2}, "runDir": str(run_dir)}), encoding="utf-8")
    return CodeRunSpec(
        user_id="u1",
        automation_id="auto-1",
        run_id="run-1",
        runtime=runtime,
        entry_path=str(entry),
        project_cwd=str(project),
        run_dir=str(run_dir),
        workspace_dir=str(run_dir.parent.parent / "workspace"),
        bootstrap_path=str(bootstrap),
        ctx_path=str(ctx),
        output_path=str(run_dir / "output.json"),
        timeout_s=timeout_s,
        env={
            "VALUZ_AUTOMATION_OUTPUT_FILE": str(run_dir / "output.json"),
            "VALUZ_AUTOMATION_RUN_ID": "run-1",
        },
    )


def _executor() -> LocalSubprocessCodeExecutor:
    return LocalSubprocessCodeExecutor(python=sys.executable, deployment_type="local")


async def test_success_writes_output_and_logs(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "import os\n"
        "def run(ctx):\n"
        "    print('hello from program')\n"
        "    assert os.environ['VALUZ_AUTOMATION_RUN_ID'] == 'run-1'\n"
        "    return {'artifact': {'summary': 'n=%d' % ctx['input']['n']}}\n",
    )
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "success", outcome
    assert outcome.exit_code == 0
    assert outcome.executor_ref.startswith("local:")
    assert json.loads(outcome.output_json or "")["artifact"] == {"summary": "n=2"}
    assert "hello from program" in outcome.stdout_tail
    assert (Path(spec.run_dir) / "stdout.log").exists()


async def test_async_run_is_awaited(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "async def run(ctx):\n    return {'artifact': {'summary': 'async'}}\n",
    )
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "success"
    assert json.loads(outcome.output_json or "")["artifact"]["summary"] == "async"


async def test_output_file_written_by_program_wins(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "import os, json\n"
        "def run(ctx):\n"
        "    with open(os.environ['VALUZ_AUTOMATION_OUTPUT_FILE'], 'w') as fh:\n"
        "        json.dump({'artifact': {'summary': 'from-file'}}, fh)\n"
        "    return {'artifact': {'summary': 'from-return'}}\n",
    )
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "success"
    assert json.loads(outcome.output_json or "")["artifact"]["summary"] == "from-file"


async def test_contract_violation_exits_2(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "def run(ctx):\n    return ['not', 'an', 'object']\n")
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "failed"
    assert outcome.exit_code == 2
    assert "artifact" in outcome.stderr_tail


async def test_missing_run_exits_2(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "x = 1\n")
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "failed"
    assert outcome.exit_code == 2
    assert "run(ctx)" in outcome.stderr_tail


async def test_entry_exception_exits_3_with_traceback(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "def run(ctx):\n    raise RuntimeError('boom')\n")
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "failed"
    assert outcome.exit_code == 3
    assert "RuntimeError: boom" in outcome.stderr_tail


async def test_timeout_kills_the_process_group(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "import time\ndef run(ctx):\n    time.sleep(30)\n    return {'artifact': {}}\n",
        timeout_s=1,
    )
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "timeout"
    assert outcome.error_code == "AUTOMATION_CODE_TIMEOUT"
    assert outcome.output_json is None


async def test_cancel_event_stops_the_program(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "import time\ndef run(ctx):\n    time.sleep(30)\n    return {'artifact': {}}\n",
        timeout_s=30,
    )
    cancel = asyncio.Event()

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.5)
        cancel.set()

    task = asyncio.create_task(_cancel_soon())
    outcome = await _executor().execute(spec, cancel=cancel)
    await task
    assert outcome.status == "cancelled"
    assert outcome.error_code == "AUTOMATION_CANCELLED"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
async def test_shell_runtime_writes_output_file(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "#!/bin/bash\n"
        'echo \'{"artifact": {"summary": "shell"}}\' > "$VALUZ_AUTOMATION_OUTPUT_FILE"\n',
        runtime="shell",
    )
    outcome = await _executor().execute(spec, cancel=asyncio.Event())
    assert outcome.status == "success", outcome
    assert json.loads(outcome.output_json or "")["artifact"]["summary"] == "shell"


async def test_cloud_deployment_refuses_to_run(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "def run(ctx):\n    return {'artifact': {}}\n")
    executor = LocalSubprocessCodeExecutor(python=sys.executable, deployment_type="cloud")
    with pytest.raises(CodeExecutorUnavailableError):
        await executor.execute(spec, cancel=asyncio.Event())
    assert not (Path(spec.run_dir) / "stdout.log").exists()


async def test_missing_interpreter_is_a_typed_failure(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "def run(ctx):\n    return {'artifact': {}}\n")
    executor = LocalSubprocessCodeExecutor(python="", deployment_type="local")
    executor.resolve_interpreter = lambda runtime: None  # type: ignore[method-assign]
    outcome = await executor.execute(spec, cancel=asyncio.Event())
    assert outcome.status == "failed"
    assert outcome.error_code == "AUTOMATION_RUNTIME_UNAVAILABLE"


async def test_base_environment_drops_host_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALUZ_DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("SOME_TOKEN", "x")
    env = base_environment()
    assert "VALUZ_DATABASE_URL" not in env
    assert "SOME_TOKEN" not in env
    assert "PATH" in env


async def test_command_for_python_and_shell(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "")
    argv = command_for(spec, interpreter="python3")
    assert argv[:2] == ["python3", spec.bootstrap_path]
    assert argv[argv.index("--entry") + 1] == spec.entry_path
    shell_spec = _spec(tmp_path / "s", "", runtime="shell")
    assert command_for(shell_spec, interpreter="bash") == ["bash", shell_spec.entry_path]
