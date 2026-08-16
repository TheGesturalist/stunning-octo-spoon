# AGENTS.md — working on stunning-octo-spoon safely

Orientation for any AI agent (or human) picking up this project. Read this
before touching code or data. Last updated **2026-08-16**.

---

## 1. What this is

A personal research search engine with two halves. **Connectors** (`connectors/`)
pull items from several sources into a local SQLite DB (`spoon.db`) as a single
normalized shape (`NormalizedItem`); an enrichment pass extracts facets;
`LocalIndexService` provides full-text + semantic search. **Providers**
(`providers/`) answer a query live against public web sources — Wikimedia,
OpenAlex, Are.na, Public Domain Review and more — and `federation.py` ranks both
halves together. `run.py` is the CLI (including the web UI).

Offline-first still holds: the corpus is local, and live search is opt-in per
source and never writes to the corpus without an explicit save (§9).

**Pure Python 3.10+ standard library only** — no `requests`, no `pytest`, no pip
installs. Tests are `unittest`.

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

- **PR #13 is merged.** `origin/main` is at `5480d06` and carries the Are.na
  connector and the `query_planner.py` byte-corruption fix. The old warning
  about `main` carrying that corruption no longer applies.
  - Note: the merge landed at `22da3dc`, so the last two commits on
    `feat/arena-connector` (`2994233`, `d41870d` — the §9 roadmap) were **not**
    in the merge. They are carried on the branch below.
- Active branch: **`claude/auto-agent-web-search-98c012`** — the live web search
  work (`providers/`, `federation.py`, `web_ui.py`). Branched from `origin/main`
  with those two orphaned doc commits cherry-picked on top.
  **102 tests pass.** Not pushed — pushing/merging is the owner's call (§7).
- The full Are.na re-ingest for user `johnny-dicanero` is **done**: 79
  non-private channels → 838 unique blocks. Old per-channel `arena:<slug>` rows
  were purged first.
- Keep files UTF-8 clean — the historical `query_planner.py` corruption came
  from a shell-transcript round-trip. Don't reintroduce it.

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

live query ──▶ providers/<name>.py ──▶ NormalizedItem ──┬─▶ web_cache_items
               (SearchProvider.search)                  │   (transient; NOT the corpus)
                                                        ▼
                                              federation.py ──▶ query_planner
                                              (concurrent fan-out,   .rank_candidates
                                               one scoring path)          │
                                                        │                 ▼
                                    run.py websearch / web_ui.py  ◀───────┘
