# Static Research Console — for MkDocs / Firebase / any static host

`research-console.html` is a dependency-free, backend-free port of the spoon web
UI. It runs the same federated search **in the browser**, so it can live on a
static site with no server, no build step, and no API keys.

Alongside it, the full app can be reached over a Cloudflare Tunnel — see
[Hosting the full app](#hosting-the-full-app-behind-cloudflare-access) below.
The two are complementary: the static page is the public front door, the
tunnelled app is the real thing.

---

## What the static page can and cannot do

| | Static page | Full app (`run.py serve`) |
|---|---|---|
| Wikimedia (all 20 bang presets) | ✅ | ✅ |
| OpenAlex · Crossref · DOAJ | ✅ | ✅ |
| Are.na channel search | ✅ | ✅ |
| Open Culture | ✅ | ✅ |
| **arXiv** | ❌ no CORS | ✅ |
| **Public Domain Review** | ❌ no CORS | ✅ |
| Are.na *block* search | ❌ (needs 2 hops) | ✅ |
| Your library | titles + summaries, from a published index | full text |
| Save to library | ❌ read-only | ✅ |
| Ranking (sliders, dials, MMR) | ✅ faithful port | ✅ |
| Search modes (all four) | ✅ faithful port | ✅ |

arXiv and the Public Domain Review send no `Access-Control-Allow-Origin`
header, so a browser is not permitted to call them. Including them would need a
proxy, which a static host does not have. They are simply absent from the nav
rather than present and broken.

---

## Deploying to the Creative Wiki (MkDocs)

**1. Generate the library index.**

`--connectors` is required and has no default — nothing is published unless you
name it. The command prints what it included *and what it excluded*, so read
those two lines before deploying:

```bash
python3 run.py export-index --connectors raindrop_io,reader_io,arena -o library-index.json
```

Roughly 2 MB raw / 0.65 MB gzipped for ~6,600 items. Every static host gzips
automatically, so that is the number that matters.

To publish titles and links but no summaries:

```bash
python3 run.py export-index --connectors raindrop_io,reader_io --no-summaries -o library-index.json
```

**2. Copy both files into the MkDocs site.**

```bash
cp research-console.html library-index.json ~/antifallin/mighty-joe-black/docs/console/
```

**3. Reference it.** MkDocs copies unknown file types through untouched, so
`docs/console/research-console.html` is served at `/console/research-console.html`.
Link to it from a page, or embed it in one:

```markdown
<iframe src="/console/research-console.html" style="width:100%;height:82vh;border:0"></iframe>
```

Embedding directly (rather than iframing) also works — every selector in the
page is namespaced under `.rc`, and all the JavaScript is in one IIFE, so it
will not collide with the theme. If you embed rather than iframe, note that
MkDocs Material wraps content in its own `<form>` for the search box; the page
binds Enter explicitly for that reason.

**4. Check the index path.** The page fetches `library-index.json` *relative to
itself*. If you put the JSON elsewhere, edit `LIBRARY_INDEX_URL` near the top of
the `<script>` block. If the fetch fails the page degrades cleanly: the library
source disappears from the nav and the web sources keep working.

### The index is deliberately not in git

`library-index.json` is git-ignored. It is generated output — it would go stale,
it would bloat the repo, and a corpus dump should be published by a deliberate
act rather than by `git push`. Regenerate it whenever you want the published
index to catch up with your library.

---

## Hosting the full app behind Cloudflare Access

You already run this pattern for Calibre. Add a second ingress rule to
`~/.cloudflared/config.yml`, **above** the catch-all:

```yaml
ingress:
  - hostname: library.bluebear.one
    service: http://localhost:8080          # Calibre
  - hostname: spoon.bluebear.one            # add this
    service: http://localhost:8090
  - service: http_status:404
```

Then create the DNS route and restart the tunnel:

```bash
cloudflared tunnel route dns <your-tunnel-name> spoon.bluebear.one
```

**Put Cloudflare Access in front of it before you point DNS at anything.**
Without an Access policy the hostname is open to the internet, and the full app
exposes your entire corpus plus a write endpoint (`POST /api/save`). In the
Cloudflare Zero Trust dashboard: *Access → Applications → Add an application →
Self-hosted*, set the hostname to `spoon.bluebear.one`, and add a policy
allowing only your own email.

### Port hygiene

Run spoon on **8090**, not 8080. Your tunnel maps `library.bluebear.one` to
`localhost:8080`; anything else that binds that port gets published at that
hostname instead of Calibre. Spoon's default is 8080, so pass the port
explicitly:

```bash
python3 run.py serve --port 8090
```

The app binds `127.0.0.1` by default, so nothing is reachable until the tunnel
is deliberately pointed at it.

### Startup time

`serve` loads and indexes the whole corpus before it binds — around 18 seconds
for 6,700 items. It prints `Serving … at http://…` when it is actually ready.
A connection error before that line means it is still starting, not broken.

---

## Keeping the two in sync

The static page hard-codes its source list and ranking, mirroring
`providers/` and `federation.py`. If you add a provider to the Python app, the
static page will not learn about it automatically — add it to the `SOURCES`
array and give it a `runSource` branch. The scoring functions
(`lexicalScore`, `lengthConfidence`, `computeWeights`, the MMR pass) are
line-for-line ports, as are the four search modes (`MODES`, `decadeSpread`,
`materialFirst`, `mineTerms`, and the shared stopword list). If you change
ranking or a mode in Python, change it here too or the two consoles will
disagree about what "best" means.
