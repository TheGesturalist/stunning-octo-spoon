# AGENTS.md — working on stunning-octo-spoon safely

Orientation for any AI agent (or human) picking up this project. Read this
before touching code or data. Last updated **2026-08-16**.

---

## 1. What this is

A personal, offline-first research search engine. Connectors pull items from
several sources into a local SQLite DB (`spoon.db`) as a single normalized shape
(`NormalizedItem`); an enrichment pass extracts facets; `LocalIndexService`
provides full-text + semantic search; `run.py` is the CLI (including a small web
UI). **Pure Python 3.10+ standard library only** — no `requests`, no `pytest`,
no pip installs. Tests are `unittest`.

> On this machine the interpreter is **`python3`** (not `python`).

---

## 2. Where things live

| | Path |
|---|---|
| **Main clone** (work here) | `/Users/themainframe/claude_git_home/stunning-octo-spoon` |
| Sibling worktree | `/Users/themainframe/claude_git_home/stunning-octo-spoon-john-test` (branch `john-test`) |
| **Canonical DB** | `spoon.db` in the main clone — **6,693 items** (raindrop 3,859 · reader 1,996 · arena 838) |
| DB backups | `spoon.db.pre-arena-bak` (main clone, old 5,855-item db) · `spoon.db.bak-20260816` (in the worktree, the pre-Are.na 6,622 corpus) |
| Are.na token | `~/.spoon_arena_token` (chmod 600, plaintext — **not** Keychain; see §6) |

`spoon.db` and `.env` are git-ignored (data/secrets never committed).

---

## 3. Current state (2026-08-16)

- Active branch: **`feat/arena-connector`**, open as **PR #13**
  (`github.com/TheGesturalist/stunning-octo-spoon/pull/13`). Contents:
  1. `query_planner.py` byte-corruption fix (see below),
  2. the **Are.na connector** (`connectors/arena.py`) + wiring + tests,
  3. cross-channel dedup, 429 backoff, `SPOON_ARENA_THROTTLE`.
  **46 tests pass.** PR is **not merged yet** — merging is the owner's call.
- ⚠️ **`main` currently carries a corruption bug.** PR #2 merged the
  `query_planner.py` module onto `main` with a stray double-encoded byte
  (`c3 82`) on the logger line (line ~19). PR #13 is what repairs it. Until #13
  merges, a fresh clone of `main` has the bug. Do not re-introduce it (it came
  from a shell-transcript round-trip; keep files UTF-8 clean).
- The full Are.na re-ingest for user `johnny-dicanero` is **done**: 79
  non-private channels → 838 unique blocks. Old per-channel `arena:<slug>` rows
  were purged first.

---

## 4. Architecture (see ARCHITECTURE.md for the diagram)

```
source APIs ──▶ connectors/<name>.py ──▶ NormalizedItem ──▶ connectors/storage.py
                (BaseConnector:                                  │  (SQLite upsert +
                 fetch_items / fetch_fulltext /                  │   connectors/enrichment.py)
                 normalize_item / sync_cursor)                   ▼
                                                            spoon.db  ── tables:
                                                            normalized_items,
                                                            enrichment_facets,
                                                            enrichment_graph_edges,
                                                            provenance_events, …
                                                                 │
                run.py search / serve ◀── LocalIndexService ◀────┘
                (CLI + web UI :8080)     (local_index_service.py:
                                          BM25 + semantic neighbors)
```

- **Connectors** (`connectors/`): `raindrop_io`, `reader_io` (Readwise Reader),
  `tumblr`, `internet_archive`, `local_library`, `academic_private`, **`arena`**,
  `fixture`. Each subclasses `BaseConnector` (`connectors/base.py`) and emits
  `NormalizedItem` (`connectors/schema.py`). **These are the only connectors that
  exist.** The broader vision (Wikimedia/cross-wiki search, Public Domain Review,
  Open Culture, Cosmos, Pinterest, academic databases) is designed but not built
  — see **§9 Roadmap** and `HANDOFF.md`.
- **Storage/enrichment**: `connectors/storage.py` (`upsert_item`,
  `upsert_item_with_enrichment`), `connectors/enrichment.py`. Schema/DDL live in
  `connectors/schema.py`.
- **Search**: `local_index_service.py` is what `run.py search`/`serve` actually
  use. `query_planner.py` is a separate intent-routing/ranking layer (present,
  not wired into the CLI search path).
