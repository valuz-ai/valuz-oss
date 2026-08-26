from __future__ import annotations

from pathlib import Path
from typing import Any

from valuz_agent.i18n import t
from valuz_agent.ports.parser_backend import ParseOptions, ParseResult

_PDF_EXTS = {".pdf"}
# NOTE: .html intentionally NOT in plain-text — it is converted, not kept
# verbatim. ``html-to-markdown`` (Goldziher fork) does it in ``_parse_html``.
_PLAIN_TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".xml"}
_HTML_EXTS = {".html", ".htm"}
# Everything ``anydoc`` converts. Far wider than the MarkItDown set it
# replaced: the legacy OLE formats (.doc/.ppt/.xls), OpenDocument, RTF and
# EPUB previously fell through to "unsupported" — or, for .rtf, were indexed
# as their own control words, since RTF source is ASCII and slipped past the
# unknown-extension UTF-8 guard below.
_OFFICE_EXTS = {
    # Word
    ".doc",
    ".docx",
    ".docm",
    # PowerPoint
    ".ppt",
    ".pps",
    ".pot",
    ".pptx",
    ".pptm",
    ".ppsx",
    ".ppsm",
    # Excel
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlsb",
    # OpenDocument
    ".odt",
    ".ods",
    ".odp",
    # Other document containers
    ".rtf",
    ".epub",
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_ALL_EXTS = _PDF_EXTS | _PLAIN_TEXT_EXTS | _HTML_EXTS | _OFFICE_EXTS | _IMAGE_EXTS


def _build_rapidocr(rapidocr_cls: Any) -> Any:
    """Construct ``RapidOCR`` on the user-authorized PP-OCRv6 bundle in
    ``~/.valuz-oss/models/light_local/rapidocr/``.

    Three outcomes, and the middle one is the point:

    * **marker + all files** — build on the bundle.
    * **marker but a file missing** — raise. The bundle is broken and must be
      re-downloaded; quietly substituting rapidocr's auto-download defaults
      would OCR with a different model generation than the user approved while
      the READY marker kept reporting the install as healthy.
    * **no marker** — no bundle was ever authorized. Only reachable when the
      capability gate is bypassed (a direct test), so keep rapidocr's
      auto-download behaviour.

    rapidocr 3.x switched from individual ``*_model_path`` kwargs to a
    flat OmegaConf ``params`` dict (``"Det.model_path"`` etc.); the
    legacy kwarg shape would raise ``TypeError`` here.
    """
    from valuz_agent.modules.parser.setup_jobs.rapidocr import (
        MODEL_FILENAMES,
        REQUIRED_MODEL_FILENAMES,
    )

    try:
        from valuz_agent.infra.fs_registry import fs_registry

        target = fs_registry.parser_model_dir("light_local", "rapidocr")
    except Exception:  # noqa: BLE001 — path resolution failed; no bundle to use
        target = None

    if target is not None and (target / "READY").exists():
        # A marker means the user authorized and completed a download, so the
        # bundle is the ONLY acceptable source here. If a file is missing the
        # bundle is broken — say so. Falling through to rapidocr's auto-download
        # would "work" while silently OCR'ing with a different model generation
        # than the one the user approved, and the READY marker would keep
        # reporting the install as healthy. ``_parse_image`` gates on
        # ``RapidOcrSetupJob.is_complete()`` (same file check) and returns a
        # needs-setup result before reaching here; this is the backstop for
        # callers that bypass that gate.
        missing = [name for name in REQUIRED_MODEL_FILENAMES if not (target / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"rapidocr model bundle at {target} is incomplete (missing: "
                f"{', '.join(missing)}) — re-run the rapidocr_models setup"
            )
        det = target / MODEL_FILENAMES["det"]
        cls = target / MODEL_FILENAMES["cls"]
        rec = target / MODEL_FILENAMES["rec"]
        keys = target / MODEL_FILENAMES["dict"]
        # ``cls`` is REQUIRED even though we disable it at inference: rapidocr
        # 3.x builds the Cls component (and loads its model) at CONSTRUCTION
        # time regardless of ``use_cls`` — a missing ``Cls.model_path`` raises
        # ``... does not exists`` at init.
        return rapidocr_cls(
            params={
                "Det.model_path": str(det),
                "Rec.model_path": str(rec),
                "Rec.rec_keys_path": str(keys),
                # Disable the text-line orientation classifier at
                # INFERENCE. The bundled PaddlePaddle
                # ``PP-LCNet_x1_0_textline_ori.onnx`` has a FIXED input
                # shape (80×160) that rapidocr 3.x's Cls preprocessing
                # (48×192) doesn't match, so RUNNING it raises
                # ``onnxruntime InvalidArgument: invalid dimensions for
                # input x``. ``Cls.model_path`` still points at the
                # bundled file because rapidocr LOADS it at init (offline,
                # no ModelScope download); ``use_cls=False`` means it's
                # loaded-but-never-invoked — det+rec carry OCR, and
                # orientation correction (rare for KB doc images) is
                # skipped rather than crashing the whole parse.
                "Cls.model_path": str(cls),
                "Global.use_cls": False,
                # Pin the model ROOT at our download dir. rapidocr
                # otherwise defaults it to ``<its own package>/models``
                # (``RapidOCR._load_config``), and every engine session
                # asserts that directory EXISTS before it even looks at
                # ``model_path``:
                #     model_root_dir = Path(cfg.get("model_root_dir"))
                #     if not model_root_dir.exists():
                #         raise FileNotFoundError(...)
                # (``inference_engine/onnxruntime/main.py``). In the
                # PyInstaller build that package dir does not exist —
                # rapidocr's ``.py`` live in the PYZ archive and we
                # deliberately don't ship its ~16 MB of bundled weights —
                # so OCR would die on the root-dir assert even though all
                # three explicit ``model_path`` values are valid. Source
                # runs happened to pass only because site-packages has
                # the directory.
                "Global.model_root_dir": str(target),
            }
        )

    # No bundle at all (no marker) — the user never authorized a download, so
    # this is only reachable when the capability gate is bypassed (a direct
    # test). Fall back to rapidocr's auto-download, pinning the root: left at
    # its default it would resolve to rapidocr's own package dir, which the
    # packaged build doesn't have — and if it ever did, rapidocr would write
    # downloaded weights INTO the signed app bundle.
    if target is not None:
        return rapidocr_cls(params={"Global.model_root_dir": str(target)})
    return rapidocr_cls()


def _light_local_parse_worker(file_path: str, options: ParseOptions | None = None) -> ParseResult:
    """Picklable entry executed inside the parse process pool.

    Runs the *inline* implementation (``_parse_sync_impl``) — NOT the public
    ``parse_sync`` wrapper, which would recursively try to offload and
    deadlock. Constructs a fresh stateless ``LightLocalParser`` in the worker.
    """
    return LightLocalParser()._parse_sync_impl(file_path, options)


class LightLocalParser:
    """Personal baseline: in-process parser using PyMuPDF4LLM + anydoc + RapidOCR.

    ``pymupdf4llm`` does its work in pure Python and holds the GIL, so both
    entry points offload to a **separate process**
    via :mod:`valuz_agent.infra.parse_pool` — otherwise a ``to_thread`` worker
    (conversation-attachment parse) or the docs-reindex daemon thread would
    starve the single-threaded event loop through GIL contention. See the
    ``parse_pool`` module docstring for the measured loop-stall numbers.
    """

    def parse_sync(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        """Sync entry. Offloads the GIL-bound parse to a worker process,
        blocking the CALLING THREAD (never the event loop) on the result.

        Must only be called from a worker thread / true-sync context — the
        router invokes it from a ``to_thread`` worker (attachments) and from
        the docs-reindex daemon thread. NEVER call it on the event loop.
        """
        from valuz_agent.infra import parse_pool

        return parse_pool.run_parse_blocking(_light_local_parse_worker, file_path, options)

    def _parse_sync_impl(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        p = Path(file_path)
        ext = p.suffix.lower()

        if ext in _PDF_EXTS:
            return self._parse_pdf(p)
        if ext in _PLAIN_TEXT_EXTS:
            return self._parse_plain_text(p)
        if ext in _HTML_EXTS:
            return self._parse_html(p)
        if ext in _OFFICE_EXTS:
            return self._parse_office(p)
        if ext in _IMAGE_EXTS:
            return self._parse_image(p)

        # Unknown extension. If the file decodes as valid UTF-8 text —
        # source code (.py / .js / .sh / .go …) and other plain-text
        # formats not in _PLAIN_TEXT_EXTS — keep its raw content as-is
        # instead of emitting a useless placeholder. Strict decode (no
        # ``errors="replace"``) so a genuinely-binary file still falls
        # through to "unsupported" rather than indexing garbage bytes.
        try:
            md = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return self._unsupported(ext)
        return ParseResult(markdown=md, page_count=1, metadata={"engine": "plain_text"})

    @staticmethod
    def _unsupported(ext: str) -> ParseResult:
        """The one shape for "this parser cannot read this file".

        ``metadata["error"]`` is load-bearing downstream: the attachment
        pipeline surfaces it verbatim as the user-visible ``error_message``.
        """
        return ParseResult(
            markdown=f"*Unsupported file type: {ext}*",
            page_count=0,
            metadata={"engine": "none", "error": f"unsupported extension {ext}"},
        )

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        # Await the parse in a worker PROCESS so the GIL-bound work can't block
        # this loop. Reached on the main loop via the router's
        # ``_runtime_fallback_async`` (cloud plugin threw → demote to local), so
        # a plain ``self.parse_sync(...)`` here would freeze the server.
        from valuz_agent.infra import parse_pool

        return await parse_pool.run_parse_async(_light_local_parse_worker, file_path, options)

    async def health_check(self) -> bool:
        return True

    @property
    def capabilities(self) -> set[str]:
        return {e.lstrip(".") for e in _ALL_EXTS}

    @property
    def strategy_name(self) -> str:
        return "light_local"

    def _parse_plain_text(self, path: Path) -> ParseResult:
        try:
            md = path.read_text(encoding="utf-8")
            return ParseResult(markdown=md, page_count=1, metadata={"engine": "plain_text"})
        except UnicodeDecodeError:
            try:
                md = path.read_text(encoding="utf-8", errors="replace")
                return ParseResult(markdown=md, page_count=1, metadata={"engine": "plain_text"})
            except Exception as exc:
                return ParseResult(
                    markdown=f"*Read error: {exc}*",
                    metadata={"engine": "plain_text", "error": str(exc)},
                )

    def _parse_pdf(self, path: Path) -> ParseResult:
        try:
            import pymupdf4llm
        except ImportError:
            return ParseResult(
                markdown="*pymupdf4llm not installed*",
                metadata={"engine": "pymupdf4llm", "error": "not_installed"},
            )
        try:
            md = pymupdf4llm.to_markdown(str(path))
            import pymupdf

            doc = pymupdf.open(str(path))
            page_count = len(doc)
            doc.close()
            return ParseResult(
                markdown=md, page_count=page_count, metadata={"engine": "pymupdf4llm"}
            )
        except Exception as exc:
            return ParseResult(
                markdown=f"*PDF parse error: {exc}*",
                metadata={"engine": "pymupdf4llm", "error": str(exc)},
            )

    def _parse_office(self, path: Path) -> ParseResult:
        """Convert any ``_OFFICE_EXTS`` document through anydoc (Rust, MIT).

        Sole backend for these formats. It replaced MarkItDown, which handled
        only docx/xlsx/pptx and routed sheets through pandas — an 863e8 revenue
        cell rendered as ``8.630000e+10`` and every empty cell as ``NaN``, both
        reaching the model verbatim.
        """
        try:
            import anydoc
        except ImportError:
            return ParseResult(
                markdown="*anydoc not installed*",
                metadata={"engine": "anydoc", "error": "not_installed"},
            )
        try:
            md = anydoc.to_markdown(str(path))
            return ParseResult(markdown=md, page_count=1, metadata={"engine": "anydoc"})
        except Exception as exc:
            # anydoc raises a typed ConvertError per failure mode (Encrypted,
            # Unsupported, Malformed, ResourceLimit, MissingPart) plus OSError
            # for unreadable files. Surface the reason rather than mapping it —
            # every one of them means "no markdown came out of this file".
            return ParseResult(
                markdown=f"*Office parse error: {exc}*",
                metadata={"engine": "anydoc", "error": str(exc)},
            )

    def _parse_html(self, path: Path) -> ParseResult:
        """Convert HTML to Markdown via ``html-to-markdown`` (Goldziher fork).

        HTML has its own backend rather than riding with the office formats:
        anydoc does not read HTML at all. (The predecessor here, MarkItDown,
        did — but its HTML path operated on ASCII and corrupted Chinese
        content, which is why HTML moved off it first, well before the office
        formats followed.)

        ``html-to-markdown`` is a typed, modernized fork of markdownify
        (same MIT license) with better HTML5 + table (rowspan/colspan)
        handling. It exposes a ``markdownify`` callable for drop-in
        compatibility — we use that here so the call site stays
        markdownify-shaped.
        """
        try:
            from html_to_markdown import markdownify as _to_md
        except ImportError:
            return ParseResult(
                markdown="*html-to-markdown not installed*",
                metadata={"engine": "html_to_markdown", "error": "not_installed"},
            )
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(
                markdown=f"*HTML read error: {exc}*",
                metadata={"engine": "html_to_markdown", "error": str(exc)},
            )
        try:
            md = _to_md(html, heading_style="ATX")
            return ParseResult(
                markdown=md,
                page_count=1,
                metadata={"engine": "html_to_markdown"},
            )
        except Exception as exc:
            return ParseResult(
                markdown=f"*HTML parse error: {exc}*",
                metadata={"engine": "html_to_markdown", "error": str(exc)},
            )

    def _parse_image(self, path: Path) -> ParseResult:
        # ── Authorization gate (plan §"反静默契约") ──
        # Even if ``rapidocr`` ships with auto-download fallback, we refuse to
        # OCR until the user has explicitly authorized the model download via
        # the SetupJob flow.
        #
        # Delegate to the setup job's ``is_complete()`` rather than re-deriving
        # the condition here: it owns the definition of a usable bundle (READY
        # marker + written by a v6 run + every model file still present), and
        # the setup UI gates on the same call — so "the UI says installed" and
        # "the parser will use it" can never disagree. This gate used to test
        # only ``READY.exists()``, which let a bundle with deleted/partial model
        # files through; the parser then fell through to rapidocr's own
        # auto-download and silently OCR'd with a different model generation.
        try:
            from valuz_agent.modules.parser.setup_jobs.rapidocr import RapidOcrSetupJob

            bundle_ready = RapidOcrSetupJob().is_complete()
        except Exception:
            bundle_ready = False
        if not bundle_ready:
            return ParseResult(
                markdown=t("backend.parser.ocrNotAuthorized"),
                metadata={
                    "engine": "rapidocr",
                    "error": "rapidocr_models setup required",
                    "setup_id": "rapidocr_models",
                },
            )

        try:
            from rapidocr import RapidOCR
        except ImportError:
            return ParseResult(
                markdown="*rapidocr not installed*",
                metadata={"engine": "rapidocr", "error": "not_installed"},
            )
        try:
            ocr = _build_rapidocr(RapidOCR)
            # rapidocr 3.x returns a single ``RapidOCROutput`` dataclass
            # with ``.txts: tuple[str] | None``. ``None`` / empty tuple
            # means "no text detected" — surface that as a stable
            # placeholder rather than letting the caller see a None.
            result = ocr(str(path))
            txts = getattr(result, "txts", None) or ()
            if not txts:
                return ParseResult(
                    markdown="*(no text detected)*",
                    page_count=1,
                    metadata={"engine": "rapidocr"},
                )
            md = "\n".join(txts)
            return ParseResult(markdown=md, page_count=1, metadata={"engine": "rapidocr"})
        except Exception as exc:
            return ParseResult(
                markdown=f"*OCR error: {exc}*",
                metadata={"engine": "rapidocr", "error": str(exc)},
            )
