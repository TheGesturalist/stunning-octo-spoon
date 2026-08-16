# Handoff — 2026-08-16

Branch: **`claude/auto-agent-web-search-98c012`**.
PRs **#14** (federated web search) and **#15** (static console + export-index)
are merged into `main`. This branch now carries the search modes on top.

**130 tests pass** (was 46). Stdlib only, `unittest` only, no new dependencies,
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

`run.py websearch` (with `--sources`, the three slider flags and `--mode`),
`run.py sources`, and `run.py export-index`. `!bang` syntax works in the CLI and
the search box.

### 5. Static console (`static_console/`)

A dependency-free browser port for a static host (the Creative Wiki on Firebase),
plus `export-index` for a publishable library index and notes on running the full
app behind Cloudflare Access. Six of eight sources allow cross-origin browser
calls; arXiv and PDR do not, and are absent rather than broken.

### 6. Search modes (`search_modes.py`)

The four presets `query_planner` had advertised since it landed now do something
concrete. See AGENTS.md → "Search modes".

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

## Known debts (flagged deliberately, not oversights)

1. **The static console duplicates logic.** `static_console/research-console.html`
   re-implements the source list, the whole scoring path (`lexicalScore`,
   `lengthConfidence`, `computeWeights`, the MMR pass) and all four search modes
   in JS. Nothing enforces the parity — the first person to change ranking in
   Python only will make the published console quietly disagree, and it will not
   fail loudly. If that page becomes load-bearing, **generate it from the Python
   definitions** rather than maintaining two copies. It is noted in AGENTS.md
   and `static_console/README.md`, but documentation is not a mechanism.

2. **`search` and `websearch` overlap.** `search` is library-only,
   `websearch` is federated over library + web. One command with a
   `--local-only` flag would be tidier, but `search` is documented in
   USER_GUIDE and has a stable `--indexes` interface, so collapsing them is a
   deliberate breaking change and the owner's call.

3. **Cosmos and Pinterest are still names in `query_planner` with nothing
   behind them.** Check whether either exposes a usable public API *before*
   promising them anywhere — Pinterest's is partner-gated, and this project has
   already been bitten once by routing to sources that do not exist (which is
   what made the search-mode presets hollow for months).

4. **Port 8080 is a trap on this machine.** spoon's default is 8080; the
   cloudflared tunnel maps `library.bluebear.one` to `localhost:8080`. While a
   stale spoon server held that port, the corpus was being served at that public
   hostname instead of Calibre. Always `--port 8090`. Changing the default in
   `run.py` would be the durable fix.

5. **VS Code `git.autofetch` leaves stale ref locks.** Interrupted background
   fetches leave `.git/refs/remotes/origin/main.lock`, and git never clears it —
   the symptom is `git fetch` reporting "another git process seems to be running"
   with nothing actually running. Disabled per-repo in both clones'
   `.vscode/settings.json` (untracked, local only). If it recurs, delete the
   lock file.

---

## What's next, roughly in order of payoff

1. **Enrich saved web items.** `save_web_item_to_library` runs
   `upsert_item_with_enrichment`, so facets are extracted on save — but nothing
   re-enriches items saved before a change to `enrichment.py`. A
   `run.py reenrich` command is still missing (it was already on the old list).
2. **Per-source result limits in the UI.** Currently one global limit is split
   across sources; a heavy source can crowd a light one before ranking even
   starts.

---

## How to start the next session

From `/Users/themainframe/claude_git_home/stunning-octo-spoon` (the canonical
clone — note this branch was developed in a worktree under `Documents/GitHub`):

```bash
git fetch && git checkout main
python3 -m unittest discover -s tests    # expect 130 passing
python3 run.py sources                   # see what's wired
python3 run.py serve                     # then open http://localhost:8080
```

Read [AGENTS.md](AGENTS.md) §9 before touching `providers/` — it records the
source constraints above, and several of them look like bugs if you don't know
they're deliberate.
