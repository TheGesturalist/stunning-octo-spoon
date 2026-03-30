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
from typing import Dict, Iterable, List, Mapping, Sequence


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


@dataclass(frozen=True)
class RankingSliders:
    """UI-exposed ranking sliders normalized in range [0.0, 1.0].

    Slider semantics:
      - relevant_surprising: 0.0 = Relevant, 1.0 = Surprising
      - focused_diverse: 0.0 = Focused, 1.0 = Diverse
      - recent_timeless: 0.0 = Recent, 1.0 = Timeless
    """

    relevant_surprising: float = 0.5
    focused_diverse: float = 0.5
    recent_timeless: float = 0.5

    def clamped(self) -> "RankingSliders":
        """Return a copy with all slider values clamped to [0.0, 1.0]."""

        return RankingSliders(
            relevant_surprising=_clamp01(self.relevant_surprising),
            focused_diverse=_clamp01(self.focused_diverse),
            recent_timeless=_clamp01(self.recent_timeless),
        )


@dataclass(frozen=True)
class RankWeights:
    """Weighted score components used by rank scoring."""

    lexical_match: float
    semantic_match: float
    recency: float
    novelty: float
    source_diversity_bonus: float

    def as_dict(self) -> Dict[str, float]:
        return {
            "lexical_match": self.lexical_match,
            "semantic_match": self.semantic_match,
            "recency": self.recency,
            "novelty": self.novelty,
            "source_diversity_bonus": self.source_diversity_bonus,
        }


@dataclass(frozen=True)
class RankCandidate:
    """Candidate result with independent normalized component scores."""

    id: str
    source: str
    lexical_match: float
    semantic_match: float
    recency: float
    novelty: float


@dataclass(frozen=True)
class RankedCandidate:
    """Ranked candidate with final score and score components for debugging."""

    candidate: RankCandidate
    weighted_score: float
    diversity_bonus: float
    final_score: float


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


def ranking_slider_config() -> Mapping[str, Mapping[str, float | str]]:
    """Return UI slider metadata for presentation controls."""

    return {
        "relevant_surprising": {
            "label_left": "Relevant",
            "label_right": "Surprising",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
        },
        "focused_diverse": {
            "label_left": "Focused",
            "label_right": "Diverse",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
        },
        "recent_timeless": {
            "label_left": "Recent",
            "label_right": "Timeless",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
        },
    }


def compute_rank_weights(sliders: RankingSliders) -> RankWeights:
    """Compute normalized component weights from UI slider values."""

    s = sliders.clamped()

    relevance = 1.0 - s.relevant_surprising
    surprising = s.relevant_surprising

    focused = 1.0 - s.focused_diverse
    diverse = s.focused_diverse

    recent = 1.0 - s.recent_timeless
    timeless = s.recent_timeless

    raw = {
        "lexical_match": 0.34 * relevance + 0.12 * focused,
        "semantic_match": 0.30 * relevance + 0.08 * focused + 0.08 * timeless,
        "recency": 0.30 * recent,
        "novelty": 0.28 * surprising + 0.06 * diverse + 0.04 * timeless,
        "source_diversity_bonus": 0.12 * diverse + 0.03 * surprising,
    }

    total = sum(raw.values())
    return RankWeights(
        lexical_match=raw["lexical_match"] / total,
        semantic_match=raw["semantic_match"] / total,
        recency=raw["recency"] / total,
        novelty=raw["novelty"] / total,
        source_diversity_bonus=raw["source_diversity_bonus"] / total,
    )


def rank_candidates(
    candidates: Sequence[RankCandidate],
    sliders: RankingSliders | None = None,
) -> Sequence[RankedCandidate]:
    """Rank candidates by weighted components and diversity-aware reweighting.

    Diversity scoring discourages a top-N monoculture from a single source by
    applying a decreasing source bonus when a source is already highly represented.
    """

    sliders = sliders or RankingSliders()
    weights = compute_rank_weights(sliders)

    weighted: List[RankedCandidate] = []
    for candidate in candidates:
        base_score = (
            weights.lexical_match * _clamp01(candidate.lexical_match)
            + weights.semantic_match * _clamp01(candidate.semantic_match)
            + weights.recency * _clamp01(candidate.recency)
            + weights.novelty * _clamp01(candidate.novelty)
        )
        weighted.append(
            RankedCandidate(
                candidate=candidate,
                weighted_score=base_score,
                diversity_bonus=0.0,
                final_score=base_score,
            )
        )

    source_counts: Dict[str, int] = {}
    reranked: List[RankedCandidate] = []

    for ranked in sorted(weighted, key=lambda item: item.weighted_score, reverse=True):
        prior_count = source_counts.get(ranked.candidate.source, 0)
        diminishing_multiplier = 1.0 / (1.0 + prior_count)
        diversity_bonus = weights.source_diversity_bonus * diminishing_multiplier
        final_score = ranked.weighted_score + diversity_bonus

        reranked.append(
            RankedCandidate(
                candidate=ranked.candidate,
                weighted_score=ranked.weighted_score,
                diversity_bonus=diversity_bonus,
                final_score=final_score,
            )
        )
        source_counts[ranked.candidate.source] = prior_count + 1

    return tuple(sorted(reranked, key=lambda item: item.final_score, reverse=True))


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))
