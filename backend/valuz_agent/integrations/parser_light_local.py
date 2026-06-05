from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from valuz_agent.i18n import t
from valuz_agent.ports.parser_backend import ParseOptions, ParseResult

_PDF_EXTS = {".pdf"}
# NOTE: .html intentionally NOT in plain-text; MarkItDown's HTML path uses
# ASCII internally and crashes on non-ASCII content (CLAUDE.md pitfall).
# HTML is handled via ``html-to-markdown`` (Goldziher fork) in ``_parse_html``.
_PLAIN_TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".xml"}
_HTML_EXTS = {".html", ".htm"}
_OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_ALL_EXTS = _PDF_EXTS | _PLAIN_TEXT_EXTS | _HTML_EXTS | _OFFICE_EXTS | _IMAGE_EXTS


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _build_rapidocr(rapidocr_cls: Any) -> Any:
    """Construct ``RapidOCR`` preferring the user-authorized PP-OCRv5
    bundle in ``~/.valuz/app/models/light_local/rapidocr/`` when the
    READY marker is present. Falls back to the library's default
    behaviour (auto-download from ModelScope) when the marker is
    absent — that case only fires when the router's capability gate is
    bypassed (e.g. a direct test or a stale setup job).

    rapidocr 3.x switched from individual ``*_model_path`` kwargs to a
    flat OmegaConf ``params`` dict (``"Det.model_path"`` etc.); the
    legacy kwarg shape would raise ``TypeError`` here.
    """
    try:
        from valuz_agent.infra.fs_registry import fs_registry

        target = fs_registry.parser_model_dir("light_local", "rapidocr")
        marker = target / "READY"
        if marker.exists():
            det = target / "ch_PP-OCRv5_det_mobile.onnx"
            cls = target / "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"
            rec = target / "ch_PP-OCRv5_rec_mobile.onnx"
            keys = target / "ppocrv5_dict.txt"
            # ``cls`` is REQUIRED even though we disable it at inference:
            # rapidocr 3.x builds the Cls component (and loads its model) at
            # CONSTRUCTION time regardless of ``use_cls`` — a missing
            # ``Cls.model_path`` raises ``... does not exists`` at init. So all
            # four bundled files must be present, else fall through to the
            # default ctor (auto-download).
            if det.exists() and cls.exists() and rec.exists() and keys.exists():
                return rapidocr_cls(
                    params={
                        "Det.model_path": str(det),
                        "Rec.model_path": str(rec),
                        "Rec.rec_keys_path": str(keys),
                        # Disable the text-line orientation classifier at
                        # INFERENCE. The bundled PP-OCRv5
                        # ``..._textline_ori_cls_mobile.onnx`` has a FIXED input
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
                    }
                )
    except Exception:  # noqa: BLE001
        # Any error in path resolution — fall through to default ctor.
        # We deliberately don't raise so that auto-download installs
        # keep working.
        pass
    return rapidocr_cls()


class LightLocalParser:
    """Personal baseline: in-process parser using PyMuPDF4LLM + MarkItDown + RapidOCR."""

    def parse_sync(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
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
            return ParseResult(
                markdown=f"*Unsupported file type: {ext}*",
                page_count=0,
                metadata={"engine": "none", "error": f"unsupported extension {ext}"},
            )
        return ParseResult(markdown=md, page_count=1, metadata={"engine": "plain_text"})

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        return self.parse_sync(file_path, options)

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
        try:
            from markitdown import MarkItDown
        except ImportError:
            return ParseResult(
                markdown="*markitdown not installed*",
                metadata={"engine": "markitdown", "error": "not_installed"},
            )
        try:
            converter = MarkItDown()
            result = converter.convert(str(path))
            md = result.text_content if result.text_content else ""
            return ParseResult(markdown=md, page_count=1, metadata={"engine": "markitdown"})
        except Exception as exc:
            return ParseResult(
                markdown=f"*Office parse error: {exc}*",
                metadata={"engine": "markitdown", "error": str(exc)},
            )

    def _parse_html(self, path: Path) -> ParseResult:
        """Convert HTML to Markdown via ``html-to-markdown`` (Goldziher fork).

        We deliberately do NOT route HTML through MarkItDown: its HTML
        backend operates on ASCII and corrupts non-ASCII content (the
        Chinese-doc failure documented in backend/CLAUDE.md).

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
        # Even if ``rapidocr`` ships with auto-download fallback, we
        # refuse to OCR until the user has explicitly authorized the
        # model download via the SetupJob flow. The marker file at
        # parser_model_dir("light_local","rapidocr")/READY is written
        # only after a successful authorized download. The v4→v5 cutover
        # invalidated the previous READY content; the setup-job's
        # ``is_complete()`` parses ``model_version=`` so users on a
        # stale v4 install are re-prompted before this gate clears.
        try:
            from valuz_agent.infra.fs_registry import fs_registry

            marker = fs_registry.parser_model_dir("light_local", "rapidocr") / "READY"
        except Exception:
            marker = None
        if marker is None or not marker.exists():
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
