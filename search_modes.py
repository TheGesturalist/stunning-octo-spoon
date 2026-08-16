"""Exploratory search modes — the presets, made to actually do something.

`query_planner.SEARCH_MODE_PRESETS` has described these four modes since the
planner landed, and `plan_query` routes them to connector-*group* names
(`pinterest`, `cosmos`, `local_notes`) that have no provider behind them. This
module translates each mode into operations the federated search can perform
against the providers that exist: which sources to fold in, how to move the
sliders, whether to run a second query pass, and how to re-rank the union.

Each mode stays faithful to the description the planner already publishes:

    seed_and_mutate  start from one artifact and branch to adjacent paths
    contrarian       surface opposing aesthetics and dissenting arguments
    time_tunnel      map the same concept across decades
    materiality      prioritize scans, marginalia, ephemera, archival traces
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from query_planner import RankingSliders, SearchMode

# Sources that carry material artifacts rather than prose — the ones
# `materiality` wants at the front.
_MATERIAL_SOURCES = (
    "cultural:pdr",
    "arena:blocks",
    "arena:channels",
    "wikimedia:wpi",
    "library:arena",
)

# Sources that widen the argument rather than deepen it.
_CONTRARIAN_SOURCES = ("academic:openalex", "academic:crossref", "arena:channels")

# Sources with the longest historical reach.
_TIME_TUNNEL_SOURCES = ("cultural:pdr", "academic:crossref", "wikimedia:wp")

# Sources you can chain outward from — your own material plus adjacent blocks.
_SEED_SOURCES = ("arena:blocks", "arena:channels")

# content_type fragments that indicate a material object.
_MATERIAL_TYPES = ("image", "file", "collection", "attachment", "media", "scan")

# Appended to a second pass in contrarian mode. Deliberately plain words: the
# providers are keyword engines, not language models.
_CONTRARIAN_TERMS = "critique criticism debate controversy objection"

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has
    have how in into is it its of on or that the
    their there these this to was were what when where which who
    will with would about after also been both could each first from
    more most only other over should some such than then they them
    very well your""".split()
)


@dataclass(frozen=True)
class ModePlan:
    """What a mode changes about one federated search."""

    mode: SearchMode
    sliders: RankingSliders
    add_provider_ids: tuple[str, ...] = ()
    source_weights: Mapping[str, float] = field(default_factory=dict)
    #: Extra literal query passes to run alongside the original.
    extra_queries: tuple[str, ...] = ()
    #: Run a second pass built from terms mined out of the first pass's results.
    mutate: bool = False
    #: Name of a re-ranking transform to apply after scoring.
    rerank: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "added_sources": list(self.add_provider_ids),
            "extra_queries": list(self.extra_queries),
            "mutate": self.mutate,
            "rerank": self.rerank,
            "notes": list(self.notes),
            "sliders": {
                "relevant_surprising": self.sliders.relevant_surprising,
                "focused_diverse": self.sliders.focused_diverse,
                "recent_timeless": self.sliders.recent_timeless,
            },
        }


