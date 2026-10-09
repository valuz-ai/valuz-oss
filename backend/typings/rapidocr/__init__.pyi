"""RapidOCR construction and default detection/recognition output consumed locally."""

from pathlib import Path
from typing import Any

from .utils.output import RapidOCROutput

class RapidOCR:
    def __init__(
        self, config_path: str | None = ..., params: dict[str, Any] | None = ...
    ) -> None: ...
    def __call__(self, img_content: str | Path | bytes) -> RapidOCROutput: ...
