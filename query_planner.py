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


class SearchMode(str, Enum):
    """Optional exploratory search modes exposed by UI presets."""

    STANDARD = "standard"
    SEED_AND_MUTATE = "seed_and_mutate"
    CONTRARIAN = "contrarian"
    TIME_TUNNEL = "time_tunnel"
    MATERIALITY = "materiality"


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
    mode: SearchMode = SearchMode.STANDARD
    search_instructions: Sequence[str] = field(default_factory=tuple)
    toggles: PlannerToggles = field(default_factory=PlannerToggles)
    debug_notes: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class SearchModePreset:
    """Serializable preset metadata for one-click UI mode buttons."""

    mode: SearchMode
    label: str
    description: str


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

SEARCH_MODE_PRESETS: Sequence[SearchModePreset] = (
    SearchModePreset(
        mode=SearchMode.SEED_AND_MUTATE,
        label="Seed-and-mutate",
        description="Start from one URL/image/note and evolve adjacent paths.",
    ),
    SearchModePreset(
        mode=SearchMode.CONTRARIAN,
        label="Contrarian",
        description="Intentionally include opposing aesthetics and arguments.",
    ),
    SearchModePreset(
        mode=SearchMode.TIME_TUNNEL,
        label="Time tunnel",
        description="Track the same concept across multiple decades.",
    ),
    SearchModePreset(
        mode=SearchMode.MATERIALITY,
        label="Materiality",
        description="Prioritize scans, marginalia, ephemera, and archival traces.",
    ),
)

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
    mode: SearchMode = SearchMode.STANDARD,
) -> PlannerDecision:
    """Build a connector plan for a query and log debugging details."""

    toggles = toggles or PlannerToggles()
    debug_notes: List[str] = []
    search_instructions: List[str] = []

    intent = classify_query_intent(query)
    debug_notes.append(f"intent={intent.value}")
    debug_notes.append(f"mode={mode.value}")

    if toggles.visual_only:
        intent = QueryIntent.VISUAL
        debug_notes.append("toggle.visual_only=true -> forcing visual intent")

    connector_groups = list(INTENT_CONNECTOR_GROUPS[intent])
    debug_notes.append(
        "base_connectors=" + ",".join(connector_groups)
    )

    if mode == SearchMode.SEED_AND_MUTATE:
        _append_unique(
            connector_groups,
            ["bookmarks", "highlights", "tumblr", "internet_archive"],
        )
        search_instructions.append(
            "Start from one seed artifact (URL/image/note) and iteratively branch to related references."
        )
        debug_notes.append("mode.seed_and_mutate=true -> added chainable memory+visual+archive connectors")
    elif mode == SearchMode.CONTRARIAN:
        _append_unique(connector_groups, INTENT_CONNECTOR_GROUPS[QueryIntent.ACADEMIC])
        _append_unique(connector_groups, INTENT_CONNECTOR_GROUPS[QueryIntent.VISUAL])
        search_instructions.append(
            "Surface opposing aesthetics, dissenting arguments, and counterexamples alongside primary matches."
        )
        debug_notes.append("mode.contrarian=true -> added opposing-lens academic+visual expansion")
    elif mode == SearchMode.TIME_TUNNEL:
        _append_unique(connector_groups, INTENT_CONNECTOR_GROUPS[QueryIntent.ACADEMIC])
        _append_unique(connector_groups, INTENT_CONNECTOR_GROUPS[QueryIntent.CANONICAL_CULTURAL])
        search_instructions.append(
            "Map the same concept across decades, including at least one source per period."
        )
        debug_notes.append("mode.time_tunnel=true -> expanded temporal academic+canonical coverage")
    elif mode == SearchMode.MATERIALITY:
        _prioritize_connectors(
            connector_groups,
            ["internet_archive", "public_domain_review", "open_culture", "local_notes"],
        )
        _append_unique(connector_groups, ["internet_archive", "local_notes", "bookmarks"])
        search_instructions.append(
            "Prioritize scans, marginalia, ephemera, archival records, and material metadata."
        )
        debug_notes.append("mode.materiality=true -> prioritized archive/ephemera-first connector ordering")

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
        mode=mode,
        search_instructions=tuple(search_instructions),
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


def _prioritize_connectors(target: List[str], priority: Sequence[str]) -> None:
    """Move prioritized connector ids to the front while preserving relative order."""

    ranked = [connector for connector in priority if connector in target]
    remainder = [connector for connector in target if connector not in ranked]
    target[:] = ranked + remainder


def get_search_mode_presets() -> Sequence[SearchModePreset]:
    """Expose one-click search mode presets for UI consumption."""

    return SEARCH_MODE_PRESETS
