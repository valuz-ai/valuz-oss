"""The ``--ptc-exec`` self-hosted interpreter mode, pinned via subprocess.

Runs ``python -m valuz_agent --ptc-exec …`` — the same dispatch the frozen
``valuz-server`` binary takes — and asserts the executor's subprocess
contract: PYTHONPATH folded into sys.path (the PyInstaller bootloader
ignores the variable, so the branch must do it), ``__main__`` semantics,
argv, exit codes, and traceback-on-stderr for failures.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run(args: list[str], *, extra_pythonpath: list[str] | None = None, cwd: Path | None = None):
    env = dict(os.environ)
    # ``-m valuz_agent`` needs the backend dir importable; the branch under
    # test additionally folds every entry into the SCRIPT's sys.path.
    entries = [str(BACKEND_DIR), *(extra_pythonpath or [])]
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return subprocess.run(
        [sys.executable, "-m", "valuz_agent", "--ptc-exec", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def test_script_runs_as_main_with_argv_and_pythonpath(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "helper_mod.py").write_text("VALUE = 41\n", encoding="utf-8")
    script = tmp_path / "prog.py"
    script.write_text(
        "import sys\n"
        "import helper_mod\n"
        "print('name', __name__)\n"
        "print('value', helper_mod.VALUE + 1)\n"
        "print('argv', sys.argv[1:])\n",
        encoding="utf-8",
    )
    proc = _run([str(script), "alpha", "beta"], extra_pythonpath=[str(lib)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "name __main__" in proc.stdout
    assert "value 42" in proc.stdout
    assert "argv ['alpha', 'beta']" in proc.stdout


def test_failing_script_exits_1_with_traceback(tmp_path: Path):
    script = tmp_path / "boom.py"
    script.write_text("raise ValueError('ptc-exec-marker')\n", encoding="utf-8")
    proc = _run([str(script)], cwd=tmp_path)
    assert proc.returncode == 1
    assert "ptc-exec-marker" in proc.stderr
    assert "Traceback" in proc.stderr


def test_sys_exit_code_is_propagated(tmp_path: Path):
    script = tmp_path / "exit7.py"
    script.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    assert _run([str(script)], cwd=tmp_path).returncode == 7


def test_missing_script_exits_2(tmp_path: Path):
    proc = _run([str(tmp_path / "absent.py")], cwd=tmp_path)
    assert proc.returncode == 2
    assert "cannot read" in proc.stderr
