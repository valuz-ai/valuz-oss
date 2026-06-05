"""Generic file splitter for parser plugins.

Some cloud parsers cap input size (MinerU rejects PDFs > 200 pages,
PaddleOCR has similar limits). When the user's file exceeds a plugin's
ceiling we'd otherwise have to fall back to the local parser, which
defeats the whole point of paying for the cloud quality.

This module provides:

- :func:`should_split` — does this file exceed the given limits?
- :func:`split_pdf_by_pages` — produce N temp PDF parts of ≤
  ``max_pages`` each
- :func:`merge_parse_results` — concatenate per-part ``ParseResult``s
  back into one
- :func:`split_and_parse` — high-level helper that wraps a plugin's
  per-part parse function with the above

Today only PDF page-split is implemented. Office / images / HTML are
returned unchanged — splitting those generically is much harder (page
boundaries don't exist in the same shape) and the cloud parsers we
ship don't have hard page limits on those formats anyway.

The splitter is plugin-agnostic: it accepts a sync ``parse_one``
callable that takes a ``Path`` and returns a ``ParseResult``. Plugins
adapt their async parse into a sync per-part function via
``asyncio.run`` (or directly invoke their handler in a thread).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from valuz_agent.ports.parser_backend import ParseResult
from valuz_agent.ports.parser_plugin import SplitPolicy

logger = logging.getLogger(__name__)


class FileTooLarge(RuntimeError):  # noqa: N818  # domain-style naming
    """Raised when a file exceeds a plugin's ``max_bytes`` and cannot
    be sensibly split."""


def pdf_page_count(file_path: Path) -> int | None:
    """Return the PDF's page count, or None if the file isn't a PDF
    we can inspect. Uses ``pymupdf`` which is already a dependency for
    the LightLocal parser path."""
    if file_path.suffix.lower() != ".pdf":
        return None
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf not installed — cannot inspect PDF page count")
        return None
    try:
        with pymupdf.open(str(file_path)) as doc:
            return doc.page_count
    except Exception as exc:  # noqa: BLE001
        logger.warning("pymupdf failed to open %s: %s", file_path, exc)
        return None


def should_split(file_path: Path, policy: SplitPolicy) -> bool:
    """Cheap check used to decide whether to invoke
    :func:`split_pdf_by_pages`.

    Only PDFs over ``policy.max_pages`` are considered splittable today
    — other formats fall through to the plugin's normal parse path.
    """
    if policy.max_pages is None:
        return False
    if file_path.suffix.lower() != ".pdf":
        return False
    pc = pdf_page_count(file_path)
    return pc is not None and pc > policy.max_pages


def split_pdf_by_pages(
    file_path: Path, max_pages: int, *, out_dir: Path | None = None
) -> list[Path]:
    """Split ``file_path`` into parts of at most ``max_pages`` pages.

    Returns a list of temp file paths. Caller owns cleanup — typically
    via the context manager :func:`split_pdf_into_temp_dir` below.
    """
    if max_pages < 1:
        raise ValueError(f"max_pages must be >= 1, got {max_pages}")
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf required for PDF splitting; it ships transitively via the "
            "default 'pymupdf4llm' dependency — run 'uv sync' in backend/"
        ) from exc

    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="valuz-pdf-parts-"))
    out_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    src = pymupdf.open(str(file_path))
    try:
        total = src.page_count
        if total <= max_pages:
            # Nothing to split — copy the original file path into the
            # out_dir so the caller gets a uniform "list of parts"
            # return value. Saves the plugin from special-casing
            # "exactly one part".
            target = out_dir / file_path.name
            shutil.copy2(file_path, target)
            return [target]
        for start in range(0, total, max_pages):
            end = min(start + max_pages, total) - 1
            out = pymupdf.open()
            try:
                out.insert_pdf(src, from_page=start, to_page=end)
                part_path = out_dir / f"{file_path.stem}__pages_{start + 1}_{end + 1}.pdf"
                out.save(str(part_path))
                parts.append(part_path)
            finally:
                out.close()
    finally:
        src.close()

    logger.info(
        "split %s (%d pages) into %d parts of <=%d pages",
        file_path.name,
        total,
        len(parts),
        max_pages,
    )
    return parts


class _SplitTempDir:
    """Context manager that yields the part list and cleans the
    temp dir on exit."""

    def __init__(self, file_path: Path, max_pages: int) -> None:
        self._file_path = file_path
        self._max_pages = max_pages
        self._out_dir: Path | None = None

    def __enter__(self) -> list[Path]:
        self._out_dir = Path(tempfile.mkdtemp(prefix="valuz-pdf-parts-"))
        return split_pdf_by_pages(self._file_path, self._max_pages, out_dir=self._out_dir)

    def __exit__(self, *exc: object) -> None:
        if self._out_dir is not None and self._out_dir.exists():
            shutil.rmtree(self._out_dir, ignore_errors=True)


def split_pdf_into_temp_dir(file_path: Path, max_pages: int) -> _SplitTempDir:
    """Context-manager wrapper around :func:`split_pdf_by_pages` that
    cleans the temp directory on exit. Use as::

        with split_pdf_into_temp_dir(path, 200) as parts:
            for part in parts:
                results.append(parse_one(part))
    """
    return _SplitTempDir(file_path, max_pages)


def merge_parse_results(results: list[ParseResult], *, separator: str = "\n\n") -> ParseResult:
    """Concatenate per-part ``ParseResult`` objects into one.

    - Markdown: joined with ``separator`` (preserves page boundaries
      naturally if each part's markdown ends with a newline).
    - ``page_count``: sum of part page counts.
    - Metadata: merged shallow. ``engine`` and ``plugin_id`` are taken
      from the first part (they should be identical across parts); a
      new ``parts`` field records the split count for observability.
    """
    if not results:
        return ParseResult(markdown="", page_count=0, metadata={})
    if len(results) == 1:
        return results[0]

    markdown = separator.join(r.markdown for r in results if r.markdown)
    page_count = sum(r.page_count for r in results)
    metadata: dict[str, str] = dict(results[0].metadata)
    metadata["parts"] = str(len(results))
    return ParseResult(markdown=markdown, page_count=page_count, metadata=metadata)


def split_and_parse(
    file_path: Path,
    policy: SplitPolicy,
    parse_one: Callable[[Path], ParseResult],
) -> ParseResult:
    """Top-level convenience: detect splittability, split if needed,
    parse each part via ``parse_one``, merge results.

    If the file is below the threshold (or not a PDF), ``parse_one``
    is called once with the original path — no temp files created.

    Sync version — plugins whose per-part parse is async should use
    :func:`split_and_parse_async` to avoid an inner ``asyncio.run``
    inside an outer event loop (raises ``RuntimeError``).
    """
    if not should_split(file_path, policy):
        return parse_one(file_path)
    assert policy.max_pages is not None  # narrows for mypy
    with split_pdf_into_temp_dir(file_path, policy.max_pages) as parts:
        results = [parse_one(p) for p in parts]
    return merge_parse_results(results)


async def split_and_parse_async(
    file_path: Path,
    policy: SplitPolicy,
    parse_one: Callable[[Path], Awaitable[ParseResult]],
) -> ParseResult:
    """Async sibling of :func:`split_and_parse`. ``parse_one`` returns
    an awaitable; we ``await`` each part in sequence inside the same
    event loop. Suited to cloud plugins whose per-part call is a
    network round-trip + scheduler-future await."""
    if not should_split(file_path, policy):
        return await parse_one(file_path)
    assert policy.max_pages is not None  # narrows for mypy
    with split_pdf_into_temp_dir(file_path, policy.max_pages) as parts:
        results: list[ParseResult] = []
        for part in parts:
            results.append(await parse_one(part))
    return merge_parse_results(results)


__all__ = [
    "FileTooLarge",
    "SplitPolicy",
    "merge_parse_results",
    "pdf_page_count",
    "should_split",
    "split_and_parse",
    "split_and_parse_async",
    "split_pdf_by_pages",
    "split_pdf_into_temp_dir",
]
