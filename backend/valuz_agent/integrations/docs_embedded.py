from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from valuz_agent.ports.docs_runtime import DocsHealthSnapshot, SearchResult

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _detect_rg() -> str | None:
    """Find the ripgrep binary. Priority: VALUZ_RG_PATH > bundled > PATH."""
    env_path = os.environ.get("VALUZ_RG_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "bin" / "rg"  # type: ignore[attr-defined]
        if bundled.is_file():
            return str(bundled)

    return shutil.which("rg")


def _tokenize_query(query: str) -> list[str]:
    """Split a query into individual keywords on whitespace."""
    return [kw for kw in query.strip().split() if kw]


def sanitize_preview_filename(source_filename: str) -> str:
    """Turn a source filename into a safe preview filename.

    Example: '九鼎投资(600053) - 2025 Q4 - 年度业绩预告.PDF'
          -> '九鼎投资(600053) - 2025 Q4 - 年度业绩预告_parsed.md'
    """
    stem = Path(source_filename).stem
    safe = _UNSAFE_FILENAME_RE.sub("_", stem).strip().rstrip(".")
    if not safe:
        safe = "unnamed"
    return f"{safe}_parsed.md"


class EmbeddedDocsRuntime:
    """Personal baseline: ripgrep-powered search over preview markdown files.

    Falls back to Python string matching when rg is not available.
    """

    SMALL_FILE_THRESHOLD = 100
    CONTEXT_LINES = 30
    RG_MAX_COUNT = 50
    RG_TIMEOUT_SECONDS = 10

    def __init__(self, preview_dir: Path | None = None) -> None:
        self._preview_dir = preview_dir
        self._rg_path: str | None = _detect_rg()

    @property
    def preview_dir(self) -> Path | None:
        return self._preview_dir

    @preview_dir.setter
    def preview_dir(self, value: Path) -> None:
        self._preview_dir = value

    @property
    def rg_available(self) -> bool:
        return self._rg_path is not None

    def search_sync(
        self,
        query: str,
        doc_scope_ids: list[str],
        top_k: int = 5,
        doc_paths: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        if doc_paths:
            resolved = {did: doc_paths[did] for did in doc_scope_ids if did in doc_paths}
        elif self._preview_dir and self._preview_dir.exists():
            resolved = {}
            for did in doc_scope_ids:
                p = self._preview_dir / f"{did}.md"
                if p.exists():
                    resolved[did] = str(p)
        else:
            return []

        if not resolved:
            return []

        if self._rg_path:
            try:
                return self._search_with_rg(query, resolved, top_k)
            except Exception:
                logger.warning("ripgrep search failed, falling back to python", exc_info=True)

        return self._search_with_python(query, resolved, top_k)

    # ------------------------------------------------------------------
    # ripgrep path
    # ------------------------------------------------------------------

    def _search_with_rg(
        self,
        query: str,
        resolved: dict[str, str],
        top_k: int,
    ) -> list[SearchResult]:
        assert self._rg_path is not None

        paths: list[str] = []
        id_by_path: dict[str, str] = {}
        for doc_id, fpath in resolved.items():
            if Path(fpath).exists():
                paths.append(fpath)
                id_by_path[fpath] = doc_id

        if not paths:
            return []

        keywords = _tokenize_query(query)
        if not keywords:
            return []

        cmd = [
            self._rg_path,
            "--json",
            "--ignore-case",
            "--fixed-strings",
            f"--context={self.CONTEXT_LINES}",
            f"--max-count={self.RG_MAX_COUNT}",
            "--no-heading",
            "--",
        ]
        for kw in keywords:
            cmd.extend(["-e", kw])
        cmd.extend(paths)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.RG_TIMEOUT_SECONDS,
        )

        if proc.returncode >= 2:
            raise RuntimeError(f"rg exited with code {proc.returncode}: {proc.stderr[:200]}")

        if proc.returncode == 1:
            return []

        return self._parse_rg_json(proc.stdout, id_by_path, top_k)

    def _parse_rg_json(
        self,
        stdout: str,
        id_by_path: dict[str, str],
        top_k: int,
    ) -> list[SearchResult]:
        file_data: dict[str, _RgFileAccumulator] = {}

        for raw_line in stdout.splitlines():
            if not raw_line.strip():
                continue
            try:
                msg = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            data = msg.get("data", {})

            if msg_type == "begin":
                fpath = data.get("path", {}).get("text", "")
                if fpath and fpath in id_by_path:
                    file_data[fpath] = _RgFileAccumulator(fpath)

            elif msg_type in ("match", "context"):
                fpath = data.get("path", {}).get("text", "")
                acc = file_data.get(fpath)
                if acc is None:
                    continue
                line_number = data.get("line_number", 0)
                line_text = data.get("lines", {}).get("text", "")
                if line_text.endswith("\n"):
                    line_text = line_text[:-1]
                acc.lines.append((line_number, line_text))
                if msg_type == "match":
                    acc.match_count += 1
                    if acc.first_match_line == 0:
                        acc.first_match_line = line_number

            elif msg_type == "end":
                fpath = data.get("path", {}).get("text", "")
                stats = data.get("stats", {})
                acc = file_data.get(fpath)
                if acc is not None:
                    acc.match_count = max(acc.match_count, stats.get("matched_lines", 0))

        results: list[tuple[float, SearchResult]] = []
        for fpath, acc in file_data.items():
            if acc.match_count == 0:
                continue
            doc_id = id_by_path[fpath]

            acc.lines.sort(key=lambda x: x[0])
            snippet_lines = [text for _, text in acc.lines]
            snippet = "\n".join(snippet_lines)

            total_lines = self._count_file_lines(fpath)

            filename = self._extract_filename_from_file(fpath)

            results.append((float(acc.match_count), SearchResult(
                document_id=doc_id,
                score=float(acc.match_count),
                snippet=snippet,
                filename=filename,
                preview_path=fpath,
                match_line=acc.first_match_line,
                total_lines=total_lines,
            )))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r[1] for r in results[:top_k]]

    @staticmethod
    def _count_file_lines(fpath: str) -> int:
        try:
            with open(fpath, encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    @staticmethod
    def _extract_filename_from_file(fpath: str) -> str | None:
        try:
            with open(fpath, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 3:
                        break
                    if line.startswith("# "):
                        return line[2:].strip()
        except OSError:
            pass
        return None

    # ------------------------------------------------------------------
    # Python fallback path
    # ------------------------------------------------------------------

    def _search_with_python(
        self,
        query: str,
        resolved: dict[str, str],
        top_k: int,
    ) -> list[SearchResult]:
        keywords = [kw.lower() for kw in _tokenize_query(query)]
        if not keywords:
            return []
        hits: list[tuple[float, SearchResult]] = []

        for doc_id, fpath in resolved.items():
            p = Path(fpath)
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            lines = text.splitlines()
            total_lines = len(lines)

            match_indices = [
                i for i, line in enumerate(lines)
                if any(kw in line.lower() for kw in keywords)
            ]
            if not match_indices:
                continue

            score = float(len(match_indices))
            first_match = match_indices[0]

            filename = None
            for line in lines[:3]:
                if line.startswith("# "):
                    filename = line[2:].strip()
                    break

            if total_lines <= self.SMALL_FILE_THRESHOLD:
                snippet = text
            else:
                start = max(0, first_match - self.CONTEXT_LINES)
                end = min(total_lines, first_match + self.CONTEXT_LINES + 1)
                selected = lines[start:end]
                snippet = "\n".join(selected)
                if start > 0:
                    snippet = f"[... lines 1-{start} omitted ...]\n" + snippet
                if end < total_lines:
                    snippet = snippet + f"\n[... lines {end + 1}-{total_lines} omitted ...]"

            hits.append((score, SearchResult(
                document_id=doc_id,
                score=score,
                snippet=snippet,
                filename=filename,
                preview_path=fpath,
                match_line=first_match + 1,
                total_lines=total_lines,
            )))

        hits.sort(key=lambda x: x[0], reverse=True)
        return [h[1] for h in hits[:top_k]]

    # ------------------------------------------------------------------
    # async wrapper + health
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        doc_scope_ids: list[str],
        top_k: int = 5,
    ) -> list[SearchResult]:
        # ``search_sync`` runs ripgrep (subprocess) + a pure-Python fallback
        # scan — both blocking. Doc search is an MCP tool the agent calls
        # mid-turn, so running it inline would freeze the whole event loop for
        # the duration of the search. Push it to a worker thread.
        return await asyncio.to_thread(self.search_sync, query, doc_scope_ids, top_k)

    async def health(self) -> DocsHealthSnapshot:
        if self._preview_dir and self._preview_dir.exists():
            return DocsHealthSnapshot(
                provider_id=self.provider_id,
                status="healthy",
            )
        return DocsHealthSnapshot(
            provider_id=self.provider_id,
            status="unavailable",
            reason="preview directory does not exist",
        )

    @property
    def provider_id(self) -> str:
        return "personal.embedded"


class _RgFileAccumulator:
    __slots__ = ("fpath", "lines", "match_count", "first_match_line")

    def __init__(self, fpath: str) -> None:
        self.fpath = fpath
        self.lines: list[tuple[int, str]] = []
        self.match_count: int = 0
        self.first_match_line: int = 0
