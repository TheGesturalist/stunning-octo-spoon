"""Persistence helpers for normalized connector items."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .enrichment import enrich_item
from .schema import (
    ENRICHMENT_SQLITE_DDL,
    NORMALIZED_ITEMS_SQLITE_DDL,
    WEB_CACHE_SQLITE_DDL,
    NormalizedItem,
)


@dataclass(frozen=True)
class LinkHealthRecord:
    connector: str
    source_id: str
    source_url: str
    checked_at: str
    status_code: int | None
    is_alive: bool
    archival_fallback_url: str | None
    failure_reason: str | None = None


@dataclass(frozen=True)
class WeeklyDigest:
    week_start: str
    week_end: str
    total_items: int
    top_connectors: list[tuple[str, int]]
    top_themes: list[tuple[str, int]]
    item_ids: list[tuple[str, str]]


def init_sqlite(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(NORMALIZED_ITEMS_SQLITE_DDL)
        conn.executescript(ENRICHMENT_SQLITE_DDL)
        conn.executescript(WEB_CACHE_SQLITE_DDL)


def upsert_item(db_path: str | Path, item: NormalizedItem) -> None:
    payload = item.to_record()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO normalized_items (
                connector, source_id, source_url, title, author, summary, fulltext,
                content_type, language, created_at, updated_at, fetched_at,
                tags_json, highlights_json, metadata_json, rights_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                metadata_json=excluded.metadata_json,
                rights_json=excluded.rights_json
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
                json.dumps(payload["rights"]),
            ),
        )
    record_provenance_event(
        db_path,
        connector=item.connector,
        source_id=item.source_id,
        event_type="upserted",
        details={
            "content_fingerprint": _content_fingerprint(item),
            "fetched_at": item.fetched_at,
        },
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
    record_provenance_event(
        db_path,
        connector=item.connector,
        source_id=item.source_id,
        event_type="enriched",
        details={
            "facet_count": len(enrichment.facets),
            "edge_count": len(enrichment.edges),
        },
    )


def record_provenance_event(
    db_path: str | Path,
    *,
    connector: str,
    source_id: str,
    event_type: str,
    details: dict[str, object] | None = None,
    event_at: str | None = None,
) -> None:
    timestamp = event_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO provenance_events (
                connector, source_id, event_type, event_at, details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (connector, source_id, event_type, timestamp, json.dumps(details or {})),
        )


# ---------------------------------------------------------------------------
# Web-search cache (kept OUT of normalized_items — see schema.WEB_CACHE_SQLITE_DDL)
# ---------------------------------------------------------------------------


# The federated search runs providers on threads, and every one of them touches
# the cache. `executescript` takes a write lock, so re-running the DDL per call
# turned a 1s fan-out into a 12s pile-up on the lock. Do it once per DB per
# process, and serialize the writes.
_web_cache_ready: set[str] = set()
_web_cache_lock = threading.Lock()


def ensure_web_cache(db_path: str | Path) -> None:
    """Create the web-cache/preferences tables if this DB predates them."""

    key = str(db_path)
    if key in _web_cache_ready:
        return
    with _web_cache_lock:
        if key in _web_cache_ready:
            return
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executescript(WEB_CACHE_SQLITE_DDL)
        _web_cache_ready.add(key)


def normalize_query_key(query: str) -> str:
    """Cache key for a query: case- and whitespace-insensitive."""

    return " ".join(query.lower().split())


def cache_web_results(
    db_path: str | Path,
    *,
    provider_id: str,
    query: str,
    items: list[NormalizedItem],
) -> None:
    """Store live results for (provider, query). Overwrites the previous batch."""

    ensure_web_cache(db_path)
    key = normalize_query_key(query)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Providers write concurrently; one writer at a time avoids lock timeouts.
    with _web_cache_lock, closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "DELETE FROM web_cache_items WHERE provider_id = ? AND query_key = ?",
            (provider_id, key),
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO web_cache_items (
                provider_id, source_id, query_key, cached_at, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [(provider_id, item.source_id, key, now, item.to_json()) for item in items],
        )


def read_cached_web_results(
    db_path: str | Path,
    *,
    provider_id: str,
    query: str,
    max_age_seconds: float,
) -> list[NormalizedItem] | None:
    """Return cached results for (provider, query), or None if absent/stale."""

    if max_age_seconds <= 0:
        return None
    ensure_web_cache(db_path)
    key = normalize_query_key(query)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        rows = conn.execute(
            """
            SELECT cached_at, payload_json FROM web_cache_items
            WHERE provider_id = ? AND query_key = ?
            """,
            (provider_id, key),
        ).fetchall()
    if not rows:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    for cached_at, _ in rows:
        try:
            stamp = datetime.strptime(cached_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
        if stamp < cutoff:
            return None
    return [NormalizedItem(**json.loads(payload)) for _, payload in rows]


def get_cached_web_item(
    db_path: str | Path,
    *,
    provider_id: str,
    source_id: str,
) -> NormalizedItem | None:
    """Fetch one cached web item by identity, newest cache entry wins."""

    ensure_web_cache(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        row = conn.execute(
            """
            SELECT payload_json FROM web_cache_items
            WHERE provider_id = ? AND source_id = ?
            ORDER BY cached_at DESC LIMIT 1
            """,
            (provider_id, source_id),
        ).fetchone()
    return NormalizedItem(**json.loads(row[0])) if row else None


def save_web_item_to_library(
    db_path: str | Path,
    *,
    provider_id: str,
    source_id: str,
    enrich: bool = True,
) -> NormalizedItem | None:
    """Promote one cached web result into the curated corpus.

    This is the *only* path from the web cache into ``normalized_items``, so
    growth of the library stays an explicit, per-item decision.
    """

    item = get_cached_web_item(db_path, provider_id=provider_id, source_id=source_id)
    if item is None:
        return None
    if enrich:
        upsert_item_with_enrichment(db_path, item)
    else:
        upsert_item(db_path, item)
    record_provenance_event(
        db_path,
        connector=item.connector,
        source_id=item.source_id,
        event_type="saved_from_web",
        details={"provider_id": provider_id},
    )
    return item


def save_search_preferences(
    db_path: str | Path, *, pref_key: str, payload: dict[str, object]
) -> None:
    """Persist a small JSON blob of UI search settings (sliders, dials, sources)."""

    ensure_web_cache(db_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO search_preferences (pref_key, pref_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(pref_key) DO UPDATE SET
                pref_json=excluded.pref_json,
                updated_at=excluded.updated_at
            """,
            (pref_key, json.dumps(payload, ensure_ascii=False), now),
        )


