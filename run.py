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
import http.server
import json
import re
import sqlite3
import sys
import urllib.parse
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
# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>stunning-octo-spoon</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fafaf7; --fg: #1a1a1a; --muted: #666; --accent: #2a5d8f;
  --card: #fff; --border: #e4e4e0; --badge: #efeee9; --mark: #ffe680;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --fg: #e8e8e6; --muted: #999; --accent: #7ab1e6;
    --card: #1d1e22; --border: #2c2d31; --badge: #2c2d31; --mark: #6a5b1f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
       font: 15px/1.5 -apple-system, system-ui, sans-serif; }
header { padding: 20px 24px; border-bottom: 1px solid var(--border); }
h1 { margin: 0; font-size: 18px; font-weight: 600; }
.meta { color: var(--muted); font-size: 13px; margin-top: 4px; }
main { max-width: 920px; margin: 0 auto; padding: 24px; }
.controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
#q { flex: 1; min-width: 240px; padding: 10px 12px; font: inherit;
     background: var(--card); color: var(--fg);
     border: 1px solid var(--border); border-radius: 6px; }
#q:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
select, button { padding: 10px 12px; font: inherit;
                 background: var(--card); color: var(--fg);
                 border: 1px solid var(--border); border-radius: 6px; }
button { cursor: pointer; background: var(--accent); color: #fff; border-color: var(--accent); }
button:hover { opacity: 0.9; }
.status { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px 18px; margin-bottom: 12px; }
.card h2 { margin: 0 0 6px; font-size: 16px; font-weight: 600; }
.card h2 a { color: var(--fg); text-decoration: none; }
.card h2 a:hover { color: var(--accent); }
.badge { display: inline-block; padding: 2px 8px; font-size: 11px;
         background: var(--badge); color: var(--muted); border-radius: 4px;
         margin-left: 8px; vertical-align: middle; }
.snippet { margin: 8px 0; color: var(--fg); }
.snippet mark { background: var(--mark); padding: 0 2px; border-radius: 2px; }
.explanations, .neighbors { font-size: 13px; color: var(--muted); margin-top: 6px; }
.explanations div::before { content: "→ "; }
.neighbors strong { font-weight: 500; color: var(--fg); }
.source { font-size: 12px; color: var(--muted); }
.source a { color: var(--muted); }
.empty { color: var(--muted); text-align: center; padding: 40px 20px; }
</style>
</head>
<body>
<header>
  <h1>stunning-octo-spoon</h1>
  <div class="meta" id="meta">loading…</div>
</header>
<main>
  <form class="controls" id="form">
    <input id="q" name="q" type="text" placeholder="Search your library…" autofocus>
    <select id="connector" name="connector"><option value="">all sources</option></select>
    <select id="limit" name="limit">
      <option value="10">10 results</option>
      <option value="20" selected>20 results</option>
      <option value="50">50 results</option>
    </select>
    <button type="submit">Search</button>
  </form>
  <div class="status" id="status"></div>
  <div id="results"><div class="empty">Type a query above to search your library.</div></div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function init() {
  const r = await fetch("/api/connectors").then((r) => r.json());
  $("#meta").textContent = `${r.total_items.toLocaleString()} items · ${r.connectors.map((c) => `${c.name} (${c.count.toLocaleString()})`).join(" · ")}`;
  for (const c of r.connectors) {
    const o = document.createElement("option");
    o.value = c.name; o.textContent = `${c.name} (${c.count.toLocaleString()})`;
    $("#connector").appendChild(o);
  }
}
function renderCard(card) {
  const connector = (card.doc_id || "").split(":")[0] || "?";
  const neighbors = (card.semantic_neighbors || []).map((n) => esc(n.title)).join(", ");
  const explanations = (card.match_explanations || []).map((e) => `<div>${esc(e)}</div>`).join("");
  return `
    <div class="card">
      <h2><a href="${esc(card.source)}" target="_blank" rel="noopener">${esc(card.title)}</a><span class="badge">${esc(connector)}</span></h2>
      <div class="source"><a href="${esc(card.source)}" target="_blank" rel="noopener">${esc(card.source)}</a></div>
      <div class="snippet">${card.snippet_highlight || ""}</div>
      ${explanations ? `<div class="explanations">${explanations}</div>` : ""}
      ${neighbors ? `<div class="neighbors"><strong>Similar:</strong> ${neighbors}</div>` : ""}
    </div>`;
}
async function search(e) {
  if (e) e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#status").textContent = "Searching…";
  $("#results").innerHTML = "";
  const params = new URLSearchParams({ q, limit: $("#limit").value });
  const connector = $("#connector").value;
  if (connector) params.set("connector", connector);
  const t0 = performance.now();
  const r = await fetch("/api/search?" + params.toString()).then((r) => r.json());
  const ms = Math.round(performance.now() - t0);
  if (r.error) {
    $("#status").textContent = `Error: ${r.error}`;
    return;
  }
  $("#status").textContent = `${r.results.length} result(s) in ${ms} ms for "${q}"`;
  if (r.results.length === 0) {
    $("#results").innerHTML = `<div class="empty">No results.</div>`;
    return;
  }
  $("#results").innerHTML = r.results.map(renderCard).join("");
}
$("#form").addEventListener("submit", search);
init();
</script>
</body>
</html>
"""
def _result_card_to_dict(card) -> dict:
    return {
        "doc_id": card.doc_id,
        "title": card.title,
        "source": card.source,
        "snippet_highlight": card.snippet_highlight,
        "match_explanations": list(card.match_explanations or []),
        "semantic_neighbors": [
            {"doc_id": n.doc_id, "title": n.title, "similarity": n.similarity}
            for n in (card.semantic_neighbors or [])
        ],
    }
def _make_handler(service: LocalIndexService, indexes: dict[str, list]):
    connector_counts = sorted(
        ({"name": name, "count": len(docs)} for name, docs in indexes.items()),
        key=lambda c: c["count"],
        reverse=True,
    )
    total_items = sum(len(docs) for docs in indexes.values())
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 — matches parent signature
            sys.stderr.write(f"[serve] {format % args}\n")
        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/":
                body = _INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/connectors":
                self._send_json(200, {"total_items": total_items, "connectors": connector_counts})
                return
            if path == "/api/search":
                params = urllib.parse.parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                if not q:
                    self._send_json(400, {"error": "missing q"})
                    return
                try:
                    limit = int((params.get("limit") or ["20"])[0])
                except ValueError:
                    limit = 20
                connector = (params.get("connector") or [""])[0].strip() or None
                index_filter = [connector] if connector else None
                try:
                    results = service.query(q, indexes=index_filter, limit=limit)
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
                    return
                self._send_json(200, {"results": [_result_card_to_dict(c) for c in results]})
                return
            self._send_json(404, {"error": "not found"})
    return Handler
def cmd_serve(args: argparse.Namespace) -> None:
    print(f"Loading documents from {args.db}…", file=sys.stderr)
    indexes = _load_documents_from_db(args.db)
    if not indexes:
        print("No items in the database. Run 'python run.py ingest ...' first.", file=sys.stderr)
        sys.exit(1)
    service = LocalIndexService(indexes)
    total = sum(len(docs) for docs in indexes.values())
    handler_cls = _make_handler(service, indexes)
    server = http.server.HTTPServer((args.host, args.port), handler_cls)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving {total} item(s) at {url} — Ctrl-C to stop.", file=sys.stderr)
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
    # -- export --
    p_export = sub.add_parser("export", help="Export items as JSON or CSV (respects rights)")
    p_export.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_export.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    p_export.add_argument("--output", "-o", metavar="PATH", help="Output file (default: stdout)")
    p_export.add_argument("--connector", metavar="NAME", help="Restrict to a single connector")
    p_export.add_argument("--limit", type=int, metavar="N", help="Max items to export")
    # -- serve --
    p_serve = sub.add_parser("serve", help="Launch the local web UI")
    p_serve.add_argument("--db", default=config.db_path(), metavar="PATH", help="Database path")
    p_serve.add_argument("--host", default="127.0.0.1", metavar="HOST", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8080, metavar="PORT", help="Bind port (default: 8080)")
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
        "export": cmd_export,
        "serve": cmd_serve,
    }
    dispatch[args.command](args)
if __name__ == "__main__":
    main()
 
