"""Internet Archive connector for metadata + optional text fetch."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from .base import BaseConnector
from .http_helpers import get_json, get_text
from .schema import NormalizedItem


class InternetArchiveConnector(BaseConnector):
    name = "internet_archive"

    def __init__(self, query: str = "mediatype:texts") -> None:
        self.query = query

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        page = int(cursor) if cursor else 1
        q = quote_plus(self.query)
        url = (
            "https://archive.org/advancedsearch.php"
            f"?q={q}&fl[]=identifier,title,creator,date,mediatype,description"
            f"&rows={limit}&page={page}&output=json"
        )
        payload = get_json(url)
        return payload.get("response", {}).get("docs", [])

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        identifier = item.get("identifier")
        if not identifier:
            return None
        # Attempt OCR/plaintext derivative commonly hosted by IA.
        text_url = f"https://archive.org/download/{identifier}/{identifier}_djvu.txt"
        try:
            return get_text(text_url)
        except Exception:
            return None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        identifier = str(item.get("identifier"))
        description = item.get("description")
        summary = description[0] if isinstance(description, list) and description else description
        return NormalizedItem(
            connector=self.name,
            source_id=identifier,
            source_url=f"https://archive.org/details/{identifier}",
            title=item.get("title"),
            author=item.get("creator"),
            summary=summary,
            fulltext=self.fetch_fulltext(item),
            content_type=item.get("mediatype", "archive_item"),
            created_at=item.get("date"),
            updated_at=item.get("date"),
            metadata={"identifier": identifier},
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        return str(last_item.get("identifier", "")) or None
