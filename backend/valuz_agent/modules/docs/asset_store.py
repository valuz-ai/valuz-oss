from __future__ import annotations

import shutil
from pathlib import Path


class AssetStore:
    def __init__(self, docs_dir: Path) -> None:
        self.assets_dir = docs_dir / "assets"
        self.preview_dir = docs_dir / "preview"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, doc_id: str, filename: str, content: bytes) -> Path:
        ext = Path(filename).suffix
        dest_dir = self.assets_dir / doc_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"source{ext}"
        dest.write_bytes(content)
        return dest

    def save_preview(self, doc_id: str, filename: str, markdown: str) -> Path:
        dest = self.preview_dir / f"{doc_id}.md"
        dest.write_text(f"# {filename}\n\n{markdown}", encoding="utf-8")
        return dest

    def read_preview(self, doc_id: str) -> str | None:
        p = self.preview_dir / f"{doc_id}.md"
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def delete_assets(self, doc_id: str) -> None:
        asset_dir = self.assets_dir / doc_id
        if asset_dir.exists():
            shutil.rmtree(asset_dir)
        preview = self.preview_dir / f"{doc_id}.md"
        if preview.exists():
            preview.unlink()
