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
from dataclasses import dataclass, field, replace
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
    SearchMode,
    UserPreferenceVector,
    compute_rank_weights,
    rank_candidates,
)
from search_modes import RERANKERS, ModePlan, mine_terms, plan_search_mode

DEFAULT_CACHE_MAX_AGE = 900.0  # 15 minutes
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_WORKERS = 6
# Ceiling on additional query passes a mode may request.
MAX_EXTRA_PASSES = 1

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
    mode: str = SearchMode.STANDARD.value
    mode_plan: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "plain_query": self.plain_query,
            "results": [h.to_dict() for h in self.hits],
            "outcomes": [o.to_dict() for o in self.outcomes],
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "bangs": list(self.bangs),
            "mode": self.mode,
            "mode_plan": dict(self.mode_plan) if self.mode_plan else None,
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
    mode: SearchMode = SearchMode.STANDARD,
    all_providers: Sequence[SearchProvider] | None = None,
) -> FederatedResponse:
    """Query every selected provider concurrently and rank the union.

    `mode` applies one of the exploratory presets (see `search_modes.py`), which
    may widen the source set, move the sliders, add a query pass, and re-rank.
    `all_providers` is the pool a mode may pull extra sources from; without it a
    mode can only work with what the caller already selected.
    """

    sliders = sliders or RankingSliders()
    plain_query = " ".join(query.split())
    terms = tokenize(plain_query)
    if not terms or not providers:
        return FederatedResponse(
            query=query,
            plain_query=plain_query,
            weights=compute_rank_weights(sliders, _preference_vector(source_weights)),
            mode=mode.value,
        )

    pool = list(all_providers or providers)
    plan = plan_search_mode(
        mode,
        query=plain_query,
        sliders=sliders,
        selected_ids=[p.provider_id for p in providers],
        available_ids=[p.provider_id for p in pool],
    )
    if plan.mode is not SearchMode.STANDARD:
        sliders = plan.sliders
        if plan.add_provider_ids:
            by_id = {p.provider_id: p for p in pool}
            providers = list(providers) + [
                by_id[pid] for pid in plan.add_provider_ids if pid in by_id
            ]
        if plan.source_weights:
            merged = dict(source_weights or {})
            for pid, weight in plan.source_weights.items():
                merged[pid] = max(-1.0, min(1.0, merged.get(pid, 0.0) + weight))
            source_weights = merged

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

    def fan_out(pass_query: str) -> list[tuple[SearchProvider, NormalizedItem]]:
        """One concurrent sweep of every provider for a single query string."""

        found: list[tuple[SearchProvider, NormalizedItem]] = []
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = {
                executor.submit(
                    _run_provider, provider, pass_query, per_source, db_path, cache_max_age
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
                found.extend((provider, item) for item in items)
        return found

    collected.extend(fan_out(plain_query))

    # A mode may widen the search with further passes. Each costs another round
    # of requests, so they are capped — and cache-backed on repeat.
    for extra in plan.extra_queries[:MAX_EXTRA_PASSES]:
        collected.extend(fan_out(extra))

    if plan.mutate:
        # Seed-and-mutate: let the first pass decide where to branch next.
        seed_hits = _rank(
            collected,
            terms=terms,
            sliders=sliders,
            source_weights=source_weights,
            known_urls=known_urls,
            limit=10,
        )
        mutated = mine_terms(seed_hits, exclude=terms)
        if mutated:
            plan = replace(plan, notes=plan.notes + (f"Mutated on: {', '.join(mutated)}",))
            collected.extend(fan_out(" ".join(mutated)))
            terms = list(terms) + mutated

    hits = _rank(
        collected,
        terms=terms,
        sliders=sliders,
        source_weights=source_weights,
        known_urls=known_urls,
        # Over-fetch when a re-ranker will reorder, so it has material to work with.
        limit=limit * 3 if plan.rerank else limit,
    )
    if plan.rerank:
        reranker = RERANKERS.get(plan.rerank)
        if reranker:
            hits = reranker(hits, limit)
    hits = list(hits)[:limit]

    return FederatedResponse(
        query=query,
        plain_query=plain_query,
        hits=tuple(hits),
        outcomes=tuple(_merge_outcomes(outcomes)),
        weights=compute_rank_weights(sliders, _preference_vector(source_weights)),
        mode=plan.mode.value,
        mode_plan=plan.to_dict() if plan.mode is not SearchMode.STANDARD else None,
    )


def _merge_outcomes(outcomes: Sequence[SourceOutcome]) -> list[SourceOutcome]:
    """Collapse per-pass outcomes to one row per source.

    A mode can run a source more than once; the UI strip should still show it
    once, with the totals.
    """

    merged: dict[str, SourceOutcome] = {}
    for outcome in outcomes:
        prior = merged.get(outcome.provider_id)
        if prior is None:
            merged[outcome.provider_id] = outcome
            continue
        merged[outcome.provider_id] = SourceOutcome(
            provider_id=outcome.provider_id,
            label=outcome.label,
            ok=prior.ok or outcome.ok,
            count=prior.count + outcome.count,
            cached=prior.cached and outcome.cached,
            error=prior.error or outcome.error,
            elapsed_ms=prior.elapsed_ms + outcome.elapsed_ms,
        )
    return sorted(merged.values(), key=lambda o: (not o.ok, -o.count, o.label))


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
