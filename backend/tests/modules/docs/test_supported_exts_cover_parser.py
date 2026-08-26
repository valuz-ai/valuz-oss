"""Pin the three extension tables that must move together.

Local parsing capability is declared in three places, each with a different
job, and nothing but this test stops them from drifting apart:

* ``LightLocalParser.capabilities`` — what the parser can actually read.
* ``docs.service.SUPPORTED_EXTS`` — the knowledge-base ingestion gate. A
  format missing here is **silently skipped** by the directory scan, so
  widening the parser without widening this leaves the new capability
  unreachable from the KB with no error anywhere.
* ``parser.router.classify`` — the routing bucket. A format the parser
  handles but the router files under ``text`` can never reach a cloud
  plugin, and reads oddly next to ``.md``.
"""

from __future__ import annotations

import pytest

from valuz_agent.integrations.parser_light_local import LightLocalParser
from valuz_agent.modules.docs.service import SUPPORTED_EXTS
from valuz_agent.modules.parser.router import classify

# Formats the local parser reads but the KB deliberately does not ingest.
# Empty today; an entry here is a decision, not an oversight.
_INGESTION_EXCLUDED: frozenset[str] = frozenset()


def test_kb_ingests_everything_the_local_parser_can_read() -> None:
    parseable = {f".{ext}" for ext in LightLocalParser().capabilities}
    missing = parseable - SUPPORTED_EXTS - _INGESTION_EXCLUDED
    assert not missing, (
        f"LightLocalParser reads {sorted(missing)} but docs.SUPPORTED_EXTS "
        f"does not accept them — the KB directory scan will skip these files "
        f"silently. Add them to SUPPORTED_EXTS, or to _INGESTION_EXCLUDED with "
        f"a reason."
    )


@pytest.mark.parametrize(
    ("filename", "expected_kind"),
    [
        ("报告.doc", "office"),
        ("报告.docx", "office"),
        ("报告.docm", "office"),
        ("讲义.ppt", "office"),
        ("讲义.pptx", "office"),
        ("讲义.pptm", "office"),
        ("纪要.odt", "office"),
        ("演示.odp", "office"),
        ("说明.rtf", "office"),
        ("手册.epub", "office"),
        ("模型.xls", "spreadsheet"),
        ("模型.xlsx", "spreadsheet"),
        ("模型.xlsm", "spreadsheet"),
        ("模型.xlsb", "spreadsheet"),
        ("模型.ods", "spreadsheet"),
        # Unchanged buckets — guard against an over-broad edit.
        ("扫描件.pdf", "pdf"),
        ("图.png", "image"),
        ("页面.html", "web"),
        ("持仓.csv", "text"),
        ("笔记.md", "text"),
    ],
)
def test_classify_buckets_documents_by_what_they_are(filename: str, expected_kind: str) -> None:
    assert classify(filename) == expected_kind


def test_every_office_and_spreadsheet_ext_is_parseable() -> None:
    """The other direction: the router must not route a format to a bucket
    LightLocal cannot serve, since LightLocal is the universal fallback."""
    parseable = {f".{ext}" for ext in LightLocalParser().capabilities}
    for filename in ("a.doc", "a.odt", "a.rtf", "a.epub", "a.ods", "a.xlsb"):
        ext = filename[filename.rindex(".") :]
        assert ext in parseable, f"router routes {ext} but LightLocal cannot parse it"


# ---------------------------------------------------------------------------
# Retired-engine requeue — the upgrade path for documents already in a KB
# ---------------------------------------------------------------------------


def test_retired_engines_are_requeued_despite_unchanged_plugin() -> None:
    """``_run_rescan``'s Trigger 3 compares PLUGIN ids, and every LightLocal
    engine maps to ``light_local`` — so swapping MarkItDown for anydoc is
    invisible to it. Without ``_RETIRED_ENGINE_FOR_KIND`` the migration would
    never reach documents already in a KB: a docx parsed by MarkItDown keeps
    its pandas artifacts, and an ``.rtf`` keeps the raw control words it was
    polluted with, forever.
    """
    from valuz_agent.modules.docs.service import (
        _RETIRED_ENGINE_FOR_KIND,
        _engine_to_plugin_id,
    )

    stale = [("报告.docx", "markitdown"), ("模型.xlsx", "markitdown"), ("说明.rtf", "plain_text")]
    for filename, engine in stale:
        kind = classify(filename)
        # Precondition: Trigger 3 cannot see this — both sides are light_local.
        assert _engine_to_plugin_id(engine) == "light_local"
        # So Trigger 4 must.
        assert (engine, kind) in _RETIRED_ENGINE_FOR_KIND, (
            f"{filename} parsed by {engine!r} would never be re-parsed onto anydoc"
        )