def load_search_preferences(
    db_path: str | Path, *, pref_key: str
) -> dict[str, object] | None:
    ensure_web_cache(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT pref_json FROM search_preferences WHERE pref_key = ?", (pref_key,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def monitor_link_health(
    db_path: str | Path,
    *,
    timeout_seconds: float = 4.0,
) -> list[LinkHealthRecord]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT connector, source_id, source_url
            FROM normalized_items
            WHERE source_url IS NOT NULL AND source_url != ''
            """
        ).fetchall()

        records: list[LinkHealthRecord] = []
        for connector, source_id, source_url in rows:
            checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            status_code, reason = _check_url(source_url, timeout_seconds=timeout_seconds)
            alive = status_code is not None and 200 <= status_code < 400
            fallback = None if alive else _archival_fallback_pointer(source_url)

            record = LinkHealthRecord(
                connector=connector,
                source_id=source_id,
                source_url=source_url,
                checked_at=checked_at,
                status_code=status_code,
                is_alive=alive,
                archival_fallback_url=fallback,
                failure_reason=reason,
            )
            records.append(record)

        conn.executemany(
            """
            INSERT INTO link_health_checks (
                connector, source_id, source_url, checked_at, status_code,
                is_alive, archival_fallback_url, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.connector,
                    r.source_id,
                    r.source_url,
                    r.checked_at,
                    r.status_code,
                    int(r.is_alive),
                    r.archival_fallback_url,
                    r.failure_reason,
                )
                for r in records
            ],
        )

    for record in records:
        record_provenance_event(
            db_path,
            connector=record.connector,
            source_id=record.source_id,
            event_type="link_health_checked",
            details={
                "is_alive": record.is_alive,
                "status_code": record.status_code,
                "archival_fallback_url": record.archival_fallback_url,
            },
            event_at=record.checked_at,
        )
    return records


