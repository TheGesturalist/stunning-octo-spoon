"""Local filesystem connector using optional sidecar full-text indexes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib

from .base import BaseConnector
from .schema import NormalizedItem


class LocalLibraryConnector(BaseConnector):
    name = "local_library"

    def __init__(self, library_path: str | Path, index_path: str | Path | None = None) -> None:
        self.library_path = Path(library_path)
        self.index_path = Path(index_path) if index_path else None

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        files = sorted(p for p in self.library_path.rglob("*") if p.is_file())
        start = int(cursor) if cursor else 0
        page = files[start : start + limit]
        return [
            {
                "index": start + offset,
                "path": str(path),
                "name": path.name,
                "stem": path.stem,
                "suffix": path.suffix.lower(),
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime,
            }
            for offset, path in enumerate(page)
        ]

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        source_path = Path(item["path"])
        if self.index_path:
            sidecar = self.index_path / f"{source_path.stem}.txt"
            if sidecar.exists():
                return sidecar.read_text(encoding="utf-8", errors="ignore")
        if source_path.suffix.lower() in {".txt", ".md", ".rst"}:
            return source_path.read_text(encoding="utf-8", errors="ignore")
        return None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        fulltext = self.fetch_fulltext(item)
        source_path = Path(item["path"])
        source_id = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
        return NormalizedItem(
            connector=self.name,
            source_id=source_id,
            source_url=source_path.as_uri(),
            title=item.get("stem"),
            fulltext=fulltext,
            content_type="file",
            updated_at=None,
            metadata={
                "path": item["path"],
                "suffix": item.get("suffix"),
                "size": item.get("size"),
                "mtime": item.get("mtime"),
            },
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        index = last_item.get("index")
        return str(index) if isinstance(index, int) else None
