"""Files the host ships into a code automation's run directory."""

from __future__ import annotations

from pathlib import Path

#: The stdlib-only bootstrap that turns ``run(ctx)`` into ``output.json``.
BOOTSTRAP_PATH = Path(__file__).with_name("valuz_automation_runner.py")

__all__ = ["BOOTSTRAP_PATH"]
