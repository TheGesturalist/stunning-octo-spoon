"""Open academic search providers — all keyless.

OpenAlex is the backbone (widest coverage, open metadata, no key). Crossref,
arXiv and DOAJ sit alongside it because each catches something OpenAlex ranks
poorly: registered DOIs, preprints, and fully open-access journal articles.

Google Scholar is deliberately absent — it publishes no API and prohibits
automated querying, so there is no honest way to include it as a search source.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

from connectors.schema import NormalizedItem

from ._http import fetch_json, fetch_xml, strip_html
from .base import ABSTRACT_ONLY_RIGHTS, OPEN_RIGHTS, SearchProvider

# Both OpenAlex and Crossref give faster, more reliable service to requests that
# identify a contact address ("polite pool"). Optional — omitted if unset.
_MAILTO = os.environ.get("SPOON_ACADEMIC_MAILTO") or None


def _joined_authors(names: list[str], limit: int = 8) -> str | None:
    names = [n for n in names if n]
    if not names:
        return None
    shown = names[:limit]
    suffix = " et al." if len(names) > limit else ""
    return ", ".join(shown) + suffix


class OpenAlexProvider(SearchProvider):
    """OpenAlex — ~250M scholarly works, open metadata, no API key."""

    provider_id = "academic:openalex"
    label = "OpenAlex"
    group = "Academic"
    library_connector = "openalex"
    hint = "~250M works · open metadata"
    bang = "oa"
    default_rights = OPEN_RIGHTS

    API = "https://api.openalex.org/works"

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        if not query.strip():
            return []
        payload = fetch_json(
            self.API,
            params={
                "search": query,
                "per-page": max(1, min(limit, 50)),
                "mailto": _MAILTO,
            },
        )
        return [self._normalize(w) for w in (payload.get("results") or [])[:limit]]

    @staticmethod
    def _abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
        """OpenAlex ships abstracts as an inverted index; rebuild the prose."""

        if not inverted_index:
            return None
        positions: list[tuple[int, str]] = []
        for word, locations in inverted_index.items():
            for loc in locations:
                positions.append((loc, word))
        if not positions:
            return None
        positions.sort()
        return " ".join(word for _, word in positions)

    def _normalize(self, work: dict[str, Any]) -> NormalizedItem:
        openalex_id = str(work.get("id") or "")
        short_id = openalex_id.rsplit("/", 1)[-1] if openalex_id else ""
        title = work.get("display_name") or work.get("title")
        abstract = self._abstract(work.get("abstract_inverted_index"))
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in (work.get("authorships") or [])
        ]
        location = work.get("primary_location") or {}
        venue = (location.get("source") or {}).get("display_name")
        open_access = work.get("open_access") or {}
        url = (
            open_access.get("oa_url")
            or location.get("landing_page_url")
            or work.get("doi")
            or openalex_id
        )
        return NormalizedItem(
            connector=self.library_connector,
            source_id=short_id or (work.get("doi") or title or "")[:120],
            source_url=url,
            title=title,
            author=_joined_authors([a for a in authors if a]),
            summary=abstract[:600] if abstract else None,
            fulltext="\n\n".join(p for p in (title, abstract) if p) or None,
            content_type="academic_paper",
            language=work.get("language"),
            created_at=work.get("publication_date"),
            tags=["academic", "openalex"],
            metadata={
                "doi": work.get("doi"),
                "venue": venue,
                "year": work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count"),
                "is_oa": open_access.get("is_oa"),
                "work_type": work.get("type"),
            },
            rights=dict(self.default_rights),
        )


class CrossrefProvider(SearchProvider):
    """Crossref — the DOI registry. Strong on formally published work."""

    provider_id = "academic:crossref"
    label = "Crossref"
    group = "Academic"
    library_connector = "crossref"
    hint = "DOI registry · publisher metadata"
    bang = "cr"
    default_rights = ABSTRACT_ONLY_RIGHTS

    API = "https://api.crossref.org/works"

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        if not query.strip():
            return []
        payload = fetch_json(
            self.API,
            params={"query": query, "rows": max(1, min(limit, 50)), "mailto": _MAILTO},
        )
        items = ((payload or {}).get("message") or {}).get("items") or []
        return [self._normalize(item) for item in items[:limit]]

    def _normalize(self, work: dict[str, Any]) -> NormalizedItem:
        titles = work.get("title") or []
        title = titles[0] if titles else None
        # Crossref abstracts arrive as JATS XML.
        abstract = strip_html(work.get("abstract")) or None
        authors = [
            " ".join(p for p in (a.get("given"), a.get("family")) if p)
            for a in (work.get("author") or [])
        ]
        containers = work.get("container-title") or []
        date_parts = ((work.get("issued") or {}).get("date-parts") or [[]])[0]
        created = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(date_parts))
        return NormalizedItem(
            connector=self.library_connector,
            source_id=str(work.get("DOI") or title or "")[:200],
            source_url=work.get("URL"),
            title=title,
            author=_joined_authors([a for a in authors if a.strip()]),
            summary=abstract[:600] if abstract else None,
            fulltext="\n\n".join(p for p in (title, abstract) if p) or None,
            content_type="academic_paper",
            created_at=created or None,
            tags=["academic", "crossref"],
            metadata={
                "doi": work.get("DOI"),
                "venue": containers[0] if containers else None,
                "publisher": work.get("publisher"),
                "work_type": work.get("type"),
                "cited_by_count": work.get("is-referenced-by-count"),
            },
            rights=dict(self.default_rights),
        )


class ArxivProvider(SearchProvider):
    """arXiv — preprints, full abstracts, Atom API."""

    provider_id = "academic:arxiv"
    label = "arXiv"
    group = "Academic"
    library_connector = "arxiv"
    hint = "Preprints · physics, maths, CS, econ"
    bang = "ax"
    default_rights = OPEN_RIGHTS

    API = "https://export.arxiv.org/api/query"
    _ATOM = "{http://www.w3.org/2005/Atom}"

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        if not query.strip():
            return []
        root = fetch_xml(
            self.API,
            params={
                "search_query": f"all:{query}",
                "max_results": max(1, min(limit, 50)),
                "sortBy": "relevance",
            },
        )
        entries = root.findall(f"{self._ATOM}entry")[:limit]
        return [self._normalize(e) for e in entries]

    def _text(self, entry: Any, tag: str) -> str | None:
        node = entry.find(f"{self._ATOM}{tag}")
        return " ".join(node.text.split()) if node is not None and node.text else None

    def _normalize(self, entry: Any) -> NormalizedItem:
        entry_id = self._text(entry, "id") or ""
        title = self._text(entry, "title")
        abstract = self._text(entry, "summary")
        authors = [
            " ".join(n.text.split())
            for a in entry.findall(f"{self._ATOM}author")
            for n in [a.find(f"{self._ATOM}name")]
            if n is not None and n.text
        ]
        published = self._text(entry, "published")
        return NormalizedItem(
            connector=self.library_connector,
            source_id=entry_id.rsplit("/", 1)[-1] or (title or "")[:120],
            source_url=entry_id or None,
            title=title,
            author=_joined_authors(authors),
            summary=abstract[:600] if abstract else None,
            fulltext="\n\n".join(p for p in (title, abstract) if p) or None,
            content_type="preprint",
            created_at=published,
            updated_at=self._text(entry, "updated"),
            tags=["academic", "arxiv", "preprint"],
            metadata={"arxiv_id": entry_id.rsplit("/", 1)[-1]},
            rights=dict(self.default_rights),
        )


class DoajProvider(SearchProvider):
    """DOAJ — articles from vetted fully open-access journals."""

    provider_id = "academic:doaj"
    label = "DOAJ"
    group = "Academic"
    library_connector = "doaj"
    hint = "Open-access journal articles"
    bang = "doaj"
    default_rights = OPEN_RIGHTS

    API = "https://doaj.org/api/search/articles"

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        if not query.strip():
            return []
        # DOAJ takes the query in the path, not the query string.
        url = f"{self.API}/{urllib.parse.quote(query, safe='')}"
        payload = fetch_json(url, params={"pageSize": max(1, min(limit, 50))})
        return [
            self._normalize(r) for r in (payload.get("results") or [])[:limit]
        ]

    def _normalize(self, record: dict[str, Any]) -> NormalizedItem:
        bib = record.get("bibjson") or {}
        title = bib.get("title")
        abstract = bib.get("abstract")
        authors = [a.get("name") for a in (bib.get("author") or [])]
        links = bib.get("link") or []
        url = next((l.get("url") for l in links if l.get("url")), None)
        doi = next(
            (i.get("id") for i in (bib.get("identifier") or []) if i.get("type") == "doi"),
            None,
        )
        return NormalizedItem(
            connector=self.library_connector,
            source_id=str(record.get("id") or doi or (title or ""))[:200],
            source_url=url,
            title=title,
            author=_joined_authors([a for a in authors if a]),
            summary=abstract[:600] if abstract else None,
            fulltext="\n\n".join(p for p in (title, abstract) if p) or None,
            content_type="academic_paper",
            created_at=str(bib.get("year")) if bib.get("year") else None,
            tags=["academic", "doaj", "open-access"],
            metadata={
                "doi": doi,
                "venue": (bib.get("journal") or {}).get("title"),
                "year": bib.get("year"),
            },
            rights=dict(self.default_rights),
        )


def build_academic_providers() -> list[SearchProvider]:
    return [OpenAlexProvider(), CrossrefProvider(), ArxivProvider(), DoajProvider()]
