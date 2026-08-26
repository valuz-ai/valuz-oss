"""Allow ``python -m valuz_agent`` to dispatch to ``valuz_agent.main``."""

import sys

# ``--ptc-exec`` turns this process image into the PTC fallback interpreter
# (the frozen bundle carries no python3 a subprocess could exec). Dispatch
# BEFORE the heavy server imports below: this path must never touch config,
# logging, or the data dir. See ``valuz_agent/ptc_exec.py``.
if len(sys.argv) >= 2 and sys.argv[1] == "--ptc-exec":
    from valuz_agent.ptc_exec import run_ptc_exec

    raise SystemExit(run_ptc_exec(sys.argv[2:]))

from valuz_agent.main import main  # noqa: E402 — deliberately after the dispatch

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
