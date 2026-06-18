# Handoff — 2026-06-17

## Just shipped (commit `8010ec7`, pushed to `origin/main`)

Bug fix: `python run.py ingest <source> --limit N` was ignoring `N` for the
Readwise Reader connector. Root cause was upstream-side: the Readwise Reader v3
API does **not** accept a `page_size` query param — it silently returns its
default 100-item page no matter what. `run.py`'s plumbing of `--limit` into
`fetch_items()` was correct.

Fix:
- `connectors/reader_io.py` — drop the bogus `page_size` query param, slice
  `results[:limit]` client-side. Cursor param fixed to `?pageCursor=` (was
  `&pageCursor=`, which produced a malformed URL on the very first page).
- `connectors/raindrop_io.py`, `connectors/tumblr.py`,
  `connectors/internet_archive.py` — same defensive `[:limit]` slice. None of
  these had been observed to over-return (their APIs cap server-side at 50, 20,
  and respect `rows=`), but the contract is now enforced at the connector
  boundary.
- `tests/test_connector_limits.py` — new mock-based tests for all four
  connectors. Full suite: 38 tests, all pass.

---

## What's next — two new connectors

The user wants the local research engine (which already covers Internet
Archive, Raindrop, Readwise Reader, Tumblr, local library) to also cover:

### 1. Wikimedia / cross-wiki, **namespace-aware**

The model is the user's existing "Research Console" — a constrained-search UI
with bangs that mirror the structure below. Reproduce that coverage as one (or
several) connectors, not just `en.wikipedia.org` mainspace.

**Sibling projects to cover:**

| Bang | Target |
| ---- | ------ |
| `wp` | English Wikipedia (mainspace) |
| `meta` | Meta-Wiki (`meta.wikimedia.org`) |
| `mw` | MediaWiki.org |
| `wix` | WikiIndex |

**Namespaces within English Wikipedia:**

| Bang | Namespace |
| ---- | --------- |
| `wpp` | Project pages (`Wikipedia:`) |
| `wpmw` | MediaWiki pages (`MediaWiki:`) |
| `wpc` | Category pages |
| `wph` | Help pages |
| `wpi` | Image / File namespace |
| `wpu` | User pages |
| `wpt` | Template pages |

**Community / process pages (sub-targets within `Wikipedia:` namespace):**

| Bang | Target |
| ---- | ------ |
| `wphd` | Help Desk |
| `wpfaq` | FAQ |
| `wps` | The Signpost |
| `wptm` | Template Messages |
| `vpg` | Village Pump (General) |
| `vpp` | Village Pump (Policy) |
| `vpt` | Village Pump (Technical) |
| `vpo` | Village Pump (Other) |

**Operator:**

| Bang | Behavior |
| ---- | -------- |
| `wpl` | List articles (`intitle:"List of"`) |

**Reference docs the user keeps handy:**
- Wikipedia Namespace Reference
- Wikipedia Subpages Reference

**Suggested implementation shape:**

- Use the MediaWiki Action API (`https://en.wikipedia.org/w/api.php`) — every
  Wikimedia site exposes it. No auth needed for reads.
- One `WikimediaConnector` that takes `site` (host), `namespace` (int or
  string), and `query`. Construct it from a "preset" map keyed by the bang
  names above so the user-facing layer can say `wphd: "deletion criteria"` and
  the connector knows it means `site=en.wikipedia.org`,
  `namespace=4` (Project), with a title-prefix filter for `Wikipedia:Help_desk`.
- Inherit `BaseConnector` (see `connectors/base.py`) — must implement
  `fetch_items`, `fetch_fulltext`, `normalize_item`, `sync_cursor`.
- Persist as `NormalizedItem` (`connectors/schema.py`). Set `connector` to
  something like `wikimedia:en.wikipedia/help_desk` so the existing search/UI
  can filter by source.
- Honor `--limit` in `fetch_items()` (use the new contract — `[:limit]` slice
  after fetch — see the just-shipped connectors).