def test_current_engines_are_not_requeued() -> None:
    """The other direction — a retired-engine entry that matches a LIVE engine
    would requeue every matching doc on every rescan tick, forever."""
    from valuz_agent.modules.docs.service import _RETIRED_ENGINE_FOR_KIND

    live = [
        ("anydoc", "office"),
        ("anydoc", "spreadsheet"),
        ("pymupdf4llm", "pdf"),
        ("rapidocr", "image"),
        ("html_to_markdown", "web"),
        # plain_text is retired for ``office`` (the .rtf pollution) but is
        # still the live engine for .md/.txt/.csv, which classify as ``text``.
        ("plain_text", "text"),
    ]
    for pair in live:
        assert pair not in _RETIRED_ENGINE_FOR_KIND, f"{pair} would rescan-loop forever"


# ---------------------------------------------------------------------------
# Ingestion gate — mirrors the parser's fallback, it is NOT an allow-list
# ---------------------------------------------------------------------------


def test_declared_document_types_are_ingestible() -> None:
    """A declared extension wins outright — no sniffing, no content needed."""
    from valuz_agent.modules.docs.service import SUPPORTED_EXTS, is_ingestible

    for ext in SUPPORTED_EXTS:
        assert is_ingestible(f"研报{ext}", b"\x00\x01binary-but-declared"), (
            f"{ext} is a declared document type; content must not override that"
        )


def test_undeclared_text_files_are_ingestible() -> None:
    """The point of mirroring the parser: source code and other undeclared
    plain-text formats are read as text, not refused. Rejecting them would
    contradict ``LightLocalParser``'s own unknown-extension fallback."""
    from valuz_agent.modules.docs.service import is_ingestible

    cases = [
        ("analyze.py", "# 计算估值\ndef pe(price, eps):\n    return price / eps\n"),
        ("main.go", "package main\n"),
        ("deploy.sh", "#!/bin/sh\nset -eu\n"),
        ("config.yaml", "model: opus\n"),
        ("server.log", "2026-08-06 boot ok\n"),
        ("NOTES", "no extension at all, still text\n"),
    ]
    for filename, text in cases:
        assert is_ingestible(filename, text.encode("utf-8")), f"{filename} should read as text"


def test_binary_payloads_are_refused() -> None:
    """Only genuinely unreadable files are refused — these would have been
    written to the KB folder and then silently skipped by the scan."""
    from valuz_agent.modules.docs.service import is_ingestible

    assert not is_ingestible("archive.zip", b"PK\x03\x04\x00\x00binary")
    assert not is_ingestible("app.exe", b"MZ\x90\x00\x03\x00")
    assert not is_ingestible("clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    # Invalid UTF-8 with no NUL — caught by the strict decode, not the NUL test.
    assert not is_ingestible("mystery.bin", b"\xff\xfe\xfd\xfc")


def test_multibyte_char_split_at_the_sniff_boundary_is_not_corruption() -> None:
    """A 3-byte CJK character straddling the 64 KiB cut must not read as a
    decode failure — that would refuse a large Chinese text file at random
    depending on where its characters happen to land."""
    from valuz_agent.modules.docs.service import _TEXT_SNIFF_BYTES, is_text_payload

    filler = "报" * (_TEXT_SNIFF_BYTES // 3)
    data = filler.encode("utf-8")
    # Cut one byte into the final character, and declare more data follows.
    assert is_text_payload(data[: _TEXT_SNIFF_BYTES - 1] + b"\xe6", complete=False)


def test_ingestion_gate_is_case_insensitive() -> None:
    """Windows and macOS both hand back upper-case extensions routinely."""
    from valuz_agent.modules.docs.service import is_ingestible

    for filename in ("REPORT.DOCX", "Model.XlsX", "Deck.PPTX", "Note.RTF"):
        assert is_ingestible(filename, b"\x00binary"), f"{filename} rejected on case alone"
