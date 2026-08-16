"""Federated search: fan out to many providers, rank the union.

This is the piece that finally puts `query_planner` on the search path. Local
library hits and live web hits are scored with the *same* primitives
(`local_index_service`), turned into `RankCandidate`s, and ranked by
`query_planner.rank_candidates` under the owner's sliders and per-source dials.

Design rules:
- **No provider can break a search.** Every failure is caught, reported in
  `outcomes`, and the remaining sources still return.
- **Nothing enters the library implicitly.** Live results are cached in
  `web_cache_items`; promotion into `normalized_items` is an explicit act.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from connectors.schema import NormalizedItem
from connectors.storage import (
    cache_web_results,
    ensure_web_cache,
    read_cached_web_results,
)
from local_index_service import (
    build_snippet,
    cosine_similarity,
    find_term_matches,
    term_freq,
    tokenize,
)
from providers.base import SearchProvider
from query_planner import (
    RankCandidate,
    RankingSliders,
    UserPreferenceVector,
    compute_rank_weights,
    rank_candidates,
)

DEFAULT_CACHE_MAX_AGE = 900.0  # 15 minutes
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_WORKERS = 6

# Half-life for the recency component. Research material ages slowly, so a
# five-year-old paper should not be buried by a blog post from last week.
_RECENCY_HALF_LIFE_DAYS = 365.0 * 5

_BANG_RE = re.compile(r"(?:^|\s)!([a-z0-9_]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SourceOutcome:
    """Per-provider report — surfaced in the UI so failures are never silent."""

    provider_id: str
    label: str
    ok: bool
    count: int = 0
    cached: bool = False
    error: str | None = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "label": self.label,
            "ok": self.ok,
            "count": self.count,
            "cached": self.cached,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class FederatedHit:
    item: NormalizedItem
    provider_id: str
    provider_label: str
    group: str
    origin: str  # "library" | "web"
    score: float
    component_scores: Mapping[str, float]
    snippet: str
    in_library: bool

    def to_dict(self) -> dict[str, Any]:
        item = self.item
        return {
            "provider_id": self.provider_id,
            "provider_label": self.provider_label,
            "group": self.group,
            "origin": self.origin,
            "score": round(self.score, 5),
            "component_scores": {k: round(v, 4) for k, v in self.component_scores.items()},
            "snippet": self.snippet,
            "in_library": self.in_library,
            "connector": item.connector,
            "source_id": item.source_id,
            "title": item.title,
            "author": item.author,
            "summary": item.summary,
            "source_url": item.source_url,
            "content_type": item.content_type,
            "created_at": item.created_at,
            "tags": list(item.tags),
            "metadata": dict(item.metadata),
        }


@dataclass(frozen=True)
class FederatedResponse:
    query: str
    plain_query: str
    hits: Sequence[FederatedHit] = field(default_factory=tuple)
    outcomes: Sequence[SourceOutcome] = field(default_factory=tuple)
    weights: Mapping[str, float] = field(default_factory=dict)
    bangs: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "plain_query": self.plain_query,
            "results": [h.to_dict() for h in self.hits],
            "outcomes": [o.to_dict() for o in self.outcomes],
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "bangs": list(self.bangs),
        }


# ---------------------------------------------------------------------------
# Query syntax: !bang selects sources inline, mirroring the Research Console
# ---------------------------------------------------------------------------


def parse_bangs(query: str, bang_map: Mapping[str, str]) -> tuple[str, list[str]]:
    """Split `!wphd deletion` into ("deletion", ["wikimedia:wphd"]).

    Unknown bangs are left in the query text rather than silently dropped, so a
    typo degrades to a literal search instead of a wrong source selection.
    """

    selected: list[str] = []
    consumed: list[str] = []
    for match in _BANG_RE.finditer(query):
        token = match.group(1).lower()
        provider_id = bang_map.get(token)
        if provider_id:
            if provider_id not in selected:
                selected.append(provider_id)
            consumed.append(match.group(0))
    plain = query
    for token in consumed:
        plain = plain.replace(token, " ", 1)
    return " ".join(plain.split()), selected


# ---------------------------------------------------------------------------
# Scoring — identical treatment for library and web results
# ---------------------------------------------------------------------------


def _searchable_text(item: NormalizedItem) -> str:
    return " ".join(p for p in (item.title, item.summary, item.fulltext) if p)


def lexical_score(text: str, query_terms: Sequence[str]) -> float:
    """Term coverage plus a bounded density bonus, in [0, 1]."""

    if not query_terms or not text:
        return 0.0
    matches = find_term_matches(text, query_terms)
    if not matches:
        return 0.0
    distinct = {m.term for m in matches}
    coverage = len(distinct) / len(set(query_terms))
    density = min(1.0, len(matches) / 10.0)
    return 0.7 * coverage + 0.3 * density


#: Token count at which a document is treated as carrying full evidence.
_LENGTH_CONFIDENCE_REFERENCE = 30.0


def length_confidence(token_count: int) -> float:
    """Discount cosine similarity for very short documents, in (0, 1].

    A one-word Are.na channel called "cartography" scores a perfect 1.0 cosine
    against the query "cartography" — not because it is the best answer, but
    because it has no other words to dilute the vector. Without this, short
    titles sweep the top of every result page ahead of substantial articles.
    """

    if token_count <= 0:
        return 0.0
    return min(1.0, math.log1p(token_count) / math.log1p(_LENGTH_CONFIDENCE_REFERENCE))


def recency_score(created_at: str | None) -> float:
    """Exponential decay on age; 0.5 when the date is unknown or unparseable."""

    stamp = _parse_date(created_at)
    if stamp is None:
        return 0.5
    age_days = max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 86400.0)
    return math.exp(-age_days / _RECENCY_HALF_LIFE_DAYS)


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y-%m",
    "%Y",
)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------


def federated_search(
    query: str,
    providers: Sequence[SearchProvider],
    *,
    sliders: RankingSliders | None = None,
    source_weights: Mapping[str, float] | None = None,
    limit: int = 20,
    per_source_limit: int | None = None,
    db_path: str | None = None,
    cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
    library_urls: Iterable[str] | None = None,
) -> FederatedResponse:
    """Query every selected provider concurrently and rank the union."""

    sliders = sliders or RankingSliders()
    plain_query = " ".join(query.split())
    terms = tokenize(plain_query)
    if not terms or not providers:
        return FederatedResponse(
            query=query,
            plain_query=plain_query,
            weights=compute_rank_weights(sliders, _preference_vector(source_weights)),
        )

    per_source = per_source_limit or max(5, min(limit, 25))
    known_urls = {u for u in (library_urls or ()) if u}

    # Run the DDL once, on this thread, before any worker touches the DB.
    if db_path:
        try:
            ensure_web_cache(db_path)
        except Exception:  # noqa: BLE001 — caching is optional; search still runs
            db_path = None

    outcomes: list[SourceOutcome] = []
    collected: list[tuple[SearchProvider, NormalizedItem]] = []

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(
                _run_provider, provider, plain_query, per_source, db_path, cache_max_age
            ): provider
            for provider in providers
        }
        for future in as_completed(futures, timeout=None):
            provider = futures[future]
            try:
                items, cached, elapsed_ms = future.result(timeout=timeout)
            except Exception as exc:  # noqa: BLE001 — one source never sinks the search
                outcomes.append(
                    SourceOutcome(
                        provider_id=provider.provider_id,
                        label=provider.label,
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            outcomes.append(
                SourceOutcome(
                    provider_id=provider.provider_id,
                    label=provider.label,
                    ok=True,
                    count=len(items),
                    cached=cached,
                    elapsed_ms=elapsed_ms,
                )
            )
            collected.extend((provider, item) for item in items)

    hits = _rank(
        collected,
        terms=terms,
        sliders=sliders,
        source_weights=source_weights,
        known_urls=known_urls,
        limit=limit,
    )
    outcomes.sort(key=lambda o: (not o.ok, -o.count, o.label))
    return FederatedResponse(
        query=query,
        plain_query=plain_query,
        hits=tuple(hits),
        outcomes=tuple(outcomes),
        weights=compute_rank_weights(sliders, _preference_vector(source_weights)),
    )


def _run_provider(
    provider: SearchProvider,
    query: str,
    limit: int,
    db_path: str | None,
    cache_max_age: float,
) -> tuple[list[NormalizedItem], bool, int]:
    """Cache-first execution of one provider. Library providers never cache."""

    started = datetime.now(timezone.utc)
    is_library = provider.provider_id.startswith("library:")

    if db_path and not is_library:
        cached = read_cached_web_results(
            db_path,
            provider_id=provider.provider_id,
            query=query,
            max_age_seconds=cache_max_age,
        )
        if cached is not None:
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            return cached[:limit], True, elapsed

    items = provider.search(query, limit=limit)

    if db_path and not is_library and items:
        try:
            cache_web_results(
                db_path, provider_id=provider.provider_id, query=query, items=items
            )
        except Exception:  # noqa: BLE001 — caching is an optimization, not a contract
            pass

    elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return items, False, elapsed


def _preference_vector(
    source_weights: Mapping[str, float] | None,
) -> UserPreferenceVector | None:
    """Per-source dials ride on the planner's existing source-trust channel."""

    if not source_weights:
        return None
    trimmed = {
        key: max(-1.0, min(1.0, float(value)))
        for key, value in source_weights.items()
        if value
    }
    if not trimmed:
        return None
    return UserPreferenceVector(source_trust=trimmed)


