from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ParseOptions:
    max_pages: int | None = None
    extract_tables: bool = True
    extract_images: bool = False


@dataclass
class ParseResult:
    markdown: str
    page_count: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class ParserBackend(Protocol):
    """Port: document parsing strategy."""

    async def parse(
        self, file_path: str, options: ParseOptions | None = None
    ) -> ParseResult: ...

    async def health_check(self) -> bool: ...

    @property
    def capabilities(self) -> set[str]: ...

    @property
    def strategy_name(self) -> str: ...