```

- **Connectors** (`connectors/`, ingest): `raindrop_io`, `reader_io` (Readwise
  Reader), `tumblr`, `internet_archive`, `local_library`, `academic_private`,
  `arena`, `fixture`. Each subclasses `BaseConnector` (`connectors/base.py`).
- **Providers** (`providers/`, live search): `wikimedia` (20 bang presets),
  `academic` (OpenAlex/Crossref/arXiv/DOAJ), `arena_search`, `cultural`
  (PDR/Open Culture), `library` (the corpus behind the same interface). Each
  subclasses `SearchProvider` (`providers/base.py`). See **§9**.
- **Storage/enrichment**: `connectors/storage.py` (`upsert_item`,
  `upsert_item_with_enrichment`, plus the web-cache helpers),
  `connectors/enrichment.py`. Schema/DDL live in `connectors/schema.py`.
- **Search**: `local_index_service.py` backs `run.py search`/`serve`.
  `federation.py` backs `run.py websearch` and the web UI's `/api/federated`,
  and is what finally puts `query_planner` on a real search path.
- **CLI**: `run.py` subcommands — `init`, `ingest`, `search`, **`websearch`**,
  **`sources`**, `digest`, `health`, `stats`, `export`, `serve`.
- **Web UI endpoints**: `GET /`, `GET /api/connectors`, `GET /api/search`
  (both unchanged from v1), plus `GET /api/nav`, `GET /api/federated`,
  `GET|POST /api/prefs`, `POST /api/save`.

---

## 5. Running & testing

```bash
cd /Users/themainframe/claude_git_home/stunning-octo-spoon
python3 -m unittest discover -s tests      # full suite (102 tests) — do this after any change
python3 run.py stats                       # sanity: should report 6,693 items
python3 run.py serve                       # web UI at http://localhost:8080
python3 run.py search "query" --indexes arena --limit 20   # local corpus only
python3 run.py sources                     # every searchable source + its bang
python3 run.py websearch "query" --explain # library + live web, ranked together
python3 run.py websearch '!wphd deletion'  # one constrained source via its bang
```

The test suite is fully mocked — it makes no network calls and needs no tokens.

No environment setup needed (stdlib only). `SPOON_DB_PATH` overrides the default
`./spoon.db`; connector tokens come from `--token` flags or `SPOON_*` env vars
(`SPOON_RAINDROP_TOKEN`, `SPOON_READWISE_TOKEN`, `SPOON_TUMBLR_API_KEY`/`_BLOG`,
`SPOON_ARENA_TOKEN`, `SPOON_ARENA_THROTTLE`).

Live search adds two optional ones, both unset by default:
`SPOON_USER_AGENT` (override the outgoing UA) and `SPOON_ACADEMIC_MAILTO`
(a contact address puts OpenAlex/Crossref requests in their faster "polite
pool"). Are.na live search reuses `SPOON_ARENA_TOKEN` where it helps.

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
| `websearch` returns only one source | Raise `--focused-diverse` (or the slider). At 0 the ranking is pure relevance and a strong source legitimately sweeps. |
| A source shows `ERR` in the outcome strip | Expected and non-fatal — the other sources still returned. The error text is on the pill / in stderr. |
| Every provider takes ~12s | The cache DDL is re-running per call and serializing on SQLite's write lock. See §9 "Performance trap". |
| Are.na block search returns nothing globally | Not a bug — `/v2/search/blocks` 403s for API clients. Reach is channel-first by design (§9). |
| Wikimedia 429s | Burst throttling. `_http.py` honors `Retry-After`; set `SPOON_USER_AGENT` to something identifying. |
| A saved web result didn't appear in `stats` | `stats` counts `normalized_items`. Confirm the save returned `saved: true` — cached-but-unsaved results live in `web_cache_items` and are invisible to `stats` on purpose. |

---

## 9. Live web search — `providers/` (added 2026-08-16)

The project began as a constrained wiki-search tool (the owner's **Research
Console**) and, until now, every connector only *ingested* into the corpus.
There is now a second half: **query-time search against live public sources.**

```
connectors/   pull on a schedule  -> spoon.db          (the curated corpus)
providers/    answer a query now  -> web_cache_items   (transient)
federation.py fans out to both, ranks the union through query_planner
```

Both emit `NormalizedItem`, so a Raindrop bookmark and a Wikipedia page are
scored by identical rules rather than merged after the fact.

### Built providers

| Group | Providers | Notes |
|---|---|---|
| Wikimedia | 20 bang presets over the Action API | `wp` `meta` `mw` `wix`; namespaces `wpp` `wpmw` `wpc` `wph` `wpi` `wpu` `wpt`; community `wphd` `wpfaq` `wps` `wptm` `vpg` `vpp` `vpt` `vpo`; operator `wpl` |
| Academic | OpenAlex · Crossref · arXiv · DOAJ | all keyless |
| Are.na | channel search · block search | channel-first, see below |
| Canonical/cultural | Public Domain Review · Open Culture | see caveats below |
| Your library | one provider per connector | `library:raindrop_io`, … |

`python3 run.py sources` lists every one with its id and bang.

### Source constraints worth knowing before you "fix" something

- **Are.na block search is closed to API clients.** `/v2/search/blocks` returns
  **403 even with a valid PAT**, and `/v2/search` reports `authenticated: false`
  and always returns `blocks: []`. This is not a bug in the connector and no
  token will fix it. Global reach is reconstructed channel-first: search
  channels, then read the top matches' contents through the public contents
  endpoint and score blocks locally.
- **Google Scholar is intentionally absent.** No API, and automated querying is
  against its terms. Do not add a scraper. (The owner also declined a
  launch-link, on the grounds that it duplicates a bookmark they already have.)
- **Wikimedia `intitle:`/`prefix:` are not sufficient on their own.**
  CirrusSearch indexes *redirect* titles, so `intitle:"List of"` admits articles
  that merely have such a redirect. Presets re-check the real title client-side
  (`_title_allowed`) and over-fetch to compensate. Don't remove that guard.
- **Wikimedia will 429 you** on a burst. `providers/_http.py` honors
  `Retry-After` and sends a policy-compliant User-Agent (override with
  `SPOON_USER_AGENT`).
- **Public Domain Review has no API**, but its Gatsby build ships the browse
  data as static JSON (`/page-data/collections/page-data.json`,
  `/page-data/essays/page-data.json`). That gives the *full* archive — 1,255
  collections + 343 essays — not just the 100-item RSS feed.
- **Open Culture's site search is a Google CSE.** Any request carrying `s=` or
  `search=` 302s to `/gcsearch`. The WP REST *listing* works, so the provider
  indexes a bounded recent window and says so in its nav hint. Also: asking for
  full post bodies makes the endpoint hang and return nothing — the provider
  uses `_fields` deliberately.
- **Semantic Scholar** is not wired up: unauthenticated requests 429 immediately
  and the owner chose to lean on OpenAlex instead.

### The corpus boundary (important)

Live results are cached in **`web_cache_items`**, never in `normalized_items`.
A stray `SELECT * FROM normalized_items` can therefore never see transient web
hits, and `stats`/`digest`/`export` stay honest. The **only** path across the
line is `storage.save_web_item_to_library()`, driven by the per-result
"Save to library" button. Keep it that way — the 6,693-item corpus is curated,
and silent growth would destroy that property. `tests/test_web_cache.py`
enforces this.

### Ranking

`federation.py` builds `RankCandidate`s and defers to
`query_planner.rank_candidates`, with two corrections applied around it:

1. **Length normalization.** Cosine similarity gives a one-word Are.na channel
   named "cartography" a perfect 1.0 against the query "cartography", which used
   to sweep the top of every page ahead of real articles. Semantic score is
   damped by `length_confidence(token_count)`.
2. **MMR diversification.** `rank_candidates`' diversity component scores
   `1 / results_from_this_source`, which *ties* whenever sources return equal
   counts — so a query answered by four sources still showed six Wikipedia hits.
   `_diversify()` penalizes each additional pick from an already-represented
   source, scaled by the Focused↔Diverse slider.

Per-source trust dials in the UI ride on `UserPreferenceVector.source_trust`,
which `rank_candidates` already consumed — no new ranking maths was needed.

### Performance trap (already hit once)

The cache DDL used to re-run on every call. Under the threaded fan-out that
serialized every provider on SQLite's write lock and turned a 2s search into
14s. `ensure_web_cache()` is now memoized per database per process and writes
are serialized behind `_web_cache_lock`. If searches suddenly crawl, look here
first.

---

## 10. Roadmap — what is still not built

| Planned source | Status |
|---|---|
| Cosmos (`cosmos`) | Not built (name only in `query_planner`). |
| Pinterest (`pinterest`) | Not built (name only). |
| Broader academic databases | Only `academic_private` (ingest) plus the four open providers above. |
| Semantic Scholar | Deferred — needs a free API key to be usable. |

Other open threads:

- `run.py search` still uses `LocalIndexService` alone; `websearch` is the
  federated path. Merging the two commands is a judgement call left to the owner.
- Search-mode presets (`seed_and_mutate`, `contrarian`, `time_tunnel`,
  `materiality`) are exposed by `/api/nav` but not yet wired to behavior.
- Live web results are not enriched (no facets/graph edges) until saved.

### Where the vision is written down
- **`HANDOFF.md`** — the original Wikimedia spec + bang table.
- **`README.md`** — query planner groups, ranking sliders, providers.
- **`USER_GUIDE.md`** — how to actually use it.
- **`PROJECT_STATUS.md`** — longer status narrative (pre-dates this work).