def plan_search_mode(
    mode: SearchMode,
    *,
    query: str,
    sliders: RankingSliders,
    selected_ids: Sequence[str],
    available_ids: Sequence[str],
) -> ModePlan:
    """Translate a mode into concrete changes for this search.

    Only sources that actually exist are added, so a mode never silently
    promises coverage the build doesn't have.
    """

    available = set(available_ids)
    already = set(selected_ids)

    def additions(candidates: Sequence[str]) -> tuple[str, ...]:
        return tuple(c for c in candidates if c in available and c not in already)

    if mode == SearchMode.SEED_AND_MUTATE:
        add = additions(_SEED_SOURCES)
        return ModePlan(
            mode=mode,
            # Branching outward is a novelty operation, not a precision one.
            sliders=RankingSliders(
                relevant_surprising=max(sliders.relevant_surprising, 0.75),
                focused_diverse=sliders.focused_diverse,
                recent_timeless=sliders.recent_timeless,
            ),
            add_provider_ids=add,
            mutate=True,
            notes=(
                "Second pass runs on terms mined from the first pass's results.",
                "Novelty weighted up; already-held items are damped further.",
            ),
        )

    if mode == SearchMode.CONTRARIAN:
        add = additions(_CONTRARIAN_SOURCES)
        return ModePlan(
            mode=mode,
            # Maximum spread: the point is to not hear one source's version.
            sliders=RankingSliders(
                relevant_surprising=max(sliders.relevant_surprising, 0.6),
                focused_diverse=1.0,
                recent_timeless=sliders.recent_timeless,
            ),
            add_provider_ids=add,
            extra_queries=(f"{query} {_CONTRARIAN_TERMS}".strip(),),
            notes=(
                "Extra pass adds critique/debate terms.",
                "Diversity forced to maximum so no single source dominates.",
            ),
        )

    if mode == SearchMode.TIME_TUNNEL:
        add = additions(_TIME_TUNNEL_SOURCES)
        return ModePlan(
            mode=mode,
            # Recency is the enemy here.
            sliders=RankingSliders(
                relevant_surprising=sliders.relevant_surprising,
                focused_diverse=sliders.focused_diverse,
                recent_timeless=0.0,
            ),
            add_provider_ids=add,
            rerank="decade_spread",
            notes=(
                "Results are spread across decades rather than ranked by score alone.",
                "Recency weighting turned off entirely.",
            ),
        )

    if mode == SearchMode.MATERIALITY:
        add = additions(_MATERIAL_SOURCES)
        weights = {pid: 0.6 for pid in (*_MATERIAL_SOURCES,) if pid in available}
        return ModePlan(
            mode=mode,
            sliders=sliders,
            add_provider_ids=add,
            source_weights=weights,
            rerank="material_first",
            notes=(
                "Archive and image sources weighted up.",
                "Items whose content type is a scan/image/collection are promoted.",
            ),
        )

    return ModePlan(mode=SearchMode.STANDARD, sliders=sliders)


# ---------------------------------------------------------------------------
# Re-ranking transforms
# ---------------------------------------------------------------------------


def decade_spread(hits: Sequence[Any], limit: int) -> list[Any]:
    """Round-robin across decades so one era can't fill the page.

    Hits without a usable date go last: an undated item can't demonstrate a
    concept's movement through time, which is the whole point of this mode.
    """

    buckets: dict[str, list[Any]] = {}
    undated: list[Any] = []
    for hit in hits:
        decade = _decade_of(getattr(hit.item, "created_at", None))
        if decade is None:
            undated.append(hit)
        else:
            buckets.setdefault(decade, []).append(hit)

    order = sorted(buckets.keys())
    out: list[Any] = []
    while order and len(out) < limit:
        for decade in list(order):
            if not buckets[decade]:
                order.remove(decade)
                continue
            out.append(buckets[decade].pop(0))
            if len(out) >= limit:
                break
    out.extend(undated[: max(0, limit - len(out))])
    return out


def material_first(hits: Sequence[Any], limit: int) -> list[Any]:
    """Stable-promote items that are material objects rather than prose."""

    material, rest = [], []
    for hit in hits:
        content_type = (getattr(hit.item, "content_type", "") or "").lower()
        image = (getattr(hit.item, "metadata", {}) or {}).get("image_url")
        if image or any(frag in content_type for frag in _MATERIAL_TYPES):
            material.append(hit)
        else:
            rest.append(hit)
    return (material + rest)[:limit]


RERANKERS = {"decade_spread": decade_spread, "material_first": material_first}


def _decade_of(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) < 4 or not text[:4].isdigit():
        return None
    year = int(text[:4])
    if not (1000 <= year <= 2999):
        return None
    return f"{(year // 10) * 10}s"


# ---------------------------------------------------------------------------
# Seed-and-mutate term mining
# ---------------------------------------------------------------------------


def mine_terms(hits: Sequence[Any], *, exclude: Sequence[str], count: int = 4) -> list[str]:
    """Pick the terms that best characterize a first pass, for the second.

    Frequency across *distinct results* rather than raw frequency, so one long
    document can't dictate where the search mutates to next.
    """

    excluded = {t.lower() for t in exclude} | _STOPWORDS
    doc_freq: dict[str, int] = {}
    for hit in hits:
        item = hit.item
        text = " ".join(p for p in (item.title, item.summary) if p)
        seen = set()
        for token in text.lower().replace("/", " ").split():
            token = "".join(ch for ch in token if ch.isalnum() or ch == "'")
            if len(token) < 4 or token in excluded or token in seen:
                continue
            seen.add(token)
        for token in seen:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    # A term shared by several results is a theme; one seen once is noise.
    ranked = sorted(
        ((n, t) for t, n in doc_freq.items() if n >= 2),
        key=lambda row: (-row[0], row[1]),
    )
    return [t for _, t in ranked[:count]]
