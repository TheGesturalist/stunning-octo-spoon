"""Query planner for intent classification and connector-group routing.

This module provides a lightweight planner that:
1. Classifies query intent from query text.
2. Maps intent to connector groups.
3. Applies optional user toggles (deep search, fast search, visual-only).
4. Logs decision traces for debugging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Dict, Iterable, List, Sequence


logger = logging.getLogger(__name__)


class QueryIntent(str, Enum):
    """Supported top-level intents."""

    ACADEMIC = "academic"
    VISUAL = "visual"
    CANONICAL_CULTURAL = "canonical_cultural"
    PERSONAL_MEMORY = "personal_memory"


@dataclass(frozen=True)
class PlannerToggles:
    """Optional planner behavior toggles."""

    deep_search: bool = False
    fast_search: bool = False
    visual_only: bool = False


@dataclass(frozen=True)
class PlannerDecision:
    """Planner output plus debugging notes."""

    query: str
    intent: QueryIntent
    connector_groups: Sequence[str]
    toggles: PlannerToggles = field(default_factory=PlannerToggles)
    debug_notes: Sequence[str] = field(default_factory=tuple)


INTENT_CONNECTOR_GROUPS: Dict[QueryIntent, List[str]] = {
    QueryIntent.ACADEMIC: ["library_indexes", "academic_databases"],
    QueryIntent.VISUAL: ["pinterest", "are_na", "cosmos", "tumblr"],
    QueryIntent.CANONICAL_CULTURAL: [
        "open_culture",
        "public_domain_review",
        "internet_archive",
    ],
    QueryIntent.PERSONAL_MEMORY: ["local_notes", "bookmarks", "highlights"],
}

# Ordered precedence used when multiple keyword buckets match.
_INTENT_ORDER: Sequence[QueryIntent] = (
    QueryIntent.VISUAL,
    QueryIntent.ACADEMIC,
    QueryIntent.CANONICAL_CULTURAL,
    QueryIntent.PERSONAL_MEMORY,
)

_INTENT_KEYWORDS: Dict[QueryIntent, Sequence[str]] = {
    QueryIntent.ACADEMIC: (
        "paper",
        "journal",
        "citation",
        "doi",
        "thesis",
        "research",
        "scholar",
        "peer review",
        "literature review",
    ),
    QueryIntent.VISUAL: (
        "image",
        "moodboard",
        "aesthetic",
        "visual",
        "design reference",
        "lookbook",
        "color",
        "photo",
    ),
    QueryIntent.CANONICAL_CULTURAL: (
        "canonical",
        "public domain",
        "archive",
        "historical",
        "cultural",
        "classic",
        "primary source",
        "open culture",
    ),
    QueryIntent.PERSONAL_MEMORY: (
        "my notes",
        "my bookmarks",
        "my highlights",
        "saved",
        "remember",
        "what did i read",
        "my library",
    ),
}


def classify_query_intent(query: str) -> QueryIntent:
    """Classify query intent via keyword heuristics.

    Fallback behavior:
      - If no keyword matches, default to CANONICAL_CULTURAL.
    """

    normalized = query.strip().lower()

    scores: Dict[QueryIntent, int] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for keyword in keywords if keyword in normalized)

    best_score = max(scores.values(), default=0)
    if best_score == 0:
        return QueryIntent.CANONICAL_CULTURAL

    candidates = {intent for intent, score in scores.items() if score == best_score}
    for intent in _INTENT_ORDER:
        if intent in candidates:
            return intent

    # Defensive fallback if order gets out of sync.
    return QueryIntent.CANONICAL_CULTURAL


def plan_query(
    query: str,
    toggles: PlannerToggles | None = None,
) -> PlannerDecision:
    """Build a connector plan for a query and log debugging details."""

    toggles = toggles or PlannerToggles()
    debug_notes: List[str] = []

    intent = classify_query_intent(query)
    debug_notes.append(f"intent={intent.value}")

    if toggles.visual_only:
        intent = QueryIntent.VISUAL
        debug_notes.append("toggle.visual_only=true -> forcing visual intent")

    connector_groups = list(INTENT_CONNECTOR_GROUPS[intent])
    debug_notes.append(
        "base_connectors=" + ",".join(connector_groups)
    )

    if toggles.fast_search and toggles.deep_search:
        # Fast search takes precedence for latency-sensitive behavior.
        toggles = PlannerToggles(
            deep_search=False,
            fast_search=True,
            visual_only=toggles.visual_only,
        )
        debug_notes.append(
            "toggle_conflict=deep_search+fast_search -> prioritizing fast_search"
        )

    if toggles.deep_search:
        # Add canonical backfill for richer result depth.
        _append_unique(connector_groups, INTENT_CONNECTOR_GROUPS[QueryIntent.CANONICAL_CULTURAL])
        debug_notes.append("toggle.deep_search=true -> added canonical/cultural backfill")

    if toggles.fast_search:
        connector_groups = connector_groups[:2]
        debug_notes.append("toggle.fast_search=true -> truncated connector list to top 2")

    logger.debug(
        "query_planner decision | query=%r intent=%s toggles=%s connectors=%s notes=%s",
        query,
        intent.value,
        toggles,
        connector_groups,
        debug_notes,
    )

    return PlannerDecision(
        query=query,
        intent=intent,
        connector_groups=tuple(connector_groups),
        toggles=toggles,
        debug_notes=tuple(debug_notes),
    )


def _append_unique(target: List[str], values: Iterable[str]) -> None:
    """Append values to target list while preserving uniqueness and order."""

    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)
