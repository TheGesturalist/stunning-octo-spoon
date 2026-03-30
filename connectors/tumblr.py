"""Tumblr connector for post metadata, captions, and tags."""

from __future__ import annotations

from typing import Any

from .base import BaseConnector
from .http_helpers import get_json
from .schema import NormalizedItem


class TumblrConnector(BaseConnector):
    name = "tumblr"

    def __init__(self, blog_hostname: str, api_key: str) -> None:
        self.blog_hostname = blog_hostname
        self.api_key = api_key

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        offset = int(cursor) if cursor else 0
        url = (
            f"https://api.tumblr.com/v2/blog/{self.blog_hostname}/posts"
            f"?api_key={self.api_key}&limit={limit}&offset={offset}"
        )
        payload = get_json(url)
        return payload.get("response", {}).get("posts", [])

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        body = item.get("body") or item.get("caption")
        if body:
            return str(body)
        trail = item.get("trail") or []
        snippets = [t.get("content_raw", "") for t in trail if t.get("content_raw")]
        return "\n".join(snippets) or None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        return NormalizedItem(
            connector=self.name,
            source_id=str(item.get("id")),
            source_url=item.get("post_url"),
            title=item.get("title") or item.get("summary"),
            summary=item.get("summary"),
            fulltext=self.fetch_fulltext(item),
            content_type=item.get("type", "post"),
            created_at=item.get("date"),
            updated_at=item.get("date"),
            tags=[t for t in item.get("tags", []) if isinstance(t, str)],
            metadata={
                "blog_name": item.get("blog_name"),
                "note_count": item.get("note_count"),
                "reblog_key": item.get("reblog_key"),
            },
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        return str(last_item.get("id"))
