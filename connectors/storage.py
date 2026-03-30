"""Persistence helpers for normalized connector items."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schema import NORMALIZED_ITEMS_SQLITE_DDL, NormalizedItem


def init_sqlite(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(NORMALIZED_ITEMS_SQLITE_DDL)


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
