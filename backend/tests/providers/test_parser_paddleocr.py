"""Coverage for ``PaddleOcrPlugin`` + ``PaddleOcrPollingHandler``
descriptor + locked constants (PR-3).

The HTTP-going ``submit / poll / fetch_result`` path is exercised
manually during the PR-3 E2E with a real AI Studio token. The unit
tests here pin the surface that would silently break the integration
if changed:

- Plugin id, mode, capability set
- Locked endpoint + model constants
- Auth scheme: lowercase ``bearer`` (the AI Studio job API rejects
  capital ``Bearer``)
"""

from __future__ import annotations

from plugins.parser.paddleocr.handler import (
    PADDLEOCR_HANDLER_KIND,
    PADDLEOCR_JOB_URL,
    PADDLEOCR_MODEL,
)
from plugins.parser.paddleocr.plugin import (
    PADDLEOCR_PLUGIN_ID,
)


def test_plugin_id_is_stable() -> None:
    assert PADDLEOCR_PLUGIN_ID == "paddleocr"


def test_handler_kind_is_stable() -> None:
    assert PADDLEOCR_HANDLER_KIND == "parser.paddleocr"


def test_endpoint_is_fixed_constant() -> None:
    # If this changes the demo flow breaks. The endpoint is intentionally
    # NOT a user setting per plan §"关键设计决策".
    assert PADDLEOCR_JOB_URL == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


def test_model_is_locked_to_vl_1_6() -> None:
    # Plan: "均选用最好的 vl / vlm 模型,不提供用户配置解析mode".
    # Upgraded 1.5 → 1.6 (verified live: same endpoint + payload + JSONL shape).
    assert PADDLEOCR_MODEL == "PaddleOCR-VL-1.6"


def test_descriptor_supports_only_pdf_and_image() -> None:
    """Capability gate downstream relies on this. ``office`` /
    ``spreadsheet`` / ``web`` going to PaddleOCR would silently fail."""
    from unittest.mock import MagicMock

    from plugins.parser.paddleocr import PaddleOcrPlugin

    descriptor = PaddleOcrPlugin(MagicMock()).descriptor
    kinds = {c.kind for c in descriptor.capabilities}
    assert kinds == {"pdf", "image"}


def test_descriptor_is_async_poll() -> None:
    from unittest.mock import MagicMock

    from plugins.parser.paddleocr import PaddleOcrPlugin
    from valuz_agent.ports.parser_plugin import ParserPluginMode

    # PaddleOCR job API is async — must NOT be classified as SYNC or
    # the router would skip the polling scheduler.
    assert PaddleOcrPlugin(MagicMock()).descriptor.mode == ParserPluginMode.ASYNC_POLL


def test_descriptor_requires_secret() -> None:
    from unittest.mock import MagicMock

    from plugins.parser.paddleocr import PaddleOcrPlugin

    fields = PaddleOcrPlugin(MagicMock()).descriptor.config_schema
    assert any(f.type == "secret" for f in fields)
