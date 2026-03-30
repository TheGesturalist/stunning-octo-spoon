# stunning-octo-spoon

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

### Search mode presets (one-click UI modes)

The planner also supports mode presets designed for one-click selection in a search UI:

- `seed_and_mutate`: start from one URL/image/note and evolve related paths.
- `contrarian`: intentionally surface opposing aesthetics/arguments.
- `time_tunnel`: follow the same concept across decades.
- `materiality`: prioritize scans, marginalia, ephemera, and archives.

Use `get_search_mode_presets()` to render preset labels/descriptions in the UI, and pass
the selected `SearchMode` into `plan_query(...)`.

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
