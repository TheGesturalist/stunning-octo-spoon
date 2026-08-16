"""The existing local corpus, exposed through the SearchProvider interface.

Wrapping the library as "just another provider" is what lets a Raindrop bookmark
and a Wikipedia page be scored by identical rules in `federation.py`, instead of
web results being bolted onto a separate local ranking.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from connectors.schema import NormalizedItem
from local_index_service import IndexedDocument, find_term_matches, tokenize

from .base import SearchProvider


class LibraryProvider(SearchProvider):
    """Search the owner's curated corpus (`normalized_items`)."""

    group = "Your library"
    default_rights: dict = {}

    def __init__(
        self,
        connector: str,
        docs: Sequence[IndexedDocument],
        *,
        label: str | None = None,
    ) -> None:
        self.connector = connector
        self.docs = tuple(docs)
        self.provider_id = f"library:{connector}"
        self.label = label or connector
        self.library_connector = connector
        self.hint = f"{len(self.docs):,} items"
        self.bang = None

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        terms = tokenize(query)
        if not terms:
            return []
        scored: list[tuple[int, IndexedDocument]] = []
        for doc in self.docs:
            haystack = " ".join(p for p in (doc.title, doc.abstract, doc.text) if p)
            hits = len(find_term_matches(haystack, terms))
            if hits:
                scored.append((hits, doc))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [self._normalize(doc) for _, doc in scored[:limit]]

    def _normalize(self, doc: IndexedDocument) -> NormalizedItem:
        connector, _, source_id = doc.doc_id.partition(":")
        return NormalizedItem(
            connector=connector or self.connector,
            source_id=source_id or doc.doc_id,
            source_url=doc.source,
            title=doc.title,
            summary=doc.abstract,
            fulltext=doc.text,
            content_type="library_item",
            created_at=doc.created_at,
            metadata={"origin": "library"},
            rights=dict(doc.rights),
        )


def build_library_providers(
    indexes: Mapping[str, Sequence[IndexedDocument]],
) -> list[LibraryProvider]:
    """One provider per connector, largest first (matches the nav ordering)."""

    ordered = sorted(indexes.items(), key=lambda kv: len(kv[1]), reverse=True)
    return [LibraryProvider(name, docs) for name, docs in ordered]
