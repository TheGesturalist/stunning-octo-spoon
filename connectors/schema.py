"""Shared normalized schema for all connectors.

The ranking/query layer should consume this schema instead of source-specific payloads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


ISO8601 = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(slots=True)
class NormalizedItem:
    """Canonical content record used by all connectors."""

    connector: str
    source_id: str
    source_url: str | None = None

    title: str | None = None
    author: str | None = None
    summary: str | None = None
    fulltext: str | None = None

    content_type: str = "document"
    language: str | None = None

    created_at: str | None = None
    updated_at: str | None = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(ISO8601)
    )

    tags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    rights: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


NORMALIZED_ITEM_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "NormalizedItem",
    "type": "object",
    "required": ["connector", "source_id", "content_type", "fetched_at"],
    "properties": {
        "connector": {"type": "string"},
        "source_id": {"type": "string"},
        "source_url": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "author": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "fulltext": {"type": ["string", "null"]},
        "content_type": {"type": "string"},
        "language": {"type": ["string", "null"]},
        "created_at": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"]},
        "fetched_at": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
        "rights": {"type": "object"},
    },
    "additionalProperties": False,
}


NORMALIZED_ITEMS_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS normalized_items (
    connector TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT,
    title TEXT,
    author TEXT,
    summary TEXT,
    fulltext TEXT,
    content_type TEXT NOT NULL,
    language TEXT,
    created_at TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    highlights_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    rights_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (connector, source_id)
);

CREATE INDEX IF NOT EXISTS idx_normalized_items_title
    ON normalized_items(title);

CREATE INDEX IF NOT EXISTS idx_normalized_items_updated_at
    ON normalized_items(updated_at);
""".strip()


ENRICHMENT_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS enrichment_facets (
    connector TEXT NOT NULL,
    source_id TEXT NOT NULL,
    facet_type TEXT NOT NULL,
    facet_value TEXT NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (connector, source_id, facet_type, facet_value)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_facets_type_value
    ON enrichment_facets(facet_type, facet_value);

CREATE INDEX IF NOT EXISTS idx_enrichment_facets_item
    ON enrichment_facets(connector, source_id);

CREATE TABLE IF NOT EXISTS enrichment_graph_edges (
    connector TEXT NOT NULL,
    source_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    target_node TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (connector, source_id, edge_type, target_node)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_edges_type_target
    ON enrichment_graph_edges(edge_type, target_node);

CREATE INDEX IF NOT EXISTS idx_enrichment_edges_item
    ON enrichment_graph_edges(connector, source_id);

CREATE TABLE IF NOT EXISTS link_health_checks (
    connector TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status_code INTEGER,
    is_alive INTEGER NOT NULL,
    archival_fallback_url TEXT,
    failure_reason TEXT,
    PRIMARY KEY (connector, source_id, checked_at)
);

CREATE INDEX IF NOT EXISTS idx_link_health_item
    ON link_health_checks(connector, source_id);

CREATE TABLE IF NOT EXISTS provenance_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_provenance_item
    ON provenance_events(connector, source_id, event_at);
""".strip()
