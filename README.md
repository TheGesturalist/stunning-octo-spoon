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
