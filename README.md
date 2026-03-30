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
