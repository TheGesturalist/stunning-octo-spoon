# stunning-octo-spoon — User Guide

Your personal research search engine. It holds everything you've collected —
Raindrop bookmarks, Readwise Reader highlights, and your Are.na blocks — in one
local database and lets you search across all of it at once. Nothing leaves your
machine.

**Current library:** 6,693 items — Raindrop 3,859 · Readwise 1,996 · Are.na 838.

---

## The fastest way: the web app

From a Terminal:

```bash
cd /Users/themainframe/claude_git_home/stunning-octo-spoon
python3 run.py serve
```

Then open **http://localhost:8080** in your browser. Type a query, hit search.
Leave the Terminal window open while you use it; press **Ctrl-C** there to stop
the server when you're done.

> Use `python3`, not `python`, on this Mac.

What you can do in the web app:
- **Search** across everything with one box.
- **Filter by source** (Raindrop / Readwise / Are.na) — the sidebar shows how
  many items each has.
- Each result shows a **title, source link, a highlighted snippet**, why it
  matched, and a few **"similar" items** so you can wander sideways.

To run it on a different port (if 8080 is busy):
```bash
python3 run.py serve --port 8090
```

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
