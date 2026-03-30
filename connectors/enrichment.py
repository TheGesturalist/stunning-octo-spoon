"""Post-ingestion enrichment pipeline.

This module extracts lightweight semantic signals from normalized records and
projects them into:

- searchable facets
- graph edges
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .schema import NormalizedItem


_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}|[A-Z]{2,})\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "memory": ("memory", "archive", "archival", "trace", "history"),
    "materiality": ("paper", "print", "scan", "photocopy", "texture", "ephemera"),
    "politics": ("manifesto", "ideology", "power", "policy", "protest"),
    "identity": ("identity", "self", "gender", "race", "body"),
    "technology": ("software", "internet", "machine", "digital", "platform"),
    "ecology": ("ecology", "climate", "land", "nature", "environment"),
}

MEDIUM_STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "essay": ("essay", "longform", "argument"),
    "scan": ("scan", "scanned", "facsimile", "pdf"),
    "collage": ("collage", "cut-up", "montage", "assemblage"),
    "manifesto": ("manifesto", "theses", "declaration"),
    "poem": ("poem", "poetry", "verse"),
    "interview": ("interview", "q&a", "conversation"),
    "photo_set": ("photo", "photograph", "gallery", "image"),
}

MOOD_TONE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "urgent": ("urgent", "now", "immediately", "must"),
    "melancholic": ("melancholy", "loss", "nostalgia", "haunting"),
    "playful": ("playful", "humor", "whimsy", "joke"),
    "critical": ("critique", "critical", "analysis", "interrogate"),
    "hopeful": ("hope", "optimism", "repair", "future"),
    "defiant": ("resist", "refuse", "defiant", "rebellion"),
}


@dataclass(frozen=True)
class EnrichmentFacet:
    """Searchable facet produced by enrichment."""

    facet_type: str
    facet_value: str
    confidence: float


@dataclass(frozen=True)
class GraphEdge:
    """Directed relationship from an item to a semantic node."""

    edge_type: str
    target_node: str
    weight: float


@dataclass(frozen=True)
class EnrichmentResult:
    """Enrichment output to be persisted in the store."""

    facets: tuple[EnrichmentFacet, ...]
    edges: tuple[GraphEdge, ...]


def enrich_item(item: NormalizedItem) -> EnrichmentResult:
    """Extract entities/themes/style/mood and map them to facets + edges."""

    text = "\n".join(
        filter(
            None,
            [
                item.title,
                item.summary,
                item.fulltext,
                " ".join(item.tags),
            ],
        )
    )
    lowered = text.lower()
    tokens = _tokenize(lowered)

    entities = _extract_named_entities(text)
    themes = _match_categories(tokens, THEME_KEYWORDS)
    media_styles = _match_categories(tokens, MEDIUM_STYLE_KEYWORDS)
    mood_tones = _match_categories(tokens, MOOD_TONE_KEYWORDS)

    if item.content_type:
        media_styles.add(item.content_type.lower())

    facets: list[EnrichmentFacet] = []
    edges: list[GraphEdge] = []

    for entity in sorted(entities):
        confidence = 0.6 if " " in entity else 0.5
        facets.append(EnrichmentFacet("named_entity", entity, confidence))
        edges.append(GraphEdge("mentions_entity", f"entity:{entity}", confidence))

    for theme in sorted(themes):
        facets.append(EnrichmentFacet("theme", theme, 0.75))
        edges.append(GraphEdge("has_theme", f"theme:{theme}", 0.75))

    for tag in sorted(media_styles):
        facets.append(EnrichmentFacet("medium_style", tag, 0.8))
        edges.append(GraphEdge("has_medium_style", f"medium_style:{tag}", 0.8))

    for mood in sorted(mood_tones):
        facets.append(EnrichmentFacet("mood_tone", mood, 0.7))
        edges.append(GraphEdge("has_mood_tone", f"mood_tone:{mood}", 0.7))

    return EnrichmentResult(facets=tuple(facets), edges=tuple(edges))


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _extract_named_entities(text: str) -> set[str]:
    blocked = {"The", "This", "That", "A", "An", "And"}
    entities = {match.group(1).strip() for match in _ENTITY_RE.finditer(text)}
    return {entity for entity in entities if entity not in blocked and len(entity) > 1}


def _match_categories(tokens: Iterable[str], catalog: dict[str, tuple[str, ...]]) -> set[str]:
    token_set = set(tokens)
    found: set[str] = set()
    for label, keywords in catalog.items():
        if any(keyword in token_set for keyword in keywords):
            found.add(label)
    return found