- **CLI**: `run.py` subcommands — `init`, `ingest`, `search`, `digest`,
  `health`, `stats`, `export`, `serve`. Web UI endpoints: `GET /`,
  `GET /api/connectors`, `GET /api/search?q=&limit=&connector=`.

---

## 5. Running & testing

```bash
cd /Users/themainframe/claude_git_home/stunning-octo-spoon
python3 -m unittest discover -s tests      # full suite (46 tests) — do this after any change
python3 run.py stats                       # sanity: should report 6,693 items
python3 run.py serve                        # web UI at http://localhost:8080
python3 run.py search "query" --indexes arena --limit 20
```

No environment setup needed (stdlib only). `SPOON_DB_PATH` overrides the default
`./spoon.db`; connector tokens come from `--token` flags or `SPOON_*` env vars
(`SPOON_RAINDROP_TOKEN`, `SPOON_READWISE_TOKEN`, `SPOON_TUMBLR_API_KEY`/`_BLOG`,
`SPOON_ARENA_TOKEN`, `SPOON_ARENA_THROTTLE`).

---

## 6. Are.na specifics (the tricky part)

- **Reading is unauthenticated**, but a real `User-Agent` header is **required**
  — Are.na sits behind Cloudflare, which 403s the default `Python-urllib` UA.
  The connector sends one.
- **Enumerating a user's channels is NOT available over plain REST anymore.**
  `GET /v2/users/:id/channels` → **401** even with a valid token; `GET /v2/me` →
  **410 Gone**. Discovery must go through the **official Are.na MCP**
  (`https://mcp.are.na/mcp`, streamable-HTTP JSON-RPC, `Authorization: Bearer
  <PAT>`): `initialize` → `notifications/initialized` → `tools/call`
  **`getUserContents`** (`{id, page, per}`). It returns `{meta, data}`; channel
  items carry `owner.id` and `visibility` ∈ `public|closed|private`.
- **Token:** personal access token from `are.na/settings/personal-access-tokens`,
  stored in `~/.spoon_arena_token`. **Do not use macOS Keychain** — the owner
  refuses it. Read it inline: `SPOON_ARENA_TOKEN="$(cat ~/.spoon_arena_token)" …`.
  **Never interpolate the token into a URL or echo it** (one leaked in an error
  during setup and had to be regenerated).
- **Rate limits:** bulk pulls trip HTTP 429 (seen: `Retry-After` ~254s). The
  connector has reactive backoff (honors `Retry-After`). For a full re-sync set
  **`SPOON_ARENA_THROTTLE=0.5`** and expect it to take minutes; run it in the
  background rather than a foreground call that can hit a 2-minute timeout.

### Re-syncing Are.na (full procedure)

1. Enumerate the owner's non-private channel slugs via the MCP `getUserContents`
   (filter `owner.id == <their id>`, `visibility in {public, closed}`).
2. **Back up `spoon.db` first.**
3. Purge old rows: `DELETE FROM <each table with a connector column> WHERE
   connector='arena' OR connector LIKE 'arena:%'`, then `VACUUM`.
4. Ingest throttled:
   `SPOON_ARENA_THROTTLE=0.5 python3 run.py ingest arena --channel "<slugs>" --db <db> --limit 3000`
   (run in background; re-run any channels skipped by 429).
5. Verify: `run.py stats`, and compare distinct channel tags against the target
   slug set.

> Known minor gap: because the last sync was split into two batches (rate
> limits), a few blocks that span both batches carry only their second batch's
> channel tags. Dedup is intact; a single throttled full re-run would perfect the
> tags.

---

## 7. Safety rules

