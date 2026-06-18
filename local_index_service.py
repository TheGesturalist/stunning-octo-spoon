"""Local index query service for lexical + semantic retrieval.

This module provides a small in-process search service that can query existing
full-text indexes and return result cards with:

- snippet highlights
- exact term match locations
- semantic nearest neighbors
- human-readable match explanations
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping, Sequence


_WORD_RE = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class IndexedDocument:
    """Document stored in a local full-text index."""

    doc_id: str
    title: str
    text: str
    source: str = "local"
    created_at: str | None = None
    abstract: str | None = None
    citation_metadata: Mapping[str, Any] = field(default_factory=dict)
    rights: Mapping[str, Any] = field(
        default_factory=lambda: {
            "allow_abstract": True,
            "allow_fulltext": True,
            "can_export": True,
            "export_policy": "full",
        }
    )


@dataclass(frozen=True)
class TermMatchLocation:
    """Exact term match location in a document."""

    term: str
    start: int
    end: int
    paragraph: int


@dataclass(frozen=True)
class NeighborMatch:
    """Semantic nearest-neighbor metadata."""

    doc_id: str
    title: str
    created_at: str | None
    similarity: float


@dataclass(frozen=True)
class ResultCard:
    """Search result card returned to the caller/UI."""

    doc_id: str
    title: str
    source: str
    snippet_highlight: str
    citation_metadata: Mapping[str, Any] = field(default_factory=dict)
    rights: Mapping[str, Any] = field(default_factory=dict)
    term_matches: Sequence[TermMatchLocation] = field(default_factory=tuple)
    semantic_neighbors: Sequence[NeighborMatch] = field(default_factory=tuple)
    match_explanations: Sequence[str] = field(default_factory=tuple)


class LocalIndexService:
    """Search service over existing full-text indexes."""

    def __init__(self, indexes: Mapping[str, Sequence[IndexedDocument]]) -> None:
        self._indexes = {name: tuple(docs) for name, docs in indexes.items()}

    def query(
        self,
        query_text: str,
        *,
        indexes: Iterable[str] | None = None,
        limit: int = 10,
        semantic_neighbors: int = 2,
    ) -> list[ResultCard]:
        """Query selected indexes and return ranked result cards."""

        selected = set(indexes) if indexes else set(self._indexes.keys())
        query_terms = _tokenize(query_text)
        if not query_terms:
            return []

        candidates: list[tuple[float, IndexedDocument, list[TermMatchLocation]]] = []
        for index_name, docs in self._indexes.items():
            if index_name not in selected:
                continue
            for doc in docs:
                searchable_text = _searchable_text_for_rights(doc)
                term_matches = _find_term_matches(searchable_text, query_terms)
                lexical_score = len(term_matches)
                semantic_score = _cosine_similarity(_term_freq(query_terms), _term_freq(_tokenize(searchable_text)))
                score = lexical_score + semantic_score
                if score > 0:
                    candidates.append((score, doc, term_matches))

        candidates.sort(key=lambda row: row[0], reverse=True)
        top = candidates[:limit]

        cards: list[ResultCard] = []
        for _, doc, term_matches in top:
            snippet = _build_snippet(_searchable_text_for_rights(doc), term_matches)
            neighbors = self._nearest_neighbors(doc, semantic_neighbors)
            explanations = _build_explanations(term_matches, neighbors)
            cards.append(
                ResultCard(
                    doc_id=doc.doc_id,
                    title=doc.title,
                    source=doc.source,
                    snippet_highlight=snippet,
                    citation_metadata=doc.citation_metadata,
                    rights=doc.rights,
                    term_matches=tuple(term_matches),
                    semantic_neighbors=tuple(neighbors),
                    match_explanations=tuple(explanations),
                )
            )

        return cards

    def _nearest_neighbors(self, target: IndexedDocument, count: int) -> list[NeighborMatch]:
        pool = [doc for docs in self._indexes.values() for doc in docs if doc.doc_id != target.doc_id]
        if not pool or count <= 0:
            return []

        target_vec = _term_freq(_tokenize(_searchable_text_for_rights(target)))
        scored: list[tuple[float, IndexedDocument]] = []
        for doc in pool:
            similarity = _cosine_similarity(
                target_vec, _term_freq(_tokenize(_searchable_text_for_rights(doc)))
            )
            if similarity > 0:
                scored.append((similarity, doc))

        scored.sort(key=lambda row: row[0], reverse=True)
        neighbors = [
            NeighborMatch(
                doc_id=doc.doc_id,
                title=doc.title,
                created_at=doc.created_at,
                similarity=similarity,
            )
            for similarity, doc in scored[:count]
        ]
        return neighbors


def export_result_cards(cards: Sequence[ResultCard]) -> list[dict[str, Any]]:
    """API-safe export payload that enforces document-level export rights."""

    exported: list[dict[str, Any]] = []
    for card in cards:
        if not bool(card.rights.get("can_export", False)):
            continue
        exported.append(
            {
                "doc_id": card.doc_id,
                "title": card.title,
                "source": card.source,
                "snippet_highlight": card.snippet_highlight,
                "citation_metadata": dict(card.citation_metadata),
                "rights": dict(card.rights),
            }
        )
    return exported


def _searchable_text_for_rights(doc: IndexedDocument) -> str:
    allow_fulltext = bool(doc.rights.get("allow_fulltext", False))
    allow_abstract = bool(doc.rights.get("allow_abstract", False))
    if allow_fulltext and doc.text:
        return doc.text
    if allow_abstract and doc.abstract:
        return doc.abstract
    return doc.title


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text)]


def _term_freq(tokens: Sequence[str]) -> dict[str, float]:
    counts: dict[str, float] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0.0) + 1.0
    return counts


def _cosine_similarity(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0

    dot = sum(left.get(term, 0.0) * right.get(term, 0.0) for term in set(left) | set(right))
    left_norm = math.sqrt(sum(v * v for v in left.values()))
    right_norm = math.sqrt(sum(v * v for v in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _find_term_matches(text: str, query_terms: Sequence[str]) -> list[TermMatchLocation]:
    matches: list[TermMatchLocation] = []
    lowered = text.lower()
    paragraph_ranges = _paragraph_ranges(text)

    for term in sorted(set(query_terms)):
        start = 0
        while True:
            hit = lowered.find(term, start)
            if hit == -1:
                break
            end = hit + len(term)
            paragraph = _paragraph_for_offset(hit, paragraph_ranges)
            matches.append(TermMatchLocation(term=term, start=hit, end=end, paragraph=paragraph))
            start = end

    matches.sort(key=lambda match: match.start)
    return matches


def _paragraph_ranges(text: str) -> list[tuple[int, int, int]]:
    ranges: list[tuple[int, int, int]] = []
    cursor = 0
    for idx, para in enumerate(text.split("\n\n"), start=1):
        end = cursor + len(para)
        ranges.append((cursor, end, idx))
        cursor = end + 2
    return ranges or [(0, len(text), 1)]


def _paragraph_for_offset(offset: int, ranges: Sequence[tuple[int, int, int]]) -> int:
    for start, end, paragraph in ranges:
        if start <= offset <= end:
            return paragraph
    return ranges[-1][2] if ranges else 1


def _build_snippet(text: str, matches: Sequence[TermMatchLocation], radius: int = 60) -> str:
    if not text:
        return ""
    if not matches:
        return text[: 2 * radius]

    first = matches[0]
    start = max(0, first.start - radius)
    end = min(len(text), first.end + radius)
    snippet = text[start:end]

    for match in sorted(matches, key=lambda m: m.start - start, reverse=True):
        local_start = match.start - start
        local_end = match.end - start
        if local_start < 0 or local_end > len(snippet):
            continue
        snippet = snippet[:local_start] + "<mark>" + snippet[local_start:local_end] + "</mark>" + snippet[local_end:]
    return snippet


def _build_explanations(matches: Sequence[TermMatchLocation], neighbors: Sequence[NeighborMatch]) -> list[str]:
    explanations: list[str] = []

    if matches:
        first = matches[0]
        explanations.append(f'Matched phrase in paragraph {first.paragraph} (exact term: "{first.term}").')

    for neighbor in neighbors[:2]:
        date_text = neighbor.created_at or "unknown date"
        explanations.append(f"Similar to note {neighbor.doc_id} from {date_text}.")

    return explanations
