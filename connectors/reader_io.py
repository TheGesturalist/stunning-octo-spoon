"""Reader.io connector for saved reading list/highlights.

Uses Feedbin-compatible API style as a practical baseline.
"""

from __future__ import annotations

from typing import Any

from .base import BaseConnector
from .http_helpers import get_json
from .schema import NormalizedItem


class ReaderIOConnector(BaseConnector):
    name = "reader_io"

    def __init__(self, api_token: str, base_url: str = "https://readwise.io/api/v3") -> None:
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_token}"}

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        next_page = f"&pageCursor={cursor}" if cursor else ""
        url = f"{self.base_url}/list/?page_size={limit}{next_page}"
        payload = get_json(url, headers=self._headers)
        return payload.get("results", [])

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        text = item.get("text")
        summary = item.get("summary")
        if text and summary:
            return f"{summary}\n\n{text}"
        return text or summary

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        highlights = [h.get("text", "") for h in item.get("highlights", []) if h.get("text")]
        return NormalizedItem(
            connector=self.name,
            source_id=str(item.get("id")),
            source_url=item.get("url"),
            title=item.get("title"),
            author=item.get("author"),
            summary=item.get("summary"),
            fulltext=self.fetch_fulltext(item),
            content_type="reading_item",
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            tags=[t for t in item.get("tags", []) if isinstance(t, str)],
            highlights=highlights,
            metadata={"category": item.get("category"), "site_name": item.get("site_name")},
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        return last_item.get("updated_at") or str(last_item.get("id"))
