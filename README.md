# stunning-octo-spoon

A personal research search engine with two halves:

- **Your library** — connectors pull items from Raindrop, Readwise Reader,
  Are.na and more into one local SQLite database (6,693 items), searchable
  full-text plus semantic neighbours.
- **The live web** — providers answer a query in real time against Wikimedia
  (20 constrained presets), open academic databases, all of Are.na, and the
  Public Domain Review. Results are ranked *together* with your library.

Pure Python 3.10+ standard library; `unittest` only; no API keys required.

**Documentation**
- **[USER_GUIDE.md](USER_GUIDE.md)** — how to search (web UI + CLI), sources, bangs, ranking controls.
- **[AGENTS.md](AGENTS.md)** — for anyone (agent or human) working on the code: where things live, current state, source constraints, the Are.na re-sync procedure, safety rules, troubleshooting.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — data-flow diagram.
- **[static_console/README.md](static_console/README.md)** — publishing a browser-only console to a static site (MkDocs/Firebase), and hosting the full app behind Cloudflare Access.

Quick start:
```bash
python3 run.py serve                          # web UI at http://localhost:8090
python3 run.py sources                        # every searchable source + its bang
python3 run.py websearch "cartography"        # library + live web, ranked together
python3 run.py websearch '!wphd deletion'     # one constrained source, via its bang
python3 run.py websearch "maps" --mode time_tunnel   # exploratory search modes
python3 run.py search "cartography" --indexes arena   # library only
```

> `serve` defaults to **8090**, deliberately: the cloudflared tunnel maps
> `library.bluebear.one` to `localhost:8080`, so a server bound there is
> published at that hostname instead of Calibre. Don't move it back.

## Live search sources

| Group | Sources |
|---|---|
| Wikimedia | `wp` `meta` `mw` `wix` · namespaces `wpp` `wpmw` `wpc` `wph` `wpi` `wpu` `wpt` · community `wphd` `wpfaq` `wps` `wptm` `vpg` `vpp` `vpt` `vpo` · operator `wpl` |
| Academic | OpenAlex `oa` · Crossref `cr` · arXiv `ax` · DOAJ `doaj` |
| Are.na | channels `arc` · blocks `arb` |
| Canonical & cultural | Public Domain Review `pdr` · Open Culture `oc` |

Live results are cached in a separate `web_cache_items` table and **never** enter
the curated corpus on their own — promotion is an explicit per-item
"Save to library" action.

Two source constraints are worth knowing, because they shape the design:
Are.na does not expose block search to API clients (so block reach is
channel-first), and Google Scholar is excluded outright — no API, and automated
querying is against its terms.

## Federated search (`federation.py`)

`federated_search()` fans out to the selected providers concurrently, caches
their results, and ranks the union through `query_planner.rank_candidates`.
Local and live results are scored with the *same* primitives, so they compete
fairly rather than being stitched together afterwards.

Two corrections sit around the planner's ranking:

- **Length normalization** — raw cosine similarity gives a one-word Are.na
  channel called "cartography" a perfect score against the query "cartography".
  Semantic score is damped by document length so short titles stop sweeping the
  page.
- **MMR diversification** — the planner's diversity component scores
  `1 / results_from_this_source`, which ties whenever sources return equal
  counts. A second pass penalizes each additional pick from an
  already-represented source, scaled by the Focused↔Diverse slider.

No provider can break a search: failures are caught per source, reported in
`outcomes`, and the rest of the results still return.

## Query Planner Module

This repository now includes a `query_planner` module for routing search queries to connector groups by intent.

### Supported intents and connector groups

- **Academic** → `library_indexes`, `academic_databases`
- **Visual** → `pinterest`, `are_na`, `cosmos`, `tumblr`
- **Canonical/cultural** → `open_culture`, `public_domain_review`, `internet_archive`
- **Personal memory** → `local_notes`, `bookmarks`, `highlights`

### Optional toggles

- `deep_search`: adds canonical/cultural backfill connectors for depth.
- `fast_search`: trims connector fan-out to top connectors for lower latency.
- `visual_only`: forces visual connector routing.

### Search mode presets

`query_planner` declares the presets; **`search_modes.py` is what makes them
act.** Each translates into concrete operations the federated search performs —
widen the source set, move the sliders, add a query pass, re-rank the union:

| Mode | Behavior |
|---|---|
| `seed_and_mutate` | Searches, mines terms shared by ≥2 of its own results, searches again on those. Novelty up. |
| `contrarian` | Diversity forced to 1.0, adds academic sources, second pass on critique/debate terms. |
| `time_tunnel` | Recency weighting to 0, adds long-reach sources, re-ranks round-robin across decades. |
| `materiality` | Adds and up-weights image/archive sources; promotes scans, images and collections. |

Available as `run.py websearch --mode <name>`, as buttons in the web UI, and in
the static console. A mode only ever adds sources that exist, so it cannot
promise coverage this build lacks.

### Debugging

`plan_query(...)` records planner decisions in `debug_notes` and emits a debug log message through Python's `logging` module.

## Ranking controls and weighted scoring

`query_planner` also exposes a ranking utility with weighted components:

- lexical match (BM25/full-text index score)
- semantic match (embedding cosine similarity)
- recency / temporal relevance
- novelty (distance from recently viewed/saved content)
- source diversity bonus (to avoid top results from one platform)

UI sliders are exposed through `ranking_slider_config()`:

- **Relevant ↔ Surprising** (`relevant_surprising`)
- **Focused ↔ Diverse** (`focused_diverse`)
- **Recent ↔ Timeless** (`recent_timeless`)

Use `compute_rank_weights(sliders)` to produce normalized component weights and
`rank_candidates(candidates, sliders)` to score and sort candidates.

## Local index service

A new `local_index_service` module provides in-process querying over existing full-text indexes.

### Capabilities

- snippet highlights using `<mark>` around matching query terms
- exact term match locations (`start`, `end`, and `paragraph`)
- semantic nearest neighbors via cosine similarity on term vectors
- result-card match explanations such as:
  - `Matched phrase in paragraph 3`
  - `Similar to note note-12 from 2024-09-17`

### Quick usage

```python
from local_index_service import IndexedDocument, LocalIndexService

service = LocalIndexService(
    {
        "notes": [
            IndexedDocument(doc_id="n1", title="Note 1", text="Your indexed text", created_at="2025-01-01"),
        ],
    }
)

cards = service.query("indexed text", indexes=["notes"], semantic_neighbors=2)
```

## Post-ingestion enrichment pipeline

The connectors storage layer now supports a post-ingestion enrichment pass that extracts:

- named entities
- themes / motifs
- medium / style tags (essay, scan, collage, manifesto, etc.)
- mood / tone labels

Use `connectors.storage.upsert_item_with_enrichment(...)` to persist a normalized item plus enrichment artifacts.

### Storage model

- `enrichment_facets`: searchable facet rows (`facet_type`, `facet_value`, `confidence`)
- `enrichment_graph_edges`: graph links from item → semantic node (`edge_type`, `target_node`, `weight`)

Both tables are indexed for item lookups and facet/edge target lookups.