#: Score penalty applied per already-selected result from the same source when
#: the Focused↔Diverse slider is fully to "diverse".
_MAX_CROWDING_PENALTY = 0.25


def _diversify(ranked: Sequence[Any], focused_diverse: float, limit: int) -> list[Any]:
    """Greedy MMR-style interleave so one prolific source can't take every slot.

    `query_planner.rank_candidates` already has a diversity component, but it
    scores `1 / results_from_this_source` — identical for every source that
    returned the same number of rows, so it cannot break a tie between them. In
    practice a query where four sources all answered would still show six
    Wikipedia hits. This pass penalizes each *additional* pick from a source
    already represented, which is what the "Focused ↔ Diverse" slider promises.
    """

    if not ranked:
        return []
    penalty = _MAX_CROWDING_PENALTY * max(0.0, min(1.0, focused_diverse))
    if penalty <= 0:
        return list(ranked[:limit])

    remaining = list(ranked)
    chosen: list[Any] = []
    seen: dict[str, int] = {}
    while remaining and len(chosen) < limit:
        best_idx = 0
        best_value = float("-inf")
        for idx, entry in enumerate(remaining):
            source = entry.candidate.source_id
            value = entry.score - penalty * seen.get(source, 0)
            if value > best_value:
                best_value, best_idx = value, idx
        entry = remaining.pop(best_idx)
        seen[entry.candidate.source_id] = seen.get(entry.candidate.source_id, 0) + 1
        chosen.append(entry)
    return chosen


