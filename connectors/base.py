"""Connector interface used by all source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .schema import NormalizedItem


class BaseConnector(ABC):
    name: str

    @abstractmethod
    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch source-native items in pages."""

    @abstractmethod
    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        """Fetch or compute full text for a specific item."""

    @abstractmethod
    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        """Convert a source-native item into the shared normalized schema."""

    @abstractmethod
    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        """Return cursor used to resume future syncs."""
