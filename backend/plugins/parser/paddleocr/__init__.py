"""PaddleOCR-VL plugin (async-poll).

The hosted PaddleOCR job API is asynchronous (POST → poll → JSONL),
so this plugin runs in ``ASYNC_POLL`` mode through the shared
``PollingScheduler`` — same shape as MinerU. Endpoint and model name
are fixed constants; the only user config is the API token plus a few
processing toggles.

Locked decisions:

- Endpoint: ``https://paddleocr.aistudio-app.com/api/v2/ocr/jobs``
- Model: ``PaddleOCR-VL-1.6`` (highest accuracy, not user-switchable)
- Supports ``pdf`` + ``image`` only; Office / Excel / HTML route to
  LightLocal via the router's capability gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plugins.parser.paddleocr.handler import PaddleOcrPollingHandler
from plugins.parser.paddleocr.plugin import (
    PADDLEOCR_PLUGIN_ID,
    PaddleOcrPlugin,
)
from valuz_agent.ports.parser_plugin import ParserPlugin

if TYPE_CHECKING:  # pragma: no cover
    from valuz_agent.modules.parser.polling import PollingScheduler


def make_plugin(scheduler: PollingScheduler | None = None) -> ParserPlugin:
    """Entry-point factory. See ``parser_mineru.make_plugin`` for the
    contract — async-poll plugins both require a live scheduler."""
    if scheduler is None:
        raise RuntimeError("PaddleOCR plugin requires a PollingScheduler instance")
    return PaddleOcrPlugin(scheduler)


__all__ = [
    "PADDLEOCR_PLUGIN_ID",
    "PaddleOcrPlugin",
    "PaddleOcrPollingHandler",
    "make_plugin",
]
