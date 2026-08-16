"""Live search providers and the registry the UI/CLI select from.

`connectors/` ingests on a schedule; `providers/` answers a query right now.
Both speak `NormalizedItem`, so `federation.py` can rank them side by side.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from local_index_service import IndexedDocument

from .academic import build_academic_providers
from .arena_search import build_arena_providers
from .base import ProviderError, SearchProvider
from .cultural import build_cultural_providers
from .library import LibraryProvider, build_library_providers
from .wikimedia import WIKIMEDIA_BANGS, build_wikimedia_providers

__all__ = [
    "WIKIMEDIA_BANGS",
    "LibraryProvider",
    "ProviderError",
    "SearchProvider",
    "build_all_providers",
    "build_web_providers",
    "bang_map",
    "default_selection",
    "group_providers",
    "nav_tree",
]


def build_web_providers() -> list[SearchProvider]:
    """Every live remote source, in nav order."""

    return [
        *build_wikimedia_providers(),
        *build_academic_providers(),
        *build_arena_providers(),
        *build_cultural_providers(),
    ]


def build_all_providers(
    library_indexes: Mapping[str, Sequence[IndexedDocument]] | None = None,
) -> list[SearchProvider]:
    """Library providers first (the owner's own material leads), then the web."""

    providers: list[SearchProvider] = []
    if library_indexes:
        providers.extend(build_library_providers(library_indexes))
    providers.extend(build_web_providers())
    return providers


def bang_map(providers: Sequence[SearchProvider]) -> dict[str, str]:
    """`wphd` -> `wikimedia:wphd`, for inline `!bang` query syntax."""

    mapping: dict[str, str] = {}
    for provider in providers:
        bang = getattr(provider, "bang", None)
        if bang:
            mapping[bang] = provider.provider_id
    return mapping


def group_providers(
    providers: Sequence[SearchProvider],
) -> "dict[str, list[SearchProvider]]":
    grouped: dict[str, list[SearchProvider]] = {}
    for provider in providers:
        grouped.setdefault(provider.group, []).append(provider)
    return grouped


def default_selection(providers: Sequence[SearchProvider]) -> list[str]:
    """Sources checked on first load.

    The whole library, plus the sources that answer a general query well without
    being slow. Everything else is one click away in the nav.
    """

    preferred = {
        "wikimedia:wp",
        "academic:openalex",
        "arena:channels",
        "cultural:pdr",
    }
    return [
        p.provider_id
        for p in providers
        if p.provider_id.startswith("library:") or p.provider_id in preferred
    ]


def nav_tree(providers: Sequence[SearchProvider]) -> list[dict]:
    """Serializable left-nav structure: groups -> sources."""

    tree: list[dict] = []
    for group, members in group_providers(providers).items():
        tree.append(
            {
                "group": group,
                "sources": [p.describe() for p in members],
            }
        )
    return tree
