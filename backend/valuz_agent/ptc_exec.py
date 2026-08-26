"""Self-hosted interpreter mode: ``valuz-server --ptc-exec <script> [args...]``.

The packaged backend is PyInstaller-frozen — there is no ``python3`` inside
the bundle for a subprocess to exec. This mode turns the bundled runtime
into the PTC fallback interpreter: the executor spawns
``sys.executable --ptc-exec <program>`` when no usable host python exists
(``kernel/src/ptc/interpreter.py`` owns the resolution ladder).

Fidelity notes (the executor's subprocess contract):

- The PyInstaller bootloader IGNORES ``PYTHONPATH``, and the executor
  delivers the generated-wrapper location through exactly that variable —
  so its entries are folded into ``sys.path`` here, behind the script's own
  directory (the order real python uses).
- The program runs as ``__main__`` with ``sys.argv = [script, *args]``; an
  uncaught exception prints its traceback to stderr and exits 1.
- Dispatch happens at the very top of ``valuz_agent/__main__`` — before any
  heavy import — so this path never touches config, logging, or the data
  dir, and adds nothing to normal server startup.

Stdlib-only on purpose: it must work identically in a source checkout
(``python -m valuz_agent --ptc-exec``, which is how the tests pin it) and
in the frozen bundle.
"""

from __future__ import annotations

import os
import sys
import traceback


def run_ptc_exec(argv: list[str]) -> int:
    """Execute ``argv[0]`` as a ``__main__`` script; returns the exit code."""
    if not argv:
        print("--ptc-exec requires a script path", file=sys.stderr)
        return 2
    script, *script_args = argv

    try:
        with open(script, encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        print(f"--ptc-exec: cannot read {script!r}: {exc}", file=sys.stderr)
        return 2

    # Final order: [script dir, *PYTHONPATH entries, *existing sys.path].
    entries = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    script_dir = os.path.dirname(os.path.abspath(script))
    for path in [*reversed(entries), script_dir]:
        if path not in sys.path:
            sys.path.insert(0, path)

    # Approximate ``python -u``'s promptness for the timeout-kill case: a
    # killed program should not lose everything it printed.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass

    sys.argv = [script, *script_args]
    module_globals: dict[str, object] = {
        "__name__": "__main__",
        "__file__": script,
        "__builtins__": __builtins__,
    }
    try:
        code = compile(source, script, "exec")
        exec(code, module_globals)  # noqa: S102 — this IS the interpreter mode
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    except BaseException:  # noqa: BLE001 — faithful interpreter behavior
        traceback.print_exc()
        return 1
    return 0
