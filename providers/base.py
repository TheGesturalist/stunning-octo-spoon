"""Live query-time search providers.

`connectors/` pulls content *into* the library on a schedule. Providers are the
other half: they answer a query *now*, against a remote source, and hand back the
same `NormalizedItem` shape so both halves rank against each other fairly.

A provider is deliberately thin — one network call and a normalization. It owns
no storage: caching, ranking, and promotion into the library all happen above it
in `federation.py` / `connectors/storage.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from connectors.schema import NormalizedItem


class ProviderError(RuntimeError):
    """A provider failed to answer. Never fatal — federation degrades instead."""


class SearchProvider(ABC):
    """One searchable remote source (or one constrained view of one)."""

    #: Stable identifier, e.g. ``wikimedia:wphd``. Used as the cache key, the
    #: left-nav id, and the source-weight dial key.
    provider_id: str
    #: Human label for the nav.
    label: str
    #: Nav grouping, e.g. ``Wikimedia`` / ``Academic`` / ``Are.na``.
    group: str
    #: Connector name written to ``normalized_items`` if the owner saves a hit.
    library_connector: str
    #: Short help string shown in the nav.
    hint: str = ""
    #: Owner's bang vocabulary, e.g. ``wphd``. Optional.
    bang: str | None = None
    #: Default rights recorded on items from this source.
    default_rights: dict[str, Any] = {}

    @abstractmethod
    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        """Run one live query and return normalized results."""

    def describe(self) -> dict[str, Any]:
        """Serializable metadata for the web UI's left nav."""

        return {
            "provider_id": self.provider_id,
            "label": self.label,
            "group": self.group,
            "hint": self.hint,
            "bang": self.bang,
        }

    # -- helpers shared by concrete providers -------------------------------

    def _rights(self) -> dict[str, Any]:
        return dict(self.default_rights) if self.default_rights else {}


#: Rights presets reused across providers.
OPEN_RIGHTS: dict[str, Any] = {
    "allow_abstract": True,
    "allow_fulltext": True,
    "can_export": True,
    "export_policy": "full",
}

#: Metadata-only sources (abstracts under publisher terms, e.g. Crossref).
ABSTRACT_ONLY_RIGHTS: dict[str, Any] = {
    "allow_abstract": True,
    "allow_fulltext": False,
    "can_export": True,
    "export_policy": "abstract_only",
}
