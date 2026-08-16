# stunning-octo-spoon — User Guide

Your personal research search engine. It holds everything you've collected —
Raindrop bookmarks, Readwise Reader highlights, and your Are.na blocks — in one
local database, **and now searches the live web alongside it**: Wikipedia and its
sibling wikis, open academic databases, all of Are.na, and the Public Domain
Review.

**Your library:** 6,693 items — Raindrop 3,859 · Readwise 1,996 · Are.na 838.
**Live sources:** 28 more, each switchable on and off.

---

## The fastest way: the web app

From a Terminal:

```bash
cd /Users/themainframe/claude_git_home/stunning-octo-spoon
python3 run.py serve --port 8090
```

Then open **http://localhost:8090**. Leave the Terminal window open while you
use it; press **Ctrl-C** there to stop the server.

> Use port **8090**, not the default 8080 — Calibre uses 8080, and your
> Cloudflare tunnel publishes whatever is on it at `library.bluebear.one`.
> It also takes ~18 seconds to start; it prints `Serving …` when it's ready.

> Use `python3`, not `python`, on this Mac.

### What you'll see

**Left-hand nav — pick your sources.** Everything is grouped:

| Group | What's in it |
|---|---|
| Your library | Raindrop · Readwise · Are.na, with item counts |
| Wikimedia · General | English Wikipedia, Meta-Wiki, MediaWiki.org, WikiIndex |
| Wikimedia · Namespaces | Project, MediaWiki, Category, Help, Image, User, Template pages |
| Wikimedia · Community | Help Desk, FAQ, Signpost, Template Messages, the four Village Pumps |
| Wikimedia · Operators | List articles (`intitle:"List of"`) |
| Academic | OpenAlex, Crossref, arXiv, DOAJ |
| Are.na | Channel search and block search — **all** of Are.na, not just yours |
| Canonical & cultural | Public Domain Review, Open Culture |

Each group has **all** / **none** links. A sensible handful is ticked on first
load; your choice is remembered when you press **Save settings**.

**Ranking weights — tune what "best" means.** Three sliders:

- **Relevant ↔ Surprising** — exact matching versus novelty
- **Focused ↔ Diverse** — let the strongest source dominate, or interleave
  sources so every one that answered gets a look in
- **Recent ↔ Timeless** — how much publication date matters

Below them, **Source trust** gives every selected source its own dial from −1 to
+1: push OpenAlex up and Open Culture down and the ranking follows. Expand
**Computed component weights** to see exactly what the sliders produced.

**Search mode — change what the search is *for*.** Five buttons above the
sliders:

| Mode | Use it when |
|---|---|
| Standard | You want the best matches, ranked plainly. |
| Seed-and-mutate | You don't quite know the right words yet. It searches, learns the vocabulary from what came back, and searches again on that. |
| Contrarian | You want the argument, not the consensus — it forces spread across sources and runs a second pass for critique and debate. |
| Time tunnel | You want to watch an idea move through time. Recency stops counting and results are spread one-per-decade. |
| Materiality | You want the objects — scans, images, ephemera — rather than writing about them. |

The line under the search box tells you what the mode did, including which
sources it pulled in and (for seed-and-mutate) which terms it branched on.

**Results.** Each card shows the title (linking out), which source it came from,
a highlighted snippet, and its score breakdown. Items already in your library are
badged as such. Anything from the web has a **Save to library** button — that is
the only way something enters your collection, so browsing the web through this
app never quietly grows your corpus.

**The strip above the results** reports every source that ran: how many results,
how long it took, whether it came from cache, and — if one failed — why. A
source going down is visible, never silent.

### Bangs: search one source instantly

Type a `!bang` in the search box and it overrides the checkboxes for that
search:

```
!wphd deletion criteria      → just the Wikipedia Help Desk
!oa defamiliarization        → just OpenAlex
!pdr sea monsters            → just the Public Domain Review
!wpl medieval maps           → just "List of…" articles
```

These are the same bangs from your Research Console. `python3 run.py sources`
prints the full list. A bang you mistype is simply searched as text, so nothing
silently goes to the wrong place.

---

## Searching from the Terminal (no browser)

```bash
python3 run.py search "cartography"
```

Useful options:

| You want… | Command |
|---|---|
| More results | `python3 run.py search "portraiture" --limit 25` |
| Only your Are.na | `python3 run.py search "beasts" --indexes arena` |
| Only Raindrop + Readwise | `python3 run.py search "essay" --indexes raindrop_io,reader_io` |

