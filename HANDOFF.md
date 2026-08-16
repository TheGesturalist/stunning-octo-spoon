# Handoff — 2026-08-16

Branch: **`claude/auto-agent-web-search-98c012`** (not pushed — pushing and
merging are the owner's call, per [AGENTS.md](AGENTS.md) §7).
Base: `origin/main` @ `5480d06` (PR #13, merged), plus the two doc commits that
the merge left behind on `feat/arena-connector`.

**102 tests pass** (was 46). Stdlib only, `unittest` only, no new dependencies,
no network access in the test suite.

---

## What shipped

Live, query-time web search — the thing the project was originally for. Until
now every connector only *ingested*; there was no way to ask a question of the
open web and have the answer ranked next to your own material.

### 1. `providers/` — a second package beside `connectors/`

`connectors/` pull on a schedule; `providers/` answer a query now. Both emit
`NormalizedItem`, which is what lets them be ranked together.

| File | What it covers |
|---|---|
| `wikimedia.py` | The full 20-bang Research Console table over the MediaWiki Action API |
| `academic.py` | OpenAlex, Crossref, arXiv, DOAJ — all keyless |
| `arena_search.py` | Platform-wide Are.na (channel-first, see below) |
| `cultural.py` | Public Domain Review (full archive), Open Culture (recent window) |
| `library.py` | The existing corpus, behind the same interface |
| `base.py` / `_http.py` | The `SearchProvider` contract; shared HTTP with retry/backoff |

### 2. `federation.py`

Concurrent fan-out, per-source caching, and ranking of the union through
`query_planner.rank_candidates` — **the first time `query_planner` sits on a
real search path.** No single provider can break a search; failures are
reported per source and the rest still return.

### 3. Web UI v2 (`web_ui.py`)

Left-hand source nav with the bang vocabulary, the three ranking sliders wired
to `compute_rank_weights`, per-source trust dials, a per-source outcome strip,
and persisted settings. Every v1 endpoint (`/`, `/api/connectors`,
`/api/search`) still behaves exactly as before.

### 4. CLI

`run.py websearch` (with `--sources` and the three slider flags) and
`run.py sources`. `!bang` syntax works in the CLI and the search box.

---

## Things I learned the hard way (don't re-litigate these)

1. **Are.na block search is closed to API clients.** `/v2/search/blocks` returns
   403 *even with a valid PAT*, and `/v2/search` reports `authenticated: false`
   with `blocks: []` always. No token fixes this. Global reach is reconstructed
   channel-first: search channels → read the top matches' contents over the
   public endpoint → score blocks locally.
2. **Wikimedia's `intitle:`/`prefix:` operators are not sufficient alone.**
   CirrusSearch indexes *redirect* titles, so `intitle:"List of"` happily
   returns "Geography and cartography in the medieval Islamic world". Presets
   re-check the real title client-side and over-fetch to compensate.
3. **Public Domain Review has no API but ships its browse data as static JSON**
   (`/page-data/collections/page-data.json`, `/page-data/essays/page-data.json`)
   — 1,255 collections + 343 essays, i.e. the whole archive. That is far better
   than the 100-item RSS feed, which was the obvious first move.
4. **Open Culture's search is a Google CSE.** Anything with `s=`/`search=` 302s
   to `/gcsearch`. The REST *listing* works, so it's a bounded recent window —
   labelled as such in the nav. Requesting full post bodies makes the endpoint
   hang and return nothing; `_fields` is deliberate.
5. **Re-running the cache DDL per call cost 12s per search.** Under the threaded
   fan-out every provider serialized on SQLite's write lock. `ensure_web_cache()`
   is memoized per DB per process now. Searches went 14s → 2.2s cold, ~0s warm.
6. **Short documents broke ranking.** A one-word Are.na channel named
   "cartography" scored a perfect cosine and swept the top five ahead of the
   Wikipedia article. Fixed with a length-confidence damping factor.
7. **The planner's diversity term can't break ties.** It scores
   `1 / results_from_this_source`, identical for any two sources returning the
   same count — so a query answered by four sources still showed six Wikipedia
   hits. Added an MMR pass driven by the Focused↔Diverse slider, which now
   genuinely changes the source mix.

---

## Decisions the owner made (recorded so they aren't quietly reversed)

- **Live results are cached in a separate namespace**, not in
  `normalized_items`. Promotion is an explicit per-item "Save to library".
  The curated corpus stays exactly as curated as they made it.
- **OpenAlex over Semantic Scholar.** S2 429s immediately without an API key;
  OpenAlex is keyless and wider. S2 is left unwired.
- **Google Scholar is out entirely** — not even a launch-link. It has no API,
  automated querying is against its terms, and a link duplicates a bookmark
  they already have.

---

## What's next, roughly in order of payoff

1. **Wire the search-mode presets.** `seed_and_mutate`, `contrarian`,
   `time_tunnel`, `materiality` are already served by `/api/nav` and rendered as
   data, but nothing consumes the selection yet. `federation.py` is the natural
   place — each mode is a transform on sliders + source selection.
2. **Enrich saved web items.** `save_web_item_to_library` runs
   `upsert_item_with_enrichment`, so facets are extracted on save — but nothing
   re-enriches items saved before a change to `enrichment.py`. A
   `run.py reenrich` command is still missing (it was already on the old list).
3. **Merge `search` and `websearch`?** `search` is library-only and
   `websearch` is federated. One command with a `--local-only` flag would be
   tidier, but it changes a documented interface, so it's the owner's call.
4. **Cosmos and Pinterest** remain unbuilt names in `query_planner`. Neither has
   a friendly public API; check before promising anything.
5. **Per-source result limits in the UI.** Currently one global limit is split
   across sources; a heavy source can crowd a light one before ranking even
   starts.

---

## How to start the next session

From `/Users/themainframe/claude_git_home/stunning-octo-spoon` (the canonical
clone — note this branch was developed in a worktree under `Documents/GitHub`):

```bash
git fetch && git checkout claude/auto-agent-web-search-98c012
python3 -m unittest discover -s tests    # expect 102 passing
python3 run.py sources                   # see what's wired
python3 run.py serve                     # then open http://localhost:8080
```

Read [AGENTS.md](AGENTS.md) §9 before touching `providers/` — it records the
source constraints above, and several of them look like bugs if you don't know
they're deliberate.
