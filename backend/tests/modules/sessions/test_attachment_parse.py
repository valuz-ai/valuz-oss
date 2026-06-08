"""E2E coverage for the async conversation-attachment parse pipeline.

Pins the contract behind the non-blocking upload model AND the
configured-parser fix:

  * ``_attachment_specs`` (the runtime-facing path picker) always carries the
    original ``stored_path`` as ``source_path`` and adds the parsed markdown as
    ``parsed_path`` only when ``parse_status == "ready"`` — so a turn sent *while
    a file is still parsing* (or after a parse failure) ships the raw file with
    no parsed extract (Scenario-1 S1-04/05).
  * ``SessionDatastore.update_attachment_parse`` flips a ``parsing`` row to
    ``ready`` / ``failed`` and records ``parse_mode`` (which engine ran).
  * ``_spawn_attachment_parse`` parses through the CONFIGURED ``ParserRouter``
    (not a hardcoded LightLocalParser) with MODE-AWARE off-loop dispatch:
      - SYNC backend (LightLocal)  -> ``asyncio.to_thread(router.parse_sync)``
      - ASYNC_POLL backend (cloud) -> ``await router.parse`` on the main loop
    so a user who configured MinerU / PaddleOCR gets that engine for
    conversation attachments too (Scenario-2), while the loop stays responsive.

DB fixture mirrors ``test_queries`` — tmp SQLite + monkeypatched
``AsyncSessionLocal`` so ``async_unit_of_work`` binds to it; the parser is
injected via ``_build_attachment_parser`` so no settings/registry stack is
needed (per the parser-test convention in tests/modules/parser/*).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions.attachments import _attachment_specs
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import SessionAttachmentRow
from valuz_agent.ports.parser_backend import ParseResult
from valuz_agent.ports.parser_plugin import ParserPluginMode


@pytest.fixture
def db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "attach.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=[SessionAttachmentRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )


class _Row:
    """Minimal stand-in for ``SessionAttachmentRow`` — ``_attachment_specs``
    only reads ``parse_status`` / ``parsed_path`` / ``stored_path``."""

    def __init__(self, parse_status: str, parsed_path: str | None, stored_path: str) -> None:
        self.parse_status = parse_status
        self.parsed_path = parsed_path
        self.stored_path = stored_path


# ---------------------------------------------------------------------------
# _attachment_specs — source always present, parsed only when ready (S1-04/05)
# ---------------------------------------------------------------------------


def test_attachment_specs_carries_both_when_ready() -> None:
    # Parsed-ready: agent gets the original AND the markdown extract alongside.
    assert _attachment_specs([_Row("ready", "/p.md", "/raw.pdf")]) == (("/raw.pdf", "/p.md"),)


def test_attachment_specs_no_parsed_while_parsing() -> None:
    # Submit-before-parse: the original ships now; no parsed extract yet.
    assert _attachment_specs([_Row("parsing", None, "/raw.pdf")]) == (("/raw.pdf", None),)


def test_attachment_specs_no_parsed_on_failure() -> None:
    assert _attachment_specs([_Row("failed", None, "/raw.pdf")]) == (("/raw.pdf", None),)


def test_attachment_specs_no_parsed_when_ready_but_no_path() -> None:
    assert _attachment_specs([_Row("ready", None, "/raw.pdf")]) == (("/raw.pdf", None),)


# ---------------------------------------------------------------------------
# update_attachment_parse — the background task's write-back (+ parse_mode)
# ---------------------------------------------------------------------------


async def _make_parsing_row(stored_path: str = "/raw.txt", filename: str = "a.txt") -> str:
    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work() as session:
        row = SessionAttachmentRow(
            session_id="s1",
            filename=filename,
            stored_path=stored_path,
            parsed_path=None,
            parse_status="parsing",
            size_bytes=1,
            mime_type="text/plain",
            source_kind="local",
        )
        await SessionDatastore(session).create_attachment(row)
        return row.id


async def test_update_attachment_parse_flips_to_ready(db) -> None:  # type: ignore[no-untyped-def]
    from valuz_agent.infra.db import async_unit_of_work

    rid = await _make_parsing_row()
    async with async_unit_of_work() as session:
        await SessionDatastore(session).update_attachment_parse(
            rid, parsed_path="/a.parsed.md", parse_status="ready", parse_mode="light_local"
        )
    async with async_unit_of_work() as session:
        got = await SessionDatastore(session).get_attachment(rid)
    assert got is not None
    assert got.parse_status == "ready"
    assert got.parsed_path == "/a.parsed.md"
    assert got.parse_mode == "light_local"


async def test_update_attachment_parse_flips_to_failed(db) -> None:  # type: ignore[no-untyped-def]
    from valuz_agent.infra.db import async_unit_of_work

    rid = await _make_parsing_row()
    async with async_unit_of_work() as session:
        await SessionDatastore(session).update_attachment_parse(
            rid, parsed_path=None, parse_status="failed", error_message="boom"
        )
    async with async_unit_of_work() as session:
        got = await SessionDatastore(session).get_attachment(rid)
    assert got is not None
    assert got.parse_status == "failed"
    assert got.parsed_path is None
    assert got.error_message == "boom"


# ---------------------------------------------------------------------------
# _spawn_attachment_parse — configured-parser routing + mode-aware dispatch
# ---------------------------------------------------------------------------


class _SpyRouter:
    """Records which parse method ran so we can assert MODE-AWARE dispatch:
    ASYNC_POLL must ``await parse`` on the loop; SYNC must use ``parse_sync``
    (which the caller pushes off-loop via to_thread)."""

    def __init__(
        self,
        *,
        mode: ParserPluginMode,
        markdown: str = "parsed-md",
        engine: str = "fake_cloud",
        metadata: dict | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._mode = mode
        self._markdown = markdown
        self._engine = engine
        self._metadata = metadata
        self._raises = raises
        self.parse_called = False
        self.parse_sync_called = False

    def plugin_mode_for(self, file_path):  # type: ignore[no-untyped-def]
        return self._mode

    def _result(self) -> ParseResult:
        meta = self._metadata if self._metadata is not None else {"plugin_id": self._engine}
        return ParseResult(markdown=self._markdown, metadata=meta)

    async def parse(self, file_path, options=None):  # type: ignore[no-untyped-def]
        self.parse_called = True
        if self._raises:
            raise self._raises
        return self._result()

    def parse_sync(self, file_path, options=None):  # type: ignore[no-untyped-def]
        self.parse_sync_called = True
        if self._raises:
            raise self._raises
        return self._result()


async def _drain_parse_tasks() -> None:
    from valuz_agent.api.routes.sessions import _PARSE_TASKS

    for _ in range(100):
        if not _PARSE_TASKS:
            return
        await asyncio.gather(*list(_PARSE_TASKS), return_exceptions=True)


def _inject_router(monkeypatch, router) -> None:  # type: ignore[no-untyped-def]
    from valuz_agent.api.routes import sessions as sessions_routes

    async def _provider(_db):  # type: ignore[no-untyped-def]
        return router

    monkeypatch.setattr(sessions_routes, "_build_attachment_parser", _provider)


async def _run_spawn(rid: str, src, dest, base_name: str) -> SessionAttachmentRow:  # type: ignore[no-untyped-def]
    from valuz_agent.api.routes.sessions import _spawn_attachment_parse
    from valuz_agent.infra.db import async_unit_of_work

    _spawn_attachment_parse(rid, str(src), dest, base_name)
    await _drain_parse_tasks()
    async with async_unit_of_work() as session:
        got = await SessionDatastore(session).get_attachment(rid)
    assert got is not None
    return got


async def test_spawn_sync_backend_uses_parse_sync_not_parse(db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """S2-06: a SYNC backend is driven via parse_sync (off-loop), never the
    on-loop async parse()."""
    router = _SpyRouter(mode=ParserPluginMode.SYNC, markdown="md", engine="light_local")
    _inject_router(monkeypatch, router)
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(tmp_path / "f.txt"))
    got = await _run_spawn(rid, tmp_path / "f.txt", dest, "f.txt")
    assert router.parse_sync_called is True
    assert router.parse_called is False
    assert got.parse_status == "ready"
    assert got.parse_mode == "light_local"


async def test_spawn_async_poll_backend_awaits_parse_not_parse_sync(  # type: ignore[no-untyped-def]
    db, tmp_path, monkeypatch
) -> None:
    """S2-05: an ASYNC_POLL (MinerU/PaddleOCR) backend is awaited via parse()
    on the main loop — NOT to_thread(parse_sync), which would asyncio.run a
    loop detached from the PollingScheduler and hang."""
    router = _SpyRouter(mode=ParserPluginMode.ASYNC_POLL, markdown="cloud-md", engine="mineru")
    _inject_router(monkeypatch, router)
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(tmp_path / "f.pdf"), filename="f.pdf")
    got = await _run_spawn(rid, tmp_path / "f.pdf", dest, "f.pdf")
    assert router.parse_called is True
    assert router.parse_sync_called is False
    assert got.parse_status == "ready"
    assert got.parse_mode == "mineru"


async def test_spawn_records_configured_engine_and_writes_markdown(  # type: ignore[no-untyped-def]
    db, tmp_path, monkeypatch
) -> None:
    """S2-01 / X-07 (attachment context): with a configured cloud parser, the
    attachment is parsed by THAT engine and the row records its provenance."""
    router = _SpyRouter(
        mode=ParserPluginMode.SYNC,  # routing mode is orthogonal to the engine label
        markdown="MINERU EXTRACTED TEXT",
        engine="mineru",
    )
    _inject_router(monkeypatch, router)
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(tmp_path / "report.pdf"), filename="report.pdf")
    got = await _run_spawn(rid, tmp_path / "report.pdf", dest, "report.pdf")
    assert got.parse_status == "ready"
    assert got.parse_mode == "mineru"
    assert got.parsed_path is not None
    from pathlib import Path

    assert Path(got.parsed_path).read_text(encoding="utf-8") == "MINERU EXTRACTED TEXT"


async def test_spawn_parser_exception_marks_failed(db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """S1-07: a parser crash flips the row to failed (never stranded in
    parsing) and records the error — the poller stops."""
    router = _SpyRouter(mode=ParserPluginMode.SYNC, raises=RuntimeError("kaboom"))
    _inject_router(monkeypatch, router)
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(tmp_path / "f.pdf"), filename="f.pdf")
    got = await _run_spawn(rid, tmp_path / "f.pdf", dest, "f.pdf")
    assert got.parse_status == "failed"
    assert got.parsed_path is None
    assert got.error_message and "kaboom" in got.error_message


async def test_spawn_result_with_error_metadata_marks_failed(db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """S1-08: an unsupported/binary file (parser returns metadata['error'])
    is classified failed, not written as a bogus parsed.md."""
    router = _SpyRouter(
        mode=ParserPluginMode.SYNC,
        markdown="*Unsupported file type: .bin*",
        metadata={"plugin_id": "light_local", "error": "unsupported extension .bin"},
    )
    _inject_router(monkeypatch, router)
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(tmp_path / "f.bin"), filename="f.bin")
    got = await _run_spawn(rid, tmp_path / "f.bin", dest, "f.bin")
    assert got.parse_status == "failed"
    assert got.parsed_path is None


async def test_spawn_real_light_local_router_end_to_end(db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Scenario-1 end-to-end through the REAL ParserRouter + LightLocal plugin
    (no stub): a .txt attachment parses to ready with engine=light_local and
    the parsed markdown is written to the session attachment dir."""
    from plugins.parser.light_local import LightLocalPlugin
    from valuz_agent.modules.parser.registry import ParserPluginRegistry
    from valuz_agent.modules.parser.router import ParserRouter
    from valuz_agent.modules.settings.parser_routing import DEFAULT_ROUTING_CONFIG

    router = ParserRouter(
        registry=ParserPluginRegistry(plugins=[LightLocalPlugin()]),
        routing_config=DEFAULT_ROUTING_CONFIG,
    )
    _inject_router(monkeypatch, router)
    src = tmp_path / "note.txt"
    src.write_text("hello world", encoding="utf-8")
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(src), filename="note.txt")
    got = await _run_spawn(rid, src, dest, "note.txt")
    assert got.parse_status == "ready"
    assert got.parse_mode == "light_local"
    assert got.parsed_path is not None and got.parsed_path.endswith(".parsed.md")
    from pathlib import Path

    assert "hello world" in Path(got.parsed_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Scenario-2 through the REAL ParserRouter: a configured cloud plugin actually
# wins routing for a conversation attachment (the whole point of the fix).
# ---------------------------------------------------------------------------


class _FakeCloudBackend:
    async def parse(self, file_path, options=None):  # type: ignore[no-untyped-def]
        return ParseResult(markdown=f"FAKECLOUD::{file_path}", metadata={"engine": "fake_cloud"})

    def parse_sync(self, file_path, options=None):  # type: ignore[no-untyped-def]
        return ParseResult(markdown=f"FAKECLOUD::{file_path}", metadata={"engine": "fake_cloud"})

    async def health_check(self) -> bool:
        return True

    @property
    def capabilities(self) -> set[str]:
        return {"pdf", "image"}

    @property
    def strategy_name(self) -> str:
        return "fake_cloud"


def _fake_cloud_plugin():  # type: ignore[no-untyped-def]
    from valuz_agent.ports.parser_plugin import (
        CapabilityStatus,
        ParserPlugin,
        ParserPluginConfig,
        ParserPluginDescriptor,
        PluginCapability,
        SecretResolver,
    )

    class _FakeCloudPlugin(ParserPlugin):
        descriptor = ParserPluginDescriptor(
            id="fake_cloud",
            name_zh="假云解析",
            description_zh="测试用的云解析插件。",
            mode=ParserPluginMode.SYNC,
            capabilities=(
                PluginCapability(kind="pdf", status=CapabilityStatus.READY),
                PluginCapability(kind="image", status=CapabilityStatus.READY),
            ),
        )

        def build(self, config: ParserPluginConfig, secret_resolver: SecretResolver):
            return _FakeCloudBackend()

    return _FakeCloudPlugin()


def _router_with_cloud_primary():  # type: ignore[no-untyped-def]
    from plugins.parser.light_local import LightLocalPlugin
    from valuz_agent.modules.parser.registry import ParserPluginRegistry
    from valuz_agent.modules.parser.router import ParserRouter
    from valuz_agent.modules.settings.parser_routing import ParserRoutingConfig

    return ParserRouter(
        registry=ParserPluginRegistry(plugins=[LightLocalPlugin(), _fake_cloud_plugin()]),
        routing_config=ParserRoutingConfig(primary_plugin_id="fake_cloud"),
    )


async def test_scenario2_configured_cloud_parses_pdf_attachment(db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """S2-01: with the configured router primary = fake_cloud, a .pdf
    conversation attachment is parsed by fake_cloud (NOT light_local)."""
    _inject_router(monkeypatch, _router_with_cloud_primary())
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 stub")
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(src), filename="doc.pdf")
    got = await _run_spawn(rid, src, dest, "doc.pdf")
    assert got.parse_status == "ready"
    assert got.parse_mode == "fake_cloud"
    from pathlib import Path

    assert "FAKECLOUD::" in Path(got.parsed_path).read_text(encoding="utf-8")


async def test_scenario2_locked_text_kind_stays_local_even_with_cloud_primary(  # type: ignore[no-untyped-def]
    db, tmp_path, monkeypatch
) -> None:
    """S2-02 edge: text is a LOCKED_LOCAL_KIND — even with cloud configured, a
    .txt attachment routes to light_local, not the cloud engine."""
    _inject_router(monkeypatch, _router_with_cloud_primary())
    src = tmp_path / "note.txt"
    src.write_text("plain text body", encoding="utf-8")
    dest = tmp_path / "att"
    dest.mkdir()
    rid = await _make_parsing_row(stored_path=str(src), filename="note.txt")
    got = await _run_spawn(rid, src, dest, "note.txt")
    assert got.parse_status == "ready"
    assert got.parse_mode == "light_local"