def generate_weekly_digest(
    db_path: str | Path,
    *,
    now: datetime | None = None,
) -> WeeklyDigest:
    current = now or datetime.now(timezone.utc)
    week_start_dt = current - timedelta(days=7)
    week_start = week_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    week_end = current.strftime("%Y-%m-%dT%H:%M:%SZ")

    with sqlite3.connect(db_path) as conn:
        item_rows = conn.execute(
            """
            SELECT connector, source_id
            FROM normalized_items
            WHERE fetched_at >= ?
              AND json_extract(metadata_json, '$.processed_for_digest') IS NOT 1
            ORDER BY fetched_at DESC
            """,
            (week_start,),
        ).fetchall()
        connector_rows = conn.execute(
            """
            SELECT connector, COUNT(*)
            FROM normalized_items
            WHERE fetched_at >= ?
              AND json_extract(metadata_json, '$.processed_for_digest') IS NOT 1
            GROUP BY connector
            ORDER BY COUNT(*) DESC
            LIMIT 5
            """,
            (week_start,),
        ).fetchall()
        theme_rows = conn.execute(
            """
            SELECT ef.facet_value, COUNT(*)
            FROM enrichment_facets ef
            JOIN normalized_items ni
              ON ni.connector = ef.connector
             AND ni.source_id = ef.source_id
            WHERE ef.facet_type = 'theme'
              AND ni.fetched_at >= ?
              AND json_extract(ni.metadata_json, '$.processed_for_digest') IS NOT 1
            GROUP BY ef.facet_value
            ORDER BY COUNT(*) DESC
            LIMIT 5
            """,
            (week_start,),
        ).fetchall()

    return WeeklyDigest(
        week_start=week_start,
        week_end=week_end,
        total_items=len(item_rows),
        top_connectors=[(row[0], row[1]) for row in connector_rows],
        top_themes=[(row[0], row[1]) for row in theme_rows],
        item_ids=[(row[0], row[1]) for row in item_rows],
    )


def mark_digest_items_processed(
    db_path: str | Path,
    digest: WeeklyDigest,
) -> None:
    processed_items: list[tuple[str, str]] = []
    with sqlite3.connect(db_path) as conn:
        for connector, source_id in digest.item_ids:
            metadata_json_row = conn.execute(
                """
                SELECT metadata_json
                FROM normalized_items
                WHERE connector = ? AND source_id = ?
                """,
                (connector, source_id),
            ).fetchone()
            if metadata_json_row is None:
                continue
            metadata = json.loads(metadata_json_row[0] or "{}")
            metadata["processed_for_digest"] = True
            metadata["processed_for_digest_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            conn.execute(
                """
                UPDATE normalized_items
                SET metadata_json = ?
                WHERE connector = ? AND source_id = ?
                """,
                (json.dumps(metadata), connector, source_id),
            )
            processed_items.append((connector, source_id))

    for connector, source_id in processed_items:
        record_provenance_event(
            db_path,
            connector=connector,
            source_id=source_id,
            event_type="digest_processed",
            details={"week_start": digest.week_start, "week_end": digest.week_end},
        )


def _check_url(url: str, *, timeout_seconds: float) -> tuple[int | None, str | None]:
    request = Request(url, method="HEAD", headers={"User-Agent": "stunning-octo-spoon/1.0"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status), None
    except URLError as exc:
        return None, str(exc.reason)


def _archival_fallback_pointer(url: str) -> str:
    return f"https://web.archive.org/web/*/{url}"


def _content_fingerprint(item: NormalizedItem) -> str:
    joined = "||".join(
        [
            item.title or "",
            item.summary or "",
            item.fulltext or "",
            item.updated_at or "",
        ]
    )
    return sha256(joined.encode("utf-8")).hexdigest()