- **Never push or merge without explicit owner approval.** This repo uses a PR
  workflow (see PR #2/#13). Direct pushes to `main` were historically held back
  on purpose.
- **The corpus is precious.** The Are.na-era corpus was painfully recovered past
  a macOS TCC block once. **Back up `spoon.db` before any destructive DB op**
  (purge, migration). Backups already exist (§2) — don't delete them.
- **Keep files UTF-8 clean** — the `query_planner.py` corruption came from stray
  bytes; run a non-ASCII scan if you suspect a repeat.
- **Secrets:** tokens live in `~/.spoon_arena_token` / `.env` only; never commit
  them, never print them, no Keychain.
- **Stack discipline:** stdlib only, `unittest` only. Don't add dependencies or
  switch to pytest.

---

## 8. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `command not found: python` | Use **`python3`**. |
| `No items in the database` | Wrong `--db`/`SPOON_DB_PATH`. Canonical DB is the main clone's `spoon.db` (6,693 items). |
| Are.na read → **403** | Missing/blocked User-Agent. The connector sets one; if calling the API directly, send a real UA. |
| Are.na channel list → **401** / `/me` → **410** | Expected — REST enumeration is retired. Use the MCP (§6). |
| Are.na bulk ingest → **429** | Rate limited. Honor `Retry-After`; set `SPOON_ARENA_THROTTLE=0.5`; run in background; re-ingest skipped channels. |
| Foreground ingest times out (~2 min) | The CLI fetches all channels before writing, so a timeout writes nothing. Re-run in the background. |
| Tests can't import `connectors`/`config` | Run from the repo root, or set `PYTHONPATH=<repo>`. Pyright "missing import" warnings on `run.py` are false positives (project root not on its path). |
| Search finds nothing expected | Check for a stale `--indexes`/`?connector=` filter; try a more common word (semantic neighbors still help). |

---

## 9. Roadmap — built vs. planned

**Important context for anyone extending this project.** The project began as a
**wiki search tool** and was always meant to grow into a constrained web-search
front end (the owner's **"Research Console"**), tying in live public sources.
Most of that is **designed but not yet implemented.** `query_planner.py` routes
queries to connector-group *names* that, in several cases, **have no connector
module behind them yet** — routing to a name is not the same as a working source.

### Built (connector modules that exist)
`raindrop_io` · `reader_io` · `tumblr` · `internet_archive` · `local_library` ·
`academic_private` · `arena` · `fixture`.

### Planned / NOT built
| Planned source | Group (in `query_planner`) | Status |
|---|---|---|
| **Wikimedia / cross-wiki** (namespace-aware) | — | **Not built. This is the flagship next feature** (`HANDOFF.md` §"What's next" #1). |
| Public Domain Review (`public_domain_review`) | Canonical/cultural | Not built (name only). |
| Open Culture (`open_culture`) | Canonical/cultural | Not built (name only). |
| Cosmos (`cosmos`) | Visual | Not built (name only). |
| Pinterest (`pinterest`) | Visual | Not built (name only). |
| Library indexes / academic databases | Academic | Only `academic_private` exists; the broader group is not built. |

### The Wikimedia connector — the origin of the project
`HANDOFF.md` §1 has the full spec: one `WikimediaConnector(site, namespace,
query)` over the MediaWiki Action API (`/w/api.php`, no auth for reads), driven by
a **preset map keyed by the owner's "bang" vocabulary** that mirrors the Research
Console:

- **Sites:** `wp` English Wikipedia (mainspace) · `meta` Meta-Wiki · `mw`
  MediaWiki.org · `wix` WikiIndex
- **en.wp namespaces:** `wpp` Project · `wpmw` MediaWiki · `wpc` Category ·
  `wph` Help · `wpi` Image/File · `wpu` User · `wpt` Template
- **Community/process pages:** `wphd` Help Desk · `wpfaq` FAQ · `wps` The Signpost
  · `wptm` Template Messages · `vpg`/`vpp`/`vpt`/`vpo` Village Pump
  (General/Policy/Technical/Other)
- **Operator:** `wpl` list articles (`intitle:"List of"`)

### Where the full vision is written down
- **`HANDOFF.md`** — the primary design doc: Wikimedia connector spec + bang
  table + open questions (started as the "wiki search tool").
- **`README.md`** (Query Planner) — the connector *groups* (academic / visual /
  canonical-cultural / personal memory) and ranking/mode presets.
- **`PROJECT_STATUS.md`** — longer status narrative.
- **`~/2026_gemini_local/research_console_architecture.pdf`** — the original
  Research Console / bang UI that seeded the whole thing.

### Suggested starting point for a fresh session
Build the `WikimediaConnector` per `HANDOFF.md` §1 (mirror `connectors/arena.py`
for structure and `tests/test_arena_connector.py` for the mock-API test pattern),
register its bang presets in `run.py`'s ingest CLI, then layer PDR / Open Culture
on top. None of the planned sources need auth for reads.
