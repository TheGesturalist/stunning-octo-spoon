# Project Status: stunning-octo-spoon

*Last updated: April 2026. Written for handoff — readable by both human owners and AI agents.*

---

## What this project is

A personal research discovery engine. It ingests content from multiple sources (bookmarks, reading apps, archives, personal libraries), stores everything in a local database, and lets you search across all of it in one place — with relevance ranking, semantic neighbours, and themed digests.

The design is deliberately offline-first and owner-controlled: no cloud service, no subscription, your data stays local. The intended user is someone who accumulates a lot of research material across many platforms and wants a unified way to find, relate, and surface it.

---

## What has been built

The project was built incrementally across ~12 pull requests. Here is what exists:

### 1. Connectors (data importers)

Each connector knows how to pull content from one source and convert it to a standard format (`NormalizedItem`). All connectors are in `connectors/`.

| Connector | Source | Status |
|---|---|---|
| `raindrop_io` | Raindrop.io bookmarks | Built, untested against live API |
| `reader_io` | Readwise Reader highlights | Built, untested against live API |
| `tumblr` | Tumblr blog posts | Built, untested against live API |
| `internet_archive` | Internet Archive search | Built, network unreachable in dev env |
| `local_library` | Local folder of documents | Built |
| `academic_private` | Paywalled academic sources | Built with rights/access policy system |
| `fixture` | JSON files (for testing) | Built and verified working |

Every item, regardless of source, is normalized into the same shape: title, author, summary, full text, URL, tags, creation date, rights, metadata.

### 2. Storage layer

All items live in a local SQLite database (`spoon.db` by default). The database has tables for:

- `normalized_items` — the canonical content records
- `enrichment_facets` — extracted tags: named entities, themes, medium types, mood/tone labels
- `enrichment_graph_edges` — semantic relationships between items and concepts
- `provenance_events` — a log of how each item entered the system and what has been done to it
- `interaction_events` — lightweight tracking of what you've viewed/saved (used for ranking)

### 3. Enrichment pipeline

When items are ingested, an enrichment pass can run automatically. It extracts:
- Named entities (people, places, organisations)
- Themes and motifs
- Medium/style labels (essay, scan, collage, manifesto, etc.)
- Mood and tone

This populates the facets and graph edges tables, which power filtered search and the "similar items" feature.

### 4. Search

`local_index_service.py` provides in-process full-text search across everything in the database. Features:
- Snippet highlighting (marks matching terms in context)
- Exact match locations (which paragraph, character range)
- Semantic nearest neighbours (finds related items even without keyword overlap)
- Match explanations ("Matched phrase in paragraph 3", "Similar to note from 2024-09-17")

### 5. Query planner

`query_planner.py` routes search queries to the right connector groups based on intent:
- Academic queries → library indexes, academic databases
- Visual queries → Pinterest, Are.na, Tumblr, Cosmos
- Cultural/canonical → Open Culture, Public Domain Review, Internet Archive
- Personal memory → local notes, bookmarks, highlights

It also supports exploratory search modes:
- `seed_and_mutate` — start from one item and evolve related paths
- `contrarian` — surface opposing aesthetics or arguments
- `time_tunnel` — follow a concept across decades
- `materiality` — prioritise scans, marginalia, ephemera, archives

And ranking sliders (designed for a UI):
- Relevant ↔ Surprising
- Focused ↔ Diverse
- Recent ↔ Timeless

### 6. Ranking

A weighted scoring system combines: lexical match score, semantic similarity, recency, novelty (distance from recently seen items), and source diversity. The weights are adjustable via the sliders described above.

### 7. Rights and access control

The `academic_private` connector and the rights system handle content that has restricted access — paywalled papers, licensed material. Each item carries a `rights` object:
- `allow_abstract` — can the summary be shown?
- `allow_fulltext` — can the full text be shown?
- `can_export` — can this item be exported?
- `export_policy` — `"full"`, `"abstract_only"`, or `"none"`

The storage and search layers respect these flags.

### 8. CLI runner

`run.py` is the command-line interface. It ties everything together.

```
python run.py init                                      # create/migrate the database
python run.py ingest raindrop --token X                 # pull from a source
python run.py ingest fixture --path fixtures/           # load test data
python run.py search "collage archive"                  # search everything
python run.py digest                                    # weekly summary of new items
python run.py health                                    # check for dead links
python run.py stats                                     # item/facet/edge counts
python run.py export --format json --output items.json  # export rights-aware
```

Credentials can be passed as flags or set as environment variables:

```
SPOON_RAINDROP_TOKEN, SPOON_READWISE_TOKEN,
SPOON_TUMBLR_API_KEY, SPOON_TUMBLR_BLOG, SPOON_DB_PATH
```

### 9. Export

`run.py export` writes the database to JSON or CSV, respecting rights:
- Items with `can_export: false` or `export_policy: "none"` are skipped.
- Items with `export_policy: "abstract_only"` are included but their `fulltext` is stripped.
- Items with empty rights default to fully exportable.

Supports `--connector NAME` and `--limit N` filters. `--output PATH` writes to a file; default is stdout.

### 10. Test suite

33 tests covering: query planner, local index service, enrichment pipeline, academic connector, export rights filtering. All passing.

---

## Current state

**What works end-to-end right now:**
- Initialize a database
- Ingest from JSON fixture files
- Search, digest, health check, stats — all functional via CLI

**What is built but untested against live services:**
- All API connectors (Raindrop, Readwise, Tumblr, Internet Archive) — the code is there but hasn't been run against real credentials in this environment

**What does not exist yet:**
- A web or desktop UI (the query planner and ranking sliders are designed for one, but nothing renders them)
- A way to re-enrich existing items without re-ingesting them
- Any connection between the query planner's connector routing and the actual connector calls in `run.py` — right now `run.py ingest` targets one source at a time; the planner's multi-source fan-out is not wired to anything

**Known bugs:**
- `--limit N` on `python run.py ingest readwise` is ignored — the connector pulls its default page size (100) regardless. Likely also affects raindrop/tumblr/internet_archive; not yet investigated.

---

## Realistic next steps

Listed roughly in order of payoff:

### A. Test with real credentials (low effort, high value)
Run `python run.py ingest raindrop --token YOUR_TOKEN` or `readwise` from your Mac terminal. This will immediately tell you whether the connectors work and put real content in the database to search against. No code changes needed.

### B. Wire the query planner to `run.py search` (medium coding task)
Right now `run.py search` just searches whatever is in the database. The query planner knows which connector groups to prioritise for a given query — connecting the two would make search intent-aware. The planner is in `query_planner.py` and is well-documented.

### C. Build a minimal web UI (larger task — design decisions required)
The ranking sliders and search mode presets in `query_planner.py` are explicitly designed for a UI. A simple local web interface (Flask or FastAPI serving a single HTML page) could expose:
- A search box
- The three ranking sliders
- Mode preset buttons (seed_and_mutate, contrarian, etc.)
- Result cards with snippet highlights and similar-item links

This would make the system genuinely usable day-to-day without touching the terminal.

### D. Add more connectors (open-ended)
The connector pattern is straightforward: implement `fetch_items(limit)` and `normalize_item(raw)` in a class that inherits from `BaseConnector`. Candidates: Are.na, Pinboard, Zotero, Notion, Obsidian vault, email newsletters.

---

## Repository layout

```
stunning-octo-spoon/
├── run.py                        CLI entry point
├── config.py                     Credential/path config from env vars
├── query_planner.py              Query routing, ranking, search modes
├── local_index_service.py        In-process full-text + semantic search
├── fixtures/
│   └── sample_items.json         5 sample essays for offline testing
├── connectors/
│   ├── schema.py                 NormalizedItem dataclass + DDL
│   ├── storage.py                SQLite persistence, enrichment, digest, health
│   ├── enrichment.py             Entity/theme/facet extraction
│   ├── base.py                   BaseConnector abstract class
│   ├── raindrop_io.py
│   ├── reader_io.py              (Readwise Reader)
│   ├── tumblr.py
│   ├── internet_archive.py
│   ├── local_library.py
│   └── academic_private/         Rights-aware academic connector
└── tests/                        28 passing tests
```

---

## For a Code Agent Picking this Up

- Python 3.10+, no external dependencies beyond the standard library (sqlite3, json, argparse, re, pathlib, urllib)
- Database path defaults to `./spoon.db`; override with `SPOON_DB_PATH`
- Run tests: `python -m pytest tests/`
- The canonical content record is `connectors.schema.NormalizedItem` — everything flows through this
- `upsert_item(db, item)` persists without enrichment; `upsert_item_with_enrichment(db, item)` runs the full pipeline
- `LocalIndexService` takes `dict[str, list[IndexedDocument]]` keyed by connector name — `run.py` populates this from the DB via `_load_documents_from_db()`
- The open PR (`claude/build-cli-runner-nfDZ8` → `main`) adds `run.py`, `config.py`, `fixtures/`, and `.gitignore` — merge this before starting new work
