"""Canonical / cultural providers: Public Domain Review and Open Culture.

Neither site publishes a search API, so each is handled on its own terms:

**Public Domain Review** is a Gatsby site that ships its own browse data as
static JSON (`/page-data/<page>/page-data.json`). Fetching the collections and
essays indexes once gives the *complete* archive — 1,255 collections and 343
essays with titles, slugs and facets — which is then searched locally. This is
full-archive coverage, not a recent-items sample.

**Open Culture** redirects any `?s=`/`search=` request to a Google Custom Search
page, so its search is not reachable as an API. What *is* reachable is the
WordPress REST post listing, so this provider indexes a bounded window of recent
posts and searches that locally. The window is stated in the nav hint; treat it
as a recency feed, not an archive search.
"""

from __future__ import annotations

import time
from typing import Any

from connectors.schema import NormalizedItem
from local_index_service import find_term_matches, tokenize

from ._http import fetch_json, strip_html
from .base import OPEN_RIGHTS, SearchProvider

# Static indexes are large (~600KB) but change slowly; hold them in-process.
_INDEX_TTL_SECONDS = 6 * 3600
_index_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _cached_index(key: str, loader: Any) -> list[dict[str, Any]]:
    now = time.monotonic()
    hit = _index_cache.get(key)
    if hit and now - hit[0] < _INDEX_TTL_SECONDS:
        return hit[1]
    records = loader()
    _index_cache[key] = (now, records)
    return records


def clear_index_cache() -> None:
    """Test hook / manual refresh."""

    _index_cache.clear()


class PublicDomainReviewProvider(SearchProvider):
    """Full-archive search over PDR collections and essays."""

    provider_id = "cultural:pdr"
    label = "Public Domain Review"
    group = "Canonical & cultural"
    library_connector = "public_domain_review"
    hint = "Full archive · 1,255 collections + essays"
    bang = "pdr"
    default_rights = OPEN_RIGHTS

    BASE = "https://publicdomainreview.org"

    def _load_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for page, kind, url_segment in (
            ("collections", "collection", "collection"),
            ("essays", "essay", "essay"),
        ):
            try:
                payload = fetch_json(f"{self.BASE}/page-data/{page}/page-data.json")
            except Exception:  # noqa: BLE001 — one index missing shouldn't blank the source
                continue
            data = ((payload or {}).get("result") or {}).get("data") or {}
            for container in data.values():
                if not isinstance(container, dict) or "edges" not in container:
                    continue
                for edge in container.get("edges") or []:
                    node = (edge or {}).get("node") or {}
                    fields = node.get("data") or {}
                    if not fields.get("Title"):
                        continue
                    records.append({**fields, "_kind": kind, "_segment": url_segment})
        return records

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        terms = tokenize(query)
        if not terms:
            return []
        records = _cached_index(self.provider_id, self._load_records)
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            haystack = self._haystack(record)
            hits = len(find_term_matches(haystack, terms))
            if hits:
                scored.append((hits, record))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [self._normalize(r) for _, r in scored[:limit]]

    @staticmethod
    def _haystack(record: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("Title", "Intro", "Medium", "Slug"):
            value = record.get(key)
            if isinstance(value, str):
                parts.append(value)
        for key in ("Theme", "Genre", "Type", "Epoch"):
            value = record.get(key)
            if isinstance(value, list):
                parts.extend(str(v) for v in value)
            elif isinstance(value, str):
                parts.append(value)
        return " ".join(parts)

    def _normalize(self, record: dict[str, Any]) -> NormalizedItem:
        title = strip_html(record.get("Title")) or record.get("Title")
        slug = record.get("Slug")
        intro = strip_html(record.get("Intro")) or None
        themes = record.get("Theme")
        themes = themes if isinstance(themes, list) else ([themes] if themes else [])
        kind = record.get("_kind", "collection")
        return NormalizedItem(
            connector=self.library_connector,
            source_id=f"{kind}:{slug or title}",
            source_url=f"{self.BASE}/{record.get('_segment', 'collection')}/{slug}" if slug else self.BASE,
            title=title,
            summary=intro[:600] if intro else None,
            fulltext="\n\n".join(p for p in (title, intro) if p) or None,
            content_type=f"pdr_{kind}",
            tags=["public-domain-review", kind, *[str(t) for t in themes if t]],
            metadata={
                "medium": record.get("Medium"),
                "themes": [str(t) for t in themes if t],
                "kind": kind,
                "slug": slug,
            },
            rights=dict(self.default_rights),
        )


class OpenCultureProvider(SearchProvider):
    """Recent Open Culture posts (site search itself is a Google CSE)."""

    provider_id = "cultural:open_culture"
    label = "Open Culture"
    group = "Canonical & cultural"
    library_connector = "open_culture"
    hint = "Recent ~300 posts only (no search API)"
    bang = "oc"
    default_rights = OPEN_RIGHTS

    API = "https://www.openculture.com/wp-json/wp/v2/posts"
    #: Pages of 100 to pull when building the local window.
    WINDOW_PAGES = 3
    # Asking for full post bodies makes the endpoint hang and return nothing;
    # `_fields` keeps each page to ~130KB and ~2s. Excerpts are what we match on.
    _FIELDS = "id,link,date,modified,slug,title,excerpt"

    def _load_records(self) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        for page in range(1, self.WINDOW_PAGES + 1):
            try:
                batch = fetch_json(
                    self.API,
                    params={"per_page": 100, "page": page, "_fields": self._FIELDS},
                )
            except Exception:  # noqa: BLE001 — partial window beats no window
                break
            if not isinstance(batch, list) or not batch:
                break
            posts.extend(batch)
            if len(batch) < 100:
                break
        return posts

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        terms = tokenize(query)
        if not terms:
            return []
        posts = _cached_index(self.provider_id, self._load_records)
        scored: list[tuple[int, dict[str, Any]]] = []
        for post in posts:
            title = strip_html((post.get("title") or {}).get("rendered"))
            excerpt = strip_html((post.get("excerpt") or {}).get("rendered"))
            hits = len(find_term_matches(" ".join((title, excerpt)), terms))
            if hits:
                scored.append((hits, post))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [self._normalize(p) for _, p in scored[:limit]]

    def _normalize(self, post: dict[str, Any]) -> NormalizedItem:
        title = strip_html((post.get("title") or {}).get("rendered")) or None
        excerpt = strip_html((post.get("excerpt") or {}).get("rendered")) or None
        content = excerpt
        return NormalizedItem(
            connector=self.library_connector,
            source_id=str(post.get("id")),
            source_url=post.get("link"),
            title=title,
            summary=excerpt[:600] if excerpt else None,
            fulltext="\n\n".join(p for p in (title, content) if p) or None,
            content_type="article",
            created_at=post.get("date"),
            updated_at=post.get("modified"),
            tags=["open-culture"],
            metadata={"post_id": post.get("id"), "slug": post.get("slug")},
            rights=dict(self.default_rights),
        )


def build_cultural_providers() -> list[SearchProvider]:
    return [PublicDomainReviewProvider(), OpenCultureProvider()]