def _rank(
    collected: Sequence[tuple[SearchProvider, NormalizedItem]],
    *,
    terms: Sequence[str],
    sliders: RankingSliders,
    source_weights: Mapping[str, float] | None,
    known_urls: set[str],
    limit: int,
) -> list[FederatedHit]:
    if not collected:
        return []

    query_vec = term_freq(terms)
    candidates: list[RankCandidate] = []
    context: dict[str, tuple[SearchProvider, NormalizedItem, str, bool]] = {}

    for provider, item in collected:
        text = _searchable_text(item)
        candidate_id = f"{provider.provider_id}|{item.source_id}"
        if candidate_id in context:
            continue
        in_library = bool(item.source_url and item.source_url in known_urls)
        is_library = provider.provider_id.startswith("library:")
        doc_tokens = tokenize(text)
        semantic = cosine_similarity(query_vec, term_freq(doc_tokens))
        candidates.append(
            RankCandidate(
                candidate_id=candidate_id,
                lexical_score=lexical_score(text, terms),
                semantic_score=semantic * length_confidence(len(doc_tokens)),
                recency_score=recency_score(item.created_at),
                # Already-held material is not a discovery; live finds are.
                novelty_score=0.3 if (is_library or in_library) else 1.0,
                source_id=provider.provider_id,
                topics=tuple(item.tags),
                tags=tuple(item.tags),
            )
        )
        snippet = build_snippet(text, find_term_matches(text, terms))
        context[candidate_id] = (provider, item, snippet, in_library)

    ranked = rank_candidates(candidates, sliders, _preference_vector(source_weights))
    ranked = _diversify(ranked, sliders.focused_diverse, limit)

    hits: list[FederatedHit] = []
    for entry in ranked:
        provider, item, snippet, in_library = context[entry.candidate.candidate_id]
        hits.append(
            FederatedHit(
                item=item,
                provider_id=provider.provider_id,
                provider_label=provider.label,
                group=provider.group,
                origin="library" if provider.provider_id.startswith("library:") else "web",
                score=entry.score,
                component_scores=entry.component_scores,
                snippet=snippet,
                in_library=in_library,
            )
        )
    return hits
