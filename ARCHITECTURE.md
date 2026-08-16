# Architecture — stunning-octo-spoon

High-level data flow. Renders on GitHub and in Obsidian. See
[AGENTS.md](AGENTS.md) for the annotated component list and
[USER_GUIDE.md](USER_GUIDE.md) for how to search.

Two halves meet at `NormalizedItem`: **connectors** ingest into the corpus on a
schedule, **providers** answer a query live. `federation.py` ranks both together.

```mermaid
flowchart TB
    subgraph SRC["External sources — ingest"]
        RA["Raindrop.io API"]
        RW["Readwise Reader API"]
        TU["Tumblr API"]
        IAA["Internet Archive"]
        LLIB["Local library (files)"]
        ARC["Are.na channel contents<br/>(REST, no auth,<br/>User-Agent required)"]
        ARM["Are.na MCP<br/>mcp.are.na (Bearer PAT)<br/>— channel discovery only"]
    end

    subgraph CONN["connectors/ — subclass BaseConnector"]
        C1["raindrop_io · reader_io · tumblr<br/>internet_archive · local_library<br/>academic_private · fixture"]
        C2["arena.py<br/>dedupe by block id · 429 backoff"]
    end

    NI["NormalizedItem<br/>(connectors/schema.py — the shared shape)"]

    subgraph STORE["Storage + enrichment"]
        ST["storage.py<br/>upsert_item / upsert_item_with_enrichment"]
        EN["enrichment.py<br/>entities · themes · medium · mood"]
    end

    DB[("spoon.db — CURATED CORPUS<br/>normalized_items · enrichment_facets<br/>enrichment_graph_edges · provenance_events")]

    IDX["local_index_service.py<br/>BM25 full-text + semantic neighbors"]

    subgraph LIVE["External sources — live search"]
        WM["MediaWiki Action API<br/>en.wp · meta · mediawiki.org · wikiindex"]
        ACAD["OpenAlex · Crossref<br/>arXiv · DOAJ"]
        ARS["Are.na channel search<br/>(block search is 403 for API clients)"]
        CULT["Public Domain Review (static index)<br/>Open Culture (WP REST window)"]
    end

    subgraph PROV["providers/ — subclass SearchProvider"]
        P1["wikimedia.py — 20 bang presets"]
        P2["academic.py"]
        P3["arena_search.py"]
        P4["cultural.py"]
        P5["library.py<br/>(the corpus, same interface)"]
    end

    CACHE[("web_cache_items<br/>TRANSIENT — never the corpus")]
    FED["federation.py<br/>concurrent fan-out · one scoring path<br/>length norm + MMR diversify"]
    QP["query_planner.py<br/>rank_candidates + sliders"]

    subgraph UI["run.py (CLI + web)"]
        CLI["search · websearch · sources<br/>stats · digest · export · health"]
        WEB["serve → :8080<br/>left nav · weights · /api/federated"]
    end

    USER(("You"))

    ARM -. "list channel slugs" .-> C2
    RA --> C1
    RW --> C1
    TU --> C1
    IAA --> C1
    LLIB --> C1
    ARC --> C2
    C1 --> NI
    C2 --> NI
    NI --> ST
    ST --> EN
    ST --> DB
    EN --> DB
    DB --> IDX
    IDX --> CLI

    WM --> P1
    ACAD --> P2
    ARS --> P3
    CULT --> P4
    DB --> P5
    P1 --> FED
    P2 --> FED
    P3 --> FED
    P4 --> FED
    P5 --> FED
    FED <--> CACHE
    QP --> FED
    FED --> CLI
    FED --> WEB
    IDX --> WEB
    CLI --> USER
    WEB --> USER
    WEB -. "explicit Save to library" .-> ST
```

## Reading the diagram

- **Ingest path:** a source → its connector → `NormalizedItem` → `storage.py`
  (upsert + enrichment) → `spoon.db`.
- **Live path:** a query → the selected providers (concurrently) →
  `NormalizedItem` → `federation.py` → ranked results. Results land in
  `web_cache_items`, **not** in `spoon.db`.
- **The one crossing:** the dashed "explicit Save to library" edge. That is the
  only way a live result becomes a corpus item, which is what keeps the curated
  6,693-item collection curated.
- **The library is also a provider** (`library.py`), which is why local and web
  hits can be scored by identical rules instead of being merged after ranking.
- **Are.na is special:** the MCP discovers *which channels exist* (ingest);
  block contents come over the public REST API. Live search is channel-first
  because Are.na's block-search endpoint returns 403 to API clients. See
  [AGENTS.md §9](AGENTS.md).
- **`query_planner` is finally on a search path** — via `federation.py`. It is
  still not on the older `run.py search` path, which remains library-only.
