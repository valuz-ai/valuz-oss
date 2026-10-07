"""Default plain-markdown extraction used by LightLocalParser (page_chunks=False)."""

from typing import Literal

from pymupdf import Document

def to_markdown(doc: str | Document, *, page_chunks: Literal[False] = ...) -> str: ...
