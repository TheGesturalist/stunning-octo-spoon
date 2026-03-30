"""Persistence helpers for normalized connector items."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .enrichment import enrich_item
from .schema import ENRICHMENT_SQLITE_DDL, NORMALIZED_ITEMS_SQLITE_DDL, NormalizedItem


def init_sqlite(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(NORMALIZED_ITEMS_SQLITE_DDL)
        conn.executescript(ENRICHMENT_SQLITE_DDL)


def upsert_item(db_path: str | Path, item: NormalizedItem) -> None:
    payload = item.to_record()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO normalized_items (
                connector, source_id, source_url, title, author, summary, fulltext,
                content_type, language, created_at, updated_at, fetched_at,
                tags_json, highlights_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connector, source_id) DO UPDATE SET
                source_url=excluded.source_url,
                title=excluded.title,
                author=excluded.author,
                summary=excluded.summary,
                fulltext=excluded.fulltext,
                content_type=excluded.content_type,
                language=excluded.language,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                fetched_at=excluded.fetched_at,
                tags_json=excluded.tags_json,
                highlights_json=excluded.highlights_json,
                metadata_json=excluded.metadata_json
            """,
            (
                payload["connector"],
                payload["source_id"],
                payload["source_url"],
                payload["title"],
                payload["author"],
                payload["summary"],
                payload["fulltext"],
                payload["content_type"],
                payload["language"],
                payload["created_at"],
                payload["updated_at"],
                payload["fetched_at"],
                json.dumps(payload["tags"]),
                json.dumps(payload["highlights"]),
                json.dumps(payload["metadata"]),
            ),
        )


def upsert_item_with_enrichment(db_path: str | Path, item: NormalizedItem) -> None:
    """Persist a normalized item and derived enrichment artifacts."""

    upsert_item(db_path, item)
    enrichment = enrich_item(item)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM enrichment_facets WHERE connector = ? AND source_id = ?",
            (item.connector, item.source_id),
        )
        conn.execute(
            "DELETE FROM enrichment_graph_edges WHERE connector = ? AND source_id = ?",
            (item.connector, item.source_id),
        )

        conn.executemany(
            """
            INSERT INTO enrichment_facets (
                connector, source_id, facet_type, facet_value, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.connector,
                    item.source_id,
                    facet.facet_type,
                    facet.facet_value,
                    facet.confidence,
                )
                for facet in enrichment.facets
            ],
        )
        conn.executemany(
            """
            INSERT INTO enrichment_graph_edges (
                connector, source_id, edge_type, target_node, weight
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.connector,
                    item.source_id,
                    edge.edge_type,
                    edge.target_node,
                    edge.weight,
                )
                for edge in enrichment.edges
            ],
        )
