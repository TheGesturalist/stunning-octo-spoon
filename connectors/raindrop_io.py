"""Raindrop.io connector (bookmarks/highlights)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

from .base import BaseConnector
from .http_helpers import get_json
from .schema import NormalizedItem


class RaindropIOConnector(BaseConnector):
    name = "raindrop_io"

    def __init__(self, api_token: str, collection_id: int = 0) -> None:
        self.api_token = api_token
        self.collection_id = collection_id

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        page = int(cursor) if cursor else 0
        url = (
            f"https://api.raindrop.io/rest/v1/raindrops/{self.collection_id}"
            f"?perpage={limit}&page={page}"
        )
        payload = get_json(url, headers=self._headers)
        return payload.get("items", [])

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        highlights = item.get("highlights") or []
        if highlights:
            return "\n".join(str(h.get("text", "")) for h in highlights if h.get("text"))
        excerpt = item.get("excerpt")
        return str(excerpt) if excerpt else None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        updated = item.get("lastUpdate")
        created = item.get("created")
        return NormalizedItem(
            connector=self.name,
            source_id=str(item.get("_id")),
            source_url=item.get("link"),
            title=item.get("title"),
            summary=item.get("excerpt"),
            fulltext=self.fetch_fulltext(item),
            content_type="bookmark",
            created_at=created,
            updated_at=updated,
            tags=[t for t in item.get("tags", []) if isinstance(t, str)],
            metadata={"collection": item.get("collection"), "domain": item.get("domain")},
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        # Page-based cursor fallback; callers can store updated timestamp instead.
        updated = last_item.get("lastUpdate")
        if isinstance(updated, str):
            return quote(updated)
        return datetime.utcnow().isoformat()