- Add a CLI surface in `run.py`'s `cmd_ingest` — likely
  `python run.py ingest wiki --preset wphd --query "..."` or
  `--site en.wikipedia.org --namespace 4 --query "..."`. The argparse plumbing
  in `run.py` already has the shape — mirror how `internet_archive` takes
  `--query`.
- Tests: mock the MediaWiki API JSON response (`query.search` payload). Pattern
  to follow is `tests/test_connector_limits.py`.

The original cross-wiki search that motivated this project is documented in the
PDF the user referenced: `https://alanmacfarlane.com/TEXTS/bastardy.pdf`.

### 2. Are.na

Are.na has a public REST API (`https://api.are.na/v2/`). A read-only connector
should cover:
- User-owned channels (`/v2/channels/:slug/contents`)
- Blocks (text, links, images, attachments — each block type needs slightly
  different normalization for `fulltext` / `source_url`)
- Optionally, channel search

Token: Are.na issues personal access tokens via `dev.are.na`. Pattern to
follow:
- Config plumbing in `config.py` (e.g. `arena_token()` mirroring
  `readwise_token()`).
- CLI flag `--token` with env var fallback (`SPOON_ARENA_TOKEN`).
- Inherit `BaseConnector`, same shape as `RaindropIOConnector`.

---

## Repo orientation

```
stunning-octo-spoon/
  run.py                       # CLI entrypoint — init / ingest / search /
                               # digest / health / stats / export / serve
  config.py                    # env-var-driven config helpers
  connectors/
    base.py                    # BaseConnector ABC — all connectors inherit
    schema.py                  # NormalizedItem dataclass (the canonical row)
    http_helpers.py            # get_json / get_text (urllib-based, no deps)
    storage.py                 # SQLite upsert + enrichment hooks
    enrichment.py              # post-ingest enrichment pipeline
    internet_archive.py        # existing
    raindrop_io.py             # existing
    reader_io.py               # existing (Readwise Reader v3)
    tumblr.py                  # existing
    local_library.py           # existing (filesystem)
    academic_private/          # private/academic-source adapters
  tests/                       # unittest, no third-party deps
  local_index_service.py       # in-memory BM25 + semantic search over SQLite
  query_planner.py
  PROJECT_STATUS.md            # older handoff
```

Stack: pure-stdlib Python 3.10+ (no `requests`, no `pytest` — `unittest` only).
SQLite at `$SPOON_DB_PATH` (default `./spoon.db`). The web UI is `run.py serve`
on `:8080`.

---

## Open questions for the next session

1. **One Wikimedia connector or many?** A single parameterized
   `WikimediaConnector` keyed by `site` + `namespace` is the clean option, but
   the user's bang vocabulary suggests they think of `wphd`, `vpp`, etc. as
   distinct first-class sources. Possibly: one class, but register preset
   instances at CLI-parse time so `--source wphd` works alongside
   `--source wiki --preset wphd`.
2. **Per-namespace `content_type`?** The existing connectors use coarse types
   like `bookmark`, `reading_item`, `archive_item`. Suggest `wiki_article`,
   `wiki_project_page`, `wiki_help_page`, `wiki_template`, etc. so downstream
   filters and the digest can distinguish.
3. **Are.na block-type normalization.** Image/attachment blocks have no
   `fulltext` — should they be skipped, or stored with empty fulltext and a
   `metadata.block_type` flag so the search index can opt out?
4. **Rights.** Wikimedia is CC BY-SA, Are.na content rights vary per block.
   Set sensible `NormalizedItem.rights` defaults so `run.py export` does the
   right thing (see existing rights handling in `_iter_exportable_items`).

---

## How to start the next session

From `/Users/themainframe/claude_git_home/stunning-octo-spoon`:

```
git pull
cat HANDOFF.md
```

Then ask the new session to start with the Wikimedia connector. The Are.na one
is smaller and can follow.
