#!/usr/bin/env python3
"""CLI runner for the stunning-octo-spoon research discovery engine.
Usage:
    python run.py init
    python run.py ingest <source> [options]
    python run.py search <query> [options]
    python run.py digest [options]
    python run.py health [options]
    python run.py stats [options]
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
import config
from connectors.storage import (
    generate_weekly_digest,
    init_sqlite,
    mark_digest_items_processed,
    monitor_link_health,
    upsert_item,
    upsert_item_with_enrichment,
)
from local_index_service import IndexedDocument, LocalIndexService
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mark_snippet(html: str) -> str:
    """Convert <mark>...</mark> to **...** for terminal display."""
    return re.sub(r"<mark>(.*?)</mark>", r"**\1**", html)
def _load_documents_from_db(db_path: str) -> dict[str, list[IndexedDocument]]:
    """Load all normalized_items from SQLite and group by connector."""
    indexes: dict[str, list[IndexedDocument]] = {}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT connector, source_id, title, summary, fulltext,
                   source_url, created_at, metadata_json, rights_json
            FROM normalized_items
            """
        ).fetchall()
    for connector, source_id, title, summary, fulltext, source_url, created_at, metadata_json, rights_json in rows:
        rights = json.loads(rights_json or "{}")
        # Default rights: allow abstract and fulltext unless restricted
        if not rights:
            rights = {
                "allow_abstract": True,
                "allow_fulltext": True,
                "can_export": True,
                "export_policy": "full",
            }
        doc = IndexedDocument(
            doc_id=f"{connector}:{source_id}",
            title=title or "(untitled)",
            text=fulltext or "",
            source=source_url or connector,
            created_at=created_at,
            abstract=summary,
            rights=rights,
        )
        indexes.setdefault(connector, []).append(doc)
    return indexes
# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> None:
    db = args.db
    init_sqlite(db)
    print(f"Database initialized at: {Path(db).resolve()}")
