"""Typed PyMuPDF surface used by the parser and page splitter."""

from pathlib import Path
from types import TracebackType
from typing import Self

class Document:
    def __init__(
        self,
        filename: str | Path | None = ...,
        stream: bytes | bytearray | None = ...,
        filetype: str | None = ...,
    ) -> None: ...
    @property
    def page_count(self) -> int: ...
    def __len__(self) -> int: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def insert_pdf(self, docsrc: Document, *, from_page: int = ..., to_page: int = ...) -> None: ...
    def save(self, filename: str | Path) -> None: ...
    def close(self) -> None: ...

open = Document
