"""MinerU 精准解析 plugin.

Submits one parse-task per call, polls until the remote ZIP is ready,
extracts ``full.md``, returns it as the markdown payload. Images inside
the ZIP are ignored — see ``docs/references/mineru-api.md`` for the
contract.

Locked decisions (from plan §"关键设计决策"):

- ``model_version="vlm"`` for everything except ``.html`` which uses
  ``"MinerU-HTML"``. Not exposed to the user.
- File upload uses MinerU's signed-URL flow (``/api/v4/file-urls/batch``
  → ``PUT`` → ``/api/v4/extract/task``). We never read user files into
  memory beyond the streaming ``PUT`` body.
- Result: only ``full.md`` is consumed. Images and intermediate JSON are
  ignored to keep parity with LightLocal's contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from valuz_agent.ports.parser_plugin import ParserPlugin
from plugins.parser.mineru.handler import MineruPollingHandler
from plugins.parser.mineru.plugin import MINERU_PLUGIN_ID, MineruPlugin

if TYPE_CHECKING:  # pragma: no cover
    from valuz_agent.modules.parser.polling import PollingScheduler


def make_plugin(scheduler: "PollingScheduler | None" = None) -> ParserPlugin:
    """Entry-point factory — instantiates the plugin against the host's
    ``PollingScheduler``. Declared in the host's ``pyproject.toml``
    under ``[project.entry-points."valuz.parser_plugins"]``; future
    out-of-tree plugin packages declare the same group themselves and
    ``build_default_registry`` discovers them at app startup.

    ``scheduler`` is required at runtime for the async-poll plugins;
    ``None`` is accepted for test contexts that don't drive cloud
    plugins — the resulting plugin will raise on first ``parse()``.
    """
    if scheduler is None:
        raise RuntimeError(
            "MinerU plugin requires a PollingScheduler instance"
        )
    return MineruPlugin(scheduler)


__all__ = [
    "MINERU_PLUGIN_ID",
    "MineruPlugin",
    "MineruPollingHandler",
    "make_plugin",
]