def cmd_ingest(args: argparse.Namespace) -> None:
    source = args.source
    db = args.db
    limit = args.limit
    do_enrich = not args.no_enrich
    # Build the connector
    if source == "internet_archive":
        if not args.query:
            print("Error: --query is required for internet_archive.", file=sys.stderr)
            sys.exit(1)
        from connectors.internet_archive import InternetArchiveConnector
        connector = InternetArchiveConnector(query=args.query)
    elif source == "local_library":
        if not args.path:
            print("Error: --path is required for local_library.", file=sys.stderr)
            sys.exit(1)
        from connectors.local_library import LocalLibraryConnector
        connector = LocalLibraryConnector(
            library_path=args.path,
            index_path=args.index_path or None,
        )
    elif source == "raindrop":
        token = args.token or config.raindrop_token()
        if not token:
            print(
                "Error: --token (or SPOON_RAINDROP_TOKEN env var) is required for raindrop.",
                file=sys.stderr,
            )
            sys.exit(1)
        from connectors.raindrop_io import RaindropIOConnector
        collection = int(args.collection) if args.collection else 0
        connector = RaindropIOConnector(api_token=token, collection_id=collection)
    elif source == "readwise":
        token = args.token or config.readwise_token()
        if not token:
            print(
                "Error: --token (or SPOON_READWISE_TOKEN env var) is required for readwise.",
                file=sys.stderr,
            )
            sys.exit(1)
        from connectors.reader_io import ReaderIOConnector
        connector = ReaderIOConnector(api_token=token)
    elif source == "tumblr":
        blog = args.blog or config.tumblr_blog()
        api_key = args.api_key or config.tumblr_api_key()
        if not blog:
            print(
                "Error: --blog (or SPOON_TUMBLR_BLOG env var) is required for tumblr.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not api_key:
            print(
                "Error: --api-key (or SPOON_TUMBLR_API_KEY env var) is required for tumblr.",
                file=sys.stderr,
            )
            sys.exit(1)
        from connectors.tumblr import TumblrConnector
        connector = TumblrConnector(blog_hostname=blog, api_key=api_key)
    elif source == "fixture":
        if not args.path:
            print("Error: --path is required for fixture mode.", file=sys.stderr)
            sys.exit(1)
        _ingest_fixtures(args.path, db, do_enrich)
        return
    else:
        print(f"Error: unknown source '{source}'.", file=sys.stderr)
        print(
            "Supported: internet_archive, local_library, raindrop, readwise, tumblr, fixture",
            file=sys.stderr,
        )
        sys.exit(1)
    # Fetch and persist
    try:
        raw_items = connector.fetch_items(limit=limit)
    except Exception as exc:
        print(f"Error fetching items from {source}: {exc}", file=sys.stderr)
        sys.exit(1)
    count = 0
    for raw in raw_items:
        try:
            item = connector.normalize_item(raw)
        except Exception as exc:
            print(f"  [skip] normalize failed: {exc}", file=sys.stderr)
            continue
        try:
            if do_enrich:
                upsert_item_with_enrichment(db, item)
            else:
                upsert_item(db, item)
            title = item.title or "(untitled)"
            print(f"  [{connector.name}] {title} ({item.source_id})")
            count += 1
        except Exception as exc:
            title = getattr(item, "title", None) or "(untitled)"
            print(f"  [skip] persist failed for '{title}': {exc}", file=sys.stderr)
            continue
    enrich_note = "with enrichment" if do_enrich else "without enrichment"
    print(f"\nIngested {count} item(s) from {source} ({enrich_note}).")
def _ingest_fixtures(fixture_path: str, db: str, do_enrich: bool) -> None:
    """Ingest pre-built NormalizedItem JSON fixtures from a directory."""
    from connectors.schema import NormalizedItem
    fixture_dir = Path(fixture_path)
    fixture_files = sorted(fixture_dir.glob("*.json"))
    if not fixture_files:
        print(f"No .json fixture files found in {fixture_dir}", file=sys.stderr)
        sys.exit(1)
    count = 0
    for fpath in fixture_files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            # data may be a list or a single item
            if isinstance(data, dict):
                data = [data]
            for record in data:
                item = NormalizedItem(
                    connector=record["connector"],
                    source_id=record["source_id"],
                    source_url=record.get("source_url"),
                    title=record.get("title"),
                    author=record.get("author"),
                    summary=record.get("summary"),
                    fulltext=record.get("fulltext"),
                    content_type=record.get("content_type", "document"),
                    language=record.get("language"),
                    created_at=record.get("created_at"),
                    updated_at=record.get("updated_at"),
                    tags=record.get("tags", []),
                    highlights=record.get("highlights", []),
                    metadata=record.get("metadata", {}),
                    rights=record.get("rights", {}),
                )
                if do_enrich:
                    upsert_item_with_enrichment(db, item)
                else:
                    upsert_item(db, item)
                print(f"  [fixture] {item.title or '(untitled)'} ({item.source_id})")
                count += 1
        except Exception as exc:
            print(f"  [skip] {fpath.name}: {exc}", file=sys.stderr)
    enrich_note = "with enrichment" if do_enrich else "without enrichment"
    print(f"\nIngested {count} fixture item(s) ({enrich_note}).")
def cmd_search(args: argparse.Namespace) -> None:
    db = args.db
    query = args.query
    limit = args.limit
    index_filter = [s.strip() for s in args.indexes.split(",")] if args.indexes else None
    indexes = _load_documents_from_db(db)
    if not indexes:
        print("No items in the database. Run 'python run.py ingest ...' first.")
        return
    service = LocalIndexService(indexes)
    results = service.query(query, indexes=index_filter, limit=limit)
    if not results:
        print(f"No results for: {query!r}")
        return
    for i, card in enumerate(results, 1):
        print(f"\n--- Result {i} ---")
        print(f"Title:  {card.title}")
        print(f"Source: {card.source}")
        snippet = _mark_snippet(card.snippet_highlight)
        if snippet:
            print(f"Snippet: {snippet}")
        if card.match_explanations:
            for exp in card.match_explanations:
                print(f"  > {exp}")
        if card.semantic_neighbors:
            neighbor_titles = ", ".join(n.title for n in card.semantic_neighbors)
            print(f"  Similar: {neighbor_titles}")
    print(f"\n{len(results)} result(s) for: {query!r}")
def cmd_digest(args: argparse.Namespace) -> None:
    db = args.db
    digest = generate_weekly_digest(db)
    print(f"Weekly Digest")
    print(f"  Period:      {digest.week_start} — {digest.week_end}")
    print(f"  Total items: {digest.total_items}")
    if digest.top_connectors:
        print("  Top sources:")
        for connector, count in digest.top_connectors:
            print(f"    {connector}: {count}")
    if digest.top_themes:
        print("  Top themes:")
        for theme, count in digest.top_themes:
            print(f"    {theme}: {count}")
    if not digest.total_items:
        print("  (No new items this week.)")
    if args.mark_processed and digest.item_ids:
        mark_digest_items_processed(db, digest)
        print(f"\nMarked {len(digest.item_ids)} item(s) as processed.")
def cmd_health(args: argparse.Namespace) -> None:
    db = args.db
    timeout = args.timeout
    print(f"Checking link health (timeout={timeout}s)…")
    records = monitor_link_health(db, timeout_seconds=timeout)
    alive = [r for r in records if r.is_alive]
    dead = [r for r in records if not r.is_alive]
    print(f"\nTotal checked: {len(records)}")
    print(f"  Alive: {len(alive)}")
    print(f"  Dead:  {len(dead)}")
    if dead:
        print("\nDead links:")
        for r in dead:
            print(f"  {r.connector} — {r.title if hasattr(r, 'title') else r.source_id}")
            print(f"    URL:      {r.source_url}")
            if r.archival_fallback_url:
                print(f"    Archive:  {r.archival_fallback_url}")
            if r.failure_reason:
                print(f"    Reason:   {r.failure_reason}")
def cmd_stats(args: argparse.Namespace) -> None:
    db = args.db
    with sqlite3.connect(db) as conn:
        total_items = conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]
        per_connector = conn.execute(
            "SELECT connector, COUNT(*) FROM normalized_items GROUP BY connector ORDER BY COUNT(*) DESC"
        ).fetchall()
        total_facets = conn.execute("SELECT COUNT(*) FROM enrichment_facets").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM enrichment_graph_edges").fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM provenance_events").fetchone()[0]
    print(f"Database: {Path(db).resolve()}")
    print(f"\nItems:           {total_items}")
    if per_connector:
        for connector, count in per_connector:
            print(f"  {connector}: {count}")
    print(f"\nEnrichment facets: {total_facets}")
    print(f"Graph edges:       {total_edges}")
    print(f"Provenance events: {total_events}")
# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="stunning-octo-spoon research discovery engine CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # -- init --
    p_init = sub.add_parser("init", help="Initialize the SQLite database")
    p_init.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    # -- ingest --
    p_ingest = sub.add_parser("ingest", help="Ingest items from a source")
    p_ingest.add_argument("source", help="Source name: internet_archive | local_library | raindrop | readwise | tumblr | fixture")
    p_ingest.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_ingest.add_argument("--limit", type=int, default=20, metavar="N", help="Max items to fetch")
    enrich_group = p_ingest.add_mutually_exclusive_group()
    enrich_group.add_argument("--enrich", dest="no_enrich", action="store_false", default=False, help="Run enrichment (default)")
    enrich_group.add_argument("--no-enrich", dest="no_enrich", action="store_true", help="Skip enrichment")
    # source-specific
    p_ingest.add_argument("--query", help="internet_archive: IA advanced search query")
    p_ingest.add_argument("--path", help="local_library/fixture: filesystem path")
    p_ingest.add_argument("--index-path", dest="index_path", help="local_library: sidecar text index directory")
    p_ingest.add_argument("--token", help="raindrop/readwise: API token")
    p_ingest.add_argument("--collection", help="raindrop: collection ID (default 0)")
    p_ingest.add_argument("--blog", help="tumblr: blog hostname")
    p_ingest.add_argument("--api-key", dest="api_key", help="tumblr: API key")
    # -- search --
    p_search = sub.add_parser("search", help="Search the local index")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_search.add_argument("--limit", type=int, default=10, metavar="N", help="Max results")
    p_search.add_argument("--indexes", metavar="a,b,...", help="Comma-separated index names to search")
    # -- digest --
    p_digest = sub.add_parser("digest", help="Show the weekly digest")
    p_digest.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_digest.add_argument("--mark-processed", dest="mark_processed", action="store_true", help="Mark items as processed")
    # -- health --
    p_health = sub.add_parser("health", help="Check link health")
    p_health.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_health.add_argument("--timeout", type=float, default=4.0, metavar="SECS", help="Request timeout in seconds")
    # -- stats --
    p_stats = sub.add_parser("stats", help="Show database statistics")
    p_stats.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    return parser
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "search": cmd_search,
        "digest": cmd_digest,
        "health": cmd_health,
        "stats": cmd_stats,
    }
    dispatch[args.command](args)
if __name__ == "__main__":
    main()
 