The source (index) names are: **`arena`**, **`raindrop_io`**, **`reader_io`**.

### Searching the web from the Terminal too

`search` looks only at your library. `websearch` searches your library **and**
the live sources together:

```bash
python3 run.py sources                          # list every source and its bang
python3 run.py websearch "defamiliarization"    # library + a default set of web sources
python3 run.py websearch '!wphd deletion'       # one source, via its bang
python3 run.py websearch "maps" --sources wikimedia:wp,academic:openalex,cultural:pdr
```

The same three sliders are available as flags, each `0`–`1`:

```bash
python3 run.py websearch "maps" --focused-diverse 1.0   # spread across sources
python3 run.py websearch "maps" --recent-timeless 0.0   # ignore how new things are
python3 run.py websearch "maps" --relevant-surprising 0.8
python3 run.py websearch "maps" --explain               # show the score breakdown
python3 run.py websearch "maps" --mode time_tunnel      # spread across decades
python3 run.py websearch "maps" --mode materiality      # scans and images first
```

Quote a query containing a `!` with single quotes, or your shell will complain.

**Reading a result:**
- **Snippet** — the matching passage, with your search terms marked.
- **`> Matched phrase in paragraph 3`** — where/why it matched.
- **`> Similar to note … from <date>`** — items the engine thinks are related.
- **`Similar:`** — a quick list of nearby items to explore.

---

## Other things it can tell you

```bash
python3 run.py stats      # how many items, by source
python3 run.py digest     # a "what's new this week" summary by source & theme
```

---

## Tips for good searches

- **Start broad, then filter.** Search a concept, then narrow with `--indexes`
  if one source is drowning out the others.
- **It's not just keywords.** The engine also finds semantically *similar*
  items, so a search for "maps" can surface "cartography" material even without
  the exact word — follow the **Similar:** links.
- **Your Are.na channel names are searchable context.** Each block is tagged
  with the channel(s) it lives in, so a term that matches a channel theme will
  pull the block in.

---

## Refreshing your Are.na (when you add new channels/blocks)

This one has a wrinkle worth knowing: **reading** Are.na needs no login, but
**discovering your channel list** now requires Are.na's MCP + your token — so a
re-sync isn't a plain one-liner. The full procedure (and everything else about
maintaining the engine) is in **[AGENTS.md](AGENTS.md)** → "Re-syncing Are.na".
The short version: ask your agent to "re-sync my Are.na into spoon," and it knows
where to look.

Adding *new sources you already have tokens for* is simpler:
```bash
python3 run.py ingest raindrop --token <...> --limit 500
python3 run.py ingest readwise --token <...> --limit 500
```

---

## If something looks wrong

- **"No items in the database"** — you're pointing at the wrong file. From the
  repo folder, `python3 run.py stats` should report 6,693 items. If it doesn't,
  see [AGENTS.md](AGENTS.md) → Troubleshooting.
- **Search returns nothing for a word you know is there** — try a shorter or
  more common form of the word, and check you didn't leave an `--indexes` filter
  on from a previous search.
- **Port already in use** — start with `--port 8090` (or any free port).
- **All the results come from one source** — push **Focused ↔ Diverse** to the
  right. At the far left the ranking is pure relevance, so a strong source is
  allowed to take every slot.
- **A source is marked red in the strip above the results** — that source failed
  (usually a timeout or rate limit) and the rest of the search carried on
  without it. Searching again a moment later normally clears it.
- **Are.na doesn't find a block you know exists** — Are.na doesn't let outside
  tools search blocks directly, so block search works by finding matching
  *channels* first and reading inside them. If the block lives in a channel whose
  title and description don't match your words, it won't surface. Your own
  Are.na is fully searchable, because that's in your library.
- **The Public Domain Review finds nothing** — it searches titles, intros and
  themes across the full archive, not the body text of essays. Try the subject
  rather than a phrase from inside a piece.
- **Nothing from a web result was added to my library** — that's deliberate.
  Press **Save to library** on a card; nothing else writes to your collection.

## A note on what isn't here

**Google Scholar** isn't one of the sources. It publishes no API and its terms
forbid automated querying, so there's no honest way to include it. The academic
sources that *are* here — OpenAlex, Crossref, arXiv, DOAJ — cover a great deal
of the same ground and are open by design.
