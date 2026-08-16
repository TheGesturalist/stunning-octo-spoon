#!/usr/bin/env python3
"""CLI runner for the stunning-octo-spoon research discovery engine.
Usage:
    python run.py init
    python run.py ingest <source> [options]
    python run.py search <query> [options]
    python run.py digest [options]
    python run.py health [options]
    python run.py stats [options]
    python run.py export [options]
    python run.py serve [options]
"""
from __future__ import annotations
import argparse
import csv
import gzip
import http.server
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
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
    elif source == "arena":
        if not args.channel and not args.user:
            print(
                "Error: --channel <slug[,slug...]> or --user <slug> is required for arena.",
                file=sys.stderr,
            )
            sys.exit(1)
        from connectors.arena import ArenaConnector
        connector = ArenaConnector(
            channel=args.channel,
            user=args.user,
            token=args.token or config.arena_token(),
        )
    elif source == "fixture":
        if not args.path:
            print("Error: --path is required for fixture mode.", file=sys.stderr)
            sys.exit(1)
        _ingest_fixtures(args.path, db, do_enrich)
        return
    else:
        print(f"Error: unknown source '{source}'.", file=sys.stderr)
        print(
            "Supported: internet_archive, local_library, raindrop, readwise, tumblr, arena, fixture",
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
def _iter_exportable_items(db_path: str, connector: str | None, limit: int | None):
    """Yield (row_dict, rights) tuples for items whose rights permit export.

    Empty rights default to fully exportable, matching _load_documents_from_db.
    Non-empty rights must include can_export=True; export_policy="none" is dropped;
    export_policy="abstract_only" causes the caller to strip fulltext.
    """
    sql = (
        "SELECT connector, source_id, source_url, title, author, summary, "
        "fulltext, content_type, language, created_at, updated_at, "
        "tags_json, metadata_json, rights_json FROM normalized_items"
    )
    params: list[object] = []
    if connector:
        sql += " WHERE connector = ?"
        params.append(connector)
    sql += " ORDER BY created_at DESC, source_id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            rights_raw = row["rights_json"] or ""
            rights = json.loads(rights_raw) if rights_raw else {}
            if rights:
                if not rights.get("can_export", False):
                    continue
                if rights.get("export_policy") == "none":
                    continue
            else:
                rights = {
                    "allow_abstract": True,
                    "allow_fulltext": True,
                    "can_export": True,
                    "export_policy": "full",
                }
            yield row, rights
def _build_export_record(row, rights: dict) -> dict:
    policy = rights.get("export_policy", "full")
    include_fulltext = policy != "abstract_only" and rights.get("allow_fulltext", True)
    include_summary = rights.get("allow_abstract", True)
    return {
        "connector": row["connector"],
        "source_id": row["source_id"],
        "source_url": row["source_url"],
        "title": row["title"],
        "author": row["author"],
        "summary": row["summary"] if include_summary else None,
        "fulltext": row["fulltext"] if include_fulltext else None,
        "content_type": row["content_type"],
        "language": row["language"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "rights": rights,
    }
def cmd_export(args: argparse.Namespace) -> None:
    db = args.db
    records = (
        _build_export_record(row, rights)
        for row, rights in _iter_exportable_items(db, args.connector, args.limit)
    )
    use_stdout = not args.output or args.output == "-"
    if use_stdout:
        out = sys.stdout
        close_after = False
    else:
        out = open(args.output, "w", encoding="utf-8", newline="")
        close_after = True
    try:
        count = 0
        if args.format == "csv":
            fieldnames = [
                "connector", "source_id", "source_url", "title", "author",
                "summary", "fulltext", "content_type", "language",
                "created_at", "updated_at", "tags", "metadata", "rights",
            ]
            writer = csv.DictWriter(out, fieldnames=fieldnames)
            writer.writeheader()
            for rec in records:
                rec["tags"] = json.dumps(rec["tags"], ensure_ascii=False)
                rec["metadata"] = json.dumps(rec["metadata"], ensure_ascii=False)
                rec["rights"] = json.dumps(rec["rights"], ensure_ascii=False)
                writer.writerow(rec)
                count += 1
        else:
            out.write("[\n")
            first = True
            for rec in records:
                if not first:
                    out.write(",\n")
                out.write("  " + json.dumps(rec, ensure_ascii=False))
                first = False
                count += 1
            out.write("\n]\n")
    finally:
        if close_after:
            out.close()
    if not use_stdout:
        print(f"Exported {count} item(s) to {args.output}", file=sys.stderr)
def cmd_export_index(args: argparse.Namespace) -> None:
    """Write a compact static search index for a public site.

    `--connectors` is deliberately required with no default. Publishing is a
    one-way door — an index that ships by accident can be crawled before anyone
    notices — so the operator names what goes out every single time, and this
    command prints what it left behind.
    """

    requested = [c.strip() for c in args.connectors.split(",") if c.strip()]
    if not requested:
        print("Error: --connectors must name at least one connector.", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(args.db) as conn:
        available = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT connector FROM normalized_items ORDER BY connector"
            )
        ]
        unknown = [c for c in requested if c not in available]
        if unknown:
            print(f"Error: no such connector(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(available)}", file=sys.stderr)
            sys.exit(1)

        placeholders = ",".join("?" for _ in requested)
        rows = conn.execute(
            f"""SELECT connector, title, summary, source_url, created_at, tags_json
                FROM normalized_items WHERE connector IN ({placeholders})
                ORDER BY created_at DESC""",
            requested,
        ).fetchall()

    items = []
    for connector, title, summary, url, created_at, tags_json in rows:
        if not (title or summary):
            continue
        entry: dict[str, object] = {"c": connector, "t": title or ""}
        if url:
            entry["u"] = url
        if created_at:
            entry["d"] = created_at[:10]
        if not args.no_summaries and summary:
            entry["s"] = summary[: args.summary_chars]
        tags = json.loads(tags_json or "[]")
        if tags:
            entry["g"] = tags[:8]
        items.append(entry)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "connectors": requested,
        "count": len(items),
        "items": items,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    Path(args.output).write_text(blob, encoding="utf-8")

    size_mb = len(blob.encode("utf-8")) / 1_048_576
    gz_mb = len(gzip.compress(blob.encode("utf-8"))) / 1_048_576
    excluded = [c for c in available if c not in requested]
    print(f"Wrote {len(items):,} item(s) to {args.output}")
    print(f"  size: {size_mb:.2f} MB raw · {gz_mb:.2f} MB gzipped (hosts gzip automatically)")
    print(f"  included: {', '.join(requested)}")
    print(f"  EXCLUDED: {', '.join(excluded) if excluded else '(nothing — every connector was published)'}")
    if not args.no_summaries:
        print(f"  summaries included, truncated to {args.summary_chars} chars")
    print("\nThis file is meant to be served publicly. Read the two lines above before you deploy it.")


# ---------------------------------------------------------------------------
# Web UI + federated web search
# ---------------------------------------------------------------------------
def _build_providers(indexes: dict[str, list[IndexedDocument]] | None = None):
    """Import providers lazily so CLI paths that don't search stay fast."""

    from providers import build_all_providers

    return build_all_providers(indexes)


def _sliders_from_args(args: argparse.Namespace):
    from query_planner import RankingSliders

    return RankingSliders(
        relevant_surprising=args.relevant_surprising,
        focused_diverse=args.focused_diverse,
        recent_timeless=args.recent_timeless,
    )


def cmd_sources(args: argparse.Namespace) -> None:
    """List every searchable source, its group, and its bang."""

    indexes = _load_documents_from_db(args.db) if Path(args.db).exists() else {}
    providers = _build_providers(indexes)
    from providers import default_selection, group_providers

    defaults = set(default_selection(providers))
    for group, members in group_providers(providers).items():
        print(f"\n{group}")
        for provider in members:
            bang = f"!{provider.bang}" if provider.bang else ""
            mark = "*" if provider.provider_id in defaults else " "
            print(f"  {mark} {provider.provider_id:26} {bang:8} {provider.label}")
            if provider.hint:
                print(f"      {provider.hint}")
    print("\n* = searched by default (override with --sources)")


def cmd_websearch(args: argparse.Namespace) -> None:
    """Search the library and live web sources together."""

    from federation import federated_search, parse_bangs
    from providers import bang_map, default_selection
    from query_planner import SearchMode

    indexes = _load_documents_from_db(args.db) if Path(args.db).exists() else {}
    providers = _build_providers(indexes)
    by_id = {p.provider_id: p for p in providers}

    plain_query, bang_ids = parse_bangs(args.query, bang_map(providers))
    if args.sources:
        requested = [s.strip() for s in args.sources.split(",") if s.strip()]
        unknown = [s for s in requested if s not in by_id]
        if unknown:
            print(f"Unknown source(s): {', '.join(unknown)}", file=sys.stderr)
            print("Run 'python3 run.py sources' to list them.", file=sys.stderr)
            sys.exit(1)
    else:
        requested = bang_ids or default_selection(providers)

    chosen = [by_id[i] for i in requested if i in by_id]
    if not chosen:
        print("No sources selected.", file=sys.stderr)
        sys.exit(1)

    response = federated_search(
        plain_query or args.query,
        chosen,
        sliders=_sliders_from_args(args),
        limit=args.limit,
        db_path=args.db,
        library_urls={doc.source for docs in indexes.values() for doc in docs if doc.source},
        mode=SearchMode(args.mode),
        all_providers=providers,
    )

    if response.mode_plan:
        plan = response.mode_plan
        print(f"  [mode] {response.mode}", file=sys.stderr)
        for note in plan.get("notes", []):
            print(f"         {note}", file=sys.stderr)
        if plan.get("added_sources"):
            print(f"         added sources: {', '.join(plan['added_sources'])}", file=sys.stderr)

    for outcome in response.outcomes:
        state = "ok " if outcome.ok else "ERR"
        detail = (
            f"{outcome.count} result(s)"
            + (" (cached)" if outcome.cached else f" in {outcome.elapsed_ms}ms")
            if outcome.ok
            else outcome.error or "failed"
        )
        print(f"  [{state}] {outcome.label}: {detail}", file=sys.stderr)

    if not response.hits:
        print(f"\nNo results for: {response.plain_query!r}")
        return

    for i, hit in enumerate(response.hits, 1):
        print(f"\n--- {i}. [{hit.provider_label}] score {hit.score:.4f} ---")
        print(f"Title:  {hit.item.title or '(untitled)'}")
        if hit.item.author:
            print(f"Author: {hit.item.author}")
        if hit.item.source_url:
            print(f"URL:    {hit.item.source_url}")
        snippet = _mark_snippet(hit.snippet)
        if snippet:
            print(f"Snippet: {snippet}")
        if hit.in_library:
            print("  > already in your library")
        if args.explain:
            parts = " ".join(
                f"{name}={value:.3f}" for name, value in sorted(hit.component_scores.items())
            )
            print(f"  > {parts}")

    print(f"\n{len(response.hits)} result(s) for: {response.plain_query!r}")
    if args.explain:
        print("weights: " + " ".join(f"{k}={v:.3f}" for k, v in sorted(response.weights.items())))


def cmd_serve(args: argparse.Namespace) -> None:
    import web_ui

    print(f"Loading documents from {args.db}\u2026", file=sys.stderr)
    indexes = _load_documents_from_db(args.db)
    if not indexes:
        print("No items in the database. Run 'python run.py ingest ...' first.", file=sys.stderr)
        sys.exit(1)
    service = LocalIndexService(indexes)
    providers = _build_providers(indexes)
    total = sum(len(docs) for docs in indexes.values())
    handler_cls = web_ui.make_handler(
        db_path=args.db, service=service, indexes=indexes, providers=providers
    )
    server = http.server.HTTPServer((args.host, args.port), handler_cls)
    url = f"http://{args.host}:{args.port}/"
    print(
        f"Serving {total} library item(s) + {len(providers)} searchable source(s) at {url}"
        " \u2014 Ctrl-C to stop.",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.", file=sys.stderr)
        server.server_close()
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
    p_ingest.add_argument("source", help="Source name: internet_archive | local_library | raindrop | readwise | tumblr | arena | fixture")
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
    p_ingest.add_argument("--channel", help="arena: channel slug(s), comma-separated")
    p_ingest.add_argument("--user", help="arena: user slug (ingest their public channels)")
    # -- search --
    p_search = sub.add_parser("search", help="Search the local index")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_search.add_argument("--limit", type=int, default=10, metavar="N", help="Max results")
    p_search.add_argument("--indexes", metavar="a,b,...", help="Comma-separated index names to search")
    # -- websearch --
    p_web = sub.add_parser(
        "websearch",
        help="Search the library and live web sources together (federated)",
    )
    p_web.add_argument("query", help="Search query (supports !bang source selection)")
    p_web.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_web.add_argument("--limit", type=int, default=15, metavar="N", help="Max results")
    p_web.add_argument(
        "--sources",
        metavar="a,b,...",
        help="Comma-separated provider ids (see 'run.py sources'); default: a sensible subset",
    )
    p_web.add_argument(
        "--relevant-surprising", dest="relevant_surprising", type=float, default=0.5,
        metavar="0..1", help="0 = relevant, 1 = surprising (default 0.5)",
    )
    p_web.add_argument(
        "--focused-diverse", dest="focused_diverse", type=float, default=0.5,
        metavar="0..1", help="0 = focused, 1 = diverse across sources (default 0.5)",
    )
    p_web.add_argument(
        "--recent-timeless", dest="recent_timeless", type=float, default=0.5,
        metavar="0..1", help="0 = timeless, 1 = recent (default 0.5)",
    )
    p_web.add_argument(
        "--mode", default="standard",
        choices=["standard", "seed_and_mutate", "contrarian", "time_tunnel", "materiality"],
        help="Exploratory search mode (default: standard)",
    )
    p_web.add_argument(
        "--explain", action="store_true", help="Show per-component scores and weights"
    )

    # -- sources --
    p_sources = sub.add_parser("sources", help="List searchable sources and their bangs")
    p_sources.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")

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
    # -- export --
    p_export = sub.add_parser("export", help="Export items as JSON or CSV (respects rights)")
    p_export.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_export.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    p_export.add_argument("--output", "-o", metavar="PATH", help="Output file (default: stdout)")
    p_export.add_argument("--connector", metavar="NAME", help="Restrict to a single connector")
    p_export.add_argument("--limit", type=int, metavar="N", help="Max items to export")
    # -- export-index --
    p_idx = sub.add_parser(
        "export-index",
        help="Write a compact static search index for a public site (explicit connectors required)",
    )
    p_idx.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_idx.add_argument(
        "--connectors", required=True, metavar="a,b,...",
        help="REQUIRED. Connectors to publish. Nothing is included by default.",
    )
    p_idx.add_argument(
        "--output", "-o", default="library-index.json", metavar="PATH", help="Output file"
    )
    p_idx.add_argument("--no-summaries", action="store_true", help="Titles and URLs only")
    p_idx.add_argument(
        "--summary-chars", type=int, default=280, metavar="N", help="Truncate summaries (default 280)"
    )

    # -- serve --
    p_serve = sub.add_parser("serve", help="Launch the local web UI")
    p_serve.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_serve.add_argument("--host", default="127.0.0.1", metavar="HOST", help="Bind host (default: 127.0.0.1)")
    # 8090, not 8080: this machine's cloudflared tunnel maps library.bluebear.one
    # to localhost:8080, so a server bound there is published at that hostname.
    p_serve.add_argument("--port", type=int, default=8090, metavar="PORT", help="Bind port (default: 8090)")
    return parser
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "search": cmd_search,
        "websearch": cmd_websearch,
        "sources": cmd_sources,
        "digest": cmd_digest,
        "health": cmd_health,
        "stats": cmd_stats,
        "export": cmd_export,
        "export-index": cmd_export_index,
        "serve": cmd_serve,
    }
    dispatch[args.command](args)
if __name__ == "__main__":
    main()
 
