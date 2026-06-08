"""Kernel-behavior coverage for the attachment source/parsed split.

These pin the vendored-kernel changes that let the agent see BOTH the original
file and its parsed text extract:

  * ``build_user_prompt`` lists ``source_path`` for every attachment and appends
    ``(extracted text: <parsed_path>)`` only when a parse exists.
  * the store converters round-trip ``{source_path, parsed_path}`` and still read
    legacy rows that carry a single ``filepath`` key (back-compat).

The kernel is vendored read-only and ships without its own tests here, so this
host-side module guards the behavior the host now depends on.
"""

from __future__ import annotations

# ruff: noqa: I001 — the boot.kernel side-effect import MUST precede ``from
# src.*`` (it injects the vendored kernel onto sys.path); isort would reorder it.

from datetime import datetime

# Side-effect import — puts the vendored kernel on sys.path so ``src.*`` resolves
# before the kernel imports below run.
import valuz_agent.boot.kernel  # noqa: F401
from src.adapters.sqlalchemy_store.converters import (  # type: ignore[import-not-found]
    dict_to_user_message,
    user_message_to_dict,
)
from src.core.prompt_builder import build_user_prompt  # type: ignore[import-not-found]
from src.core.types import Attachment, UserMessage  # type: ignore[import-not-found]

_NOW = datetime(2026, 6, 8, 9, 30)


def test_attachment_defaults_parsed_to_none() -> None:
    a = Attachment(source_path="/raw.pdf")
    assert a.source_path == "/raw.pdf"
    assert a.parsed_path is None


# ---------------------------------------------------------------------------
# build_user_prompt — render the original, with the extract alongside
# ---------------------------------------------------------------------------


def test_prompt_renders_source_with_parsed_extract() -> None:
    msg = UserMessage(
        text="summarize it",
        attachments=(Attachment(source_path="/ws/report.pdf", parsed_path="/ws/report.md"),),
    )
    out = build_user_prompt(msg, cwd="/ws", now=_NOW)
    assert "- /ws/report.pdf  (extracted text: /ws/report.md)" in out
    # The original path is what the agent acts on — it must be present verbatim.
    assert "/ws/report.pdf" in out


def test_prompt_renders_source_only_when_unparsed() -> None:
    msg = UserMessage(
        text="rename this",
        attachments=(Attachment(source_path="/ws/raw.bin"),),
    )
    out = build_user_prompt(msg, cwd="/ws", now=_NOW)
    assert "- /ws/raw.bin" in out
    assert "extracted text" not in out


# ---------------------------------------------------------------------------
# converters — round-trip both fields + legacy-filepath back-compat
# ---------------------------------------------------------------------------


def test_converter_round_trip_preserves_both_paths() -> None:
    msg = UserMessage(
        text="hi",
        attachments=(
            Attachment(source_path="/a.pdf", parsed_path="/a.md"),
            Attachment(source_path="/b.txt"),
        ),
    )
    restored = dict_to_user_message(user_message_to_dict(msg))
    assert restored.attachments == msg.attachments


def test_converter_reads_legacy_filepath_as_source() -> None:
    # Rows persisted before the split stored a single ``filepath``.
    legacy = {"text": "old turn", "attachments": [{"filepath": "/legacy.pdf"}]}
    restored = dict_to_user_message(legacy)
    assert restored.attachments == (Attachment(source_path="/legacy.pdf", parsed_path=None),)


def test_converter_coerces_empty_parsed_to_none() -> None:
    restored = dict_to_user_message(
        {"text": "t", "attachments": [{"source_path": "/x", "parsed_path": ""}]}
    )
    assert restored.attachments[0].parsed_path is None
