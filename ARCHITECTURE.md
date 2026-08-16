# Architecture — stunning-octo-spoon

High-level data flow. Renders on GitHub and in Obsidian. See
[AGENTS.md](AGENTS.md) for the annotated component list and
[USER_GUIDE.md](USER_GUIDE.md) for how to search.

```mermaid
flowchart TB
    subgraph SRC["External sources"]
        RA["Raindrop.io API"]
        RW["Readwise Reader API"]
        TU["Tumblr API"]
        IAA["Internet Archive"]
        LLIB["Local library (files)"]
        ARC["Are.na public channel<br/>contents (REST, no auth,<br/>User-Agent required)"]
        ARM["Are.na MCP<br/>mcp.are.na (Bearer PAT)<br/>— channel discovery only"]
    end

    subgraph CONN["connectors/ — each subclasses BaseConnector"]
        direction TB
        C1["raindrop_io · reader_io · tumblr<br/>internet_archive · local_library<br/>academic_private · fixture"]
        C2["arena.py<br/>fetch_items → normalize_item<br/>dedupe by block id (keep all<br/>channel memberships) · 429 backoff"]
    end

    NI["NormalizedItem<br/>(connectors/schema.py — canonical row)"]

    subgraph STORE["Storage + enrichment"]
        ST["storage.py<br/>upsert_item / upsert_item_with_enrichment"]
        EN["enrichment.py<br/>entities · themes · medium · mood"]
    end

    DB[("spoon.db (SQLite)<br/>normalized_items · enrichment_facets<br/>enrichment_graph_edges · provenance_events")]

    IDX["local_index_service.py<br/>BM25 full-text + semantic neighbors"]

    subgraph UI["run.py (CLI + web)"]
        CLI["search · stats · digest · export · health"]
        WEB["serve → http://localhost:8080<br/>/api/search?q=&connector="]
    end

    USER(("You"))

    ARM -. "list channel slugs<br/>(getUserContents)" .-> C2
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
    IDX --> WEB
    CLI --> USER
    WEB --> USER

    QP["query_planner.py<br/>intent routing + weighted ranking<br/>(module present; not wired into<br/>the CLI search path)"]
    QP -.-> IDX
```

## Reading the diagram

- **Ingest path (solid):** a source → its connector → `NormalizedItem` →
  `storage.py` (upsert + enrichment) → `spoon.db`.
- **Are.na is special (dashed):** the MCP is used **only to discover which
  channels exist**; the actual block *contents* are pulled over the public REST
  API with no auth. See [AGENTS.md §6](AGENTS.md).
- **Search path:** `spoon.db` → `LocalIndexService` (BM25 + semantic) → the CLI
  and web UI. `query_planner.py` exists as a routing/ranking layer but is not on
  the `run.py search`/`serve` path today (dashed).
- **One canonical DB:** the main clone's `spoon.db` (6,693 items). Backups noted
  in [AGENTS.md §2](AGENTS.md).
