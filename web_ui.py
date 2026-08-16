"""Web UI v2 — left-nav source selection, ranking weights, federated results.

v1 was a top search bar plus a single "source" dropdown over the local library.
v2 keeps every v1 endpoint working and adds the Research Console shape the owner
designed originally: a left-hand nav of constrained sources (with the bang
vocabulary), weighting controls wired to `query_planner`, and results merged
live from the web alongside the library.
"""

from __future__ import annotations

import http.server
import json
import sys
import urllib.parse
from typing import Any, Mapping, Sequence

from connectors.storage import (
    load_search_preferences,
    save_search_preferences,
    save_web_item_to_library,
)
from federation import federated_search, parse_bangs
from local_index_service import IndexedDocument, LocalIndexService
from providers import bang_map, default_selection, group_providers
from providers.base import SearchProvider
from query_planner import (
    RankingSliders,
    SearchMode,
    get_search_mode_presets,
    ranking_slider_config,
)

PREF_KEY = "web_ui"
MAX_POST_BYTES = 64 * 1024


def _slider(params: Mapping[str, list[str]], name: str, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float((params.get(name) or [default])[0])))
    except (TypeError, ValueError):
        return default


def _result_card_to_dict(card: Any) -> dict[str, Any]:
    return {
        "doc_id": card.doc_id,
        "title": card.title,
        "source": card.source,
        "snippet_highlight": card.snippet_highlight,
        "match_explanations": list(card.match_explanations or []),
        "semantic_neighbors": [
            {"doc_id": n.doc_id, "title": n.title, "similarity": n.similarity}
            for n in (card.semantic_neighbors or [])
        ],
    }


def make_handler(
    *,
    db_path: str,
    service: LocalIndexService,
    indexes: Mapping[str, Sequence[IndexedDocument]],
    providers: Sequence[SearchProvider],
):
    """Build the request handler bound to this session's data and providers."""

    providers_by_id = {p.provider_id: p for p in providers}
    bangs = bang_map(providers)
    defaults = default_selection(providers)
    # Known URLs let the ranker mark a live hit as "already in your library" and
    # damp its novelty, instead of presenting it as a fresh discovery.
    library_urls = {
        doc.source for docs in indexes.values() for doc in docs if doc.source
    }
    connector_counts = sorted(
        ({"name": name, "count": len(docs)} for name, docs in indexes.items()),
        key=lambda c: c["count"],
        reverse=True,
    )
    total_items = sum(len(docs) for docs in indexes.values())

    def nav_payload() -> dict[str, Any]:
        counts = {f"library:{c['name']}": c["count"] for c in connector_counts}
        groups = []
        for group, members in group_providers(providers).items():
            groups.append(
                {
                    "group": group,
                    "sources": [
                        {**p.describe(), "count": counts.get(p.provider_id)}
                        for p in members
                    ],
                }
            )
        return {
            "total_items": total_items,
            "groups": groups,
            "defaults": defaults,
            "sliders": ranking_slider_config(),
            "modes": [
                {"mode": m.mode.value, "label": m.label, "description": m.description}
                for m in get_search_mode_presets()
            ],
            "bangs": bangs,
        }

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "spoon/2.0"

        def log_message(self, format, *args):  # noqa: A002 — parent signature
            sys.stderr.write(f"[serve] {format % args}\n")

        # -- helpers --------------------------------------------------------

        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0 or length > MAX_POST_BYTES:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        # -- GET ------------------------------------------------------------

        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)

            if path == "/":
                self._send_html(INDEX_HTML)
                return

            # -- v1 endpoints, unchanged --
            if path == "/api/connectors":
                self._send_json(
                    200, {"total_items": total_items, "connectors": connector_counts}
                )
                return

            if path == "/api/search":
                q = (params.get("q") or [""])[0].strip()
                if not q:
                    self._send_json(400, {"error": "missing q"})
                    return
                try:
                    limit = int((params.get("limit") or ["20"])[0])
                except ValueError:
                    limit = 20
                connector = (params.get("connector") or [""])[0].strip() or None
                try:
                    results = service.query(
                        q, indexes=[connector] if connector else None, limit=limit
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json(500, {"error": str(exc)})
                    return
                self._send_json(
                    200, {"results": [_result_card_to_dict(c) for c in results]}
                )
                return

            # -- v2 endpoints --
            if path == "/api/nav":
                self._send_json(200, nav_payload())
                return

            if path == "/api/prefs":
                self._send_json(
                    200, {"prefs": load_search_preferences(db_path, pref_key=PREF_KEY)}
                )
                return

            if path == "/api/federated":
                self._handle_federated(params)
                return

            self._send_json(404, {"error": "not found"})

        def _handle_federated(self, params: Mapping[str, list[str]]) -> None:
            raw_query = (params.get("q") or [""])[0].strip()
            if not raw_query:
                self._send_json(400, {"error": "missing q"})
                return
            try:
                limit = int((params.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            limit = max(1, min(limit, 100))

            selected = [
                s for s in (params.get("sources") or [""])[0].split(",") if s.strip()
            ]
            plain_query, bang_ids = parse_bangs(raw_query, bangs)
            # An explicit !bang overrides the checkbox selection for that search.
            chosen_ids = bang_ids or selected or defaults
            chosen = [providers_by_id[i] for i in chosen_ids if i in providers_by_id]
            if not chosen:
                self._send_json(400, {"error": "no known sources selected"})
                return

            sliders = RankingSliders(
                relevant_surprising=_slider(params, "rs"),
                focused_diverse=_slider(params, "fd"),
                recent_timeless=_slider(params, "rt"),
            )
            weights: dict[str, float] = {}
            raw_weights = (params.get("weights") or [""])[0]
            if raw_weights:
                try:
                    parsed_weights = json.loads(raw_weights)
                    if isinstance(parsed_weights, dict):
                        weights = {
                            str(k): float(v) for k, v in parsed_weights.items()
                        }
                except (json.JSONDecodeError, TypeError, ValueError):
                    weights = {}

            raw_mode = (params.get("mode") or ["standard"])[0].strip() or "standard"
            try:
                mode = SearchMode(raw_mode)
            except ValueError:
                self._send_json(400, {"error": f"unknown mode: {raw_mode}"})
                return

            try:
                response = federated_search(
                    plain_query or raw_query,
                    chosen,
                    sliders=sliders,
                    source_weights=weights,
                    limit=limit,
                    db_path=db_path,
                    library_urls=library_urls,
                    mode=mode,
                    all_providers=providers,
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            payload = response.to_dict()
            payload["bangs_used"] = bang_ids
            self._send_json(200, payload)

        # -- POST -----------------------------------------------------------

        def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
            path = urllib.parse.urlsplit(self.path).path
            body = self._read_json_body()

            if path == "/api/save":
                provider_id = str(body.get("provider_id") or "")
                source_id = str(body.get("source_id") or "")
                if not provider_id or not source_id:
                    self._send_json(400, {"error": "provider_id and source_id required"})
                    return
                try:
                    item = save_web_item_to_library(
                        db_path, provider_id=provider_id, source_id=source_id
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                if item is None:
                    self._send_json(
                        404, {"error": "not in the web cache — run the search again"}
                    )
                    return
                self._send_json(
                    200,
                    {
                        "saved": True,
                        "connector": item.connector,
                        "source_id": item.source_id,
                        "title": item.title,
                    },
                )
                return

            if path == "/api/prefs":
                prefs = body.get("prefs")
                if not isinstance(prefs, dict):
                    self._send_json(400, {"error": "prefs object required"})
                    return
                save_search_preferences(db_path, pref_key=PREF_KEY, payload=prefs)
                self._send_json(200, {"saved": True})
                return

            self._send_json(404, {"error": "not found"})

    return Handler


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stunning-octo-spoon</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fafaf7; --fg: #1a1a1a; --muted: #666; --dim: #8a8a8a; --accent: #2a5d8f;
  --card: #fff; --border: #e4e4e0; --badge: #efeee9; --mark: #ffe680;
  --ok: #3a7d44; --err: #a33a3a; --nav: #f3f2ee;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --fg: #e8e8e6; --muted: #999; --dim: #777; --accent: #7ab1e6;
    --card: #1d1e22; --border: #2c2d31; --badge: #2c2d31; --mark: #6a5b1f;
    --ok: #7fbf88; --err: #e08585; --nav: #191a1d;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 15px/1.5 -apple-system, system-ui, sans-serif; }
header { padding: 14px 20px; border-bottom: 1px solid var(--border);
         display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
h1 { margin: 0; font-size: 16px; font-weight: 600; white-space: nowrap; }
.meta { color: var(--muted); font-size: 12px; }
.layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); min-height: calc(100vh - 56px); }
nav { background: var(--nav); border-right: 1px solid var(--border);
      padding: 16px 14px 40px; overflow-y: auto; max-height: calc(100vh - 56px);
      position: sticky; top: 0; }
main { padding: 20px 24px 60px; min-width: 0; }

.searchbar { display: flex; gap: 8px; flex: 1; min-width: 260px; }
#q { flex: 1; padding: 9px 12px; font: inherit; background: var(--card); color: var(--fg);
     border: 1px solid var(--border); border-radius: 6px; }
#q:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
button { padding: 9px 14px; font: inherit; cursor: pointer; border-radius: 6px;
         background: var(--accent); color: #fff; border: 1px solid var(--accent); }
button:hover { opacity: .9; }
button.ghost { background: transparent; color: var(--muted); border-color: var(--border); }
button.ghost:hover { color: var(--fg); }

.nav-section { margin-bottom: 18px; }
.nav-section h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
                  color: var(--dim); margin: 0 0 7px; font-weight: 600;
                  display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
.nav-section h2 .grouptools { display: flex; gap: 6px; }
.nav-section h2 a { color: var(--dim); text-decoration: none; cursor: pointer; font-weight: 400;
                    text-transform: none; letter-spacing: 0; }
.nav-section h2 a:hover { color: var(--accent); }
.src { display: flex; align-items: flex-start; gap: 7px; padding: 3px 4px; border-radius: 4px;
       cursor: pointer; font-size: 13px; }
.src:hover { background: var(--card); }
.src input { margin-top: 3px; flex-shrink: 0; }
.src .label { flex: 1; min-width: 0; }
.src .hint { display: block; color: var(--dim); font-size: 11px; }
.src .bang { color: var(--accent); font-size: 11px; font-family: ui-monospace, monospace; flex-shrink: 0; }
.src .count { color: var(--dim); font-size: 11px; flex-shrink: 0; }

.weights { border-top: 1px solid var(--border); padding-top: 14px; }
.wrow { margin-bottom: 12px; }
.wrow label { display: flex; justify-content: space-between; font-size: 11px;
              color: var(--muted); margin-bottom: 3px; }
.wrow input[type=range] { width: 100%; accent-color: var(--accent); }
.modes { }
.mode-btn { display: block; width: 100%; text-align: left; margin-bottom: 4px; padding: 5px 9px;
            font-size: 12px; background: var(--card); color: var(--fg); border: 1px solid var(--border); }
.mode-btn:hover { border-color: var(--accent); }
.mode-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.mode-btn small { display: block; font-size: 10.5px; opacity: .75; margin-top: 1px; }
.mode-notes { font-size: 11.5px; color: var(--muted); margin-bottom: 10px;
              border-left: 2px solid var(--accent); padding-left: 9px; }
.dials { margin-top: 6px; }
.dial { display: grid; grid-template-columns: 1fr auto; gap: 4px 8px; align-items: center;
        font-size: 12px; margin-bottom: 6px; }
.dial span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dial input { grid-column: 1 / -1; width: 100%; accent-color: var(--accent); }
.dial em { color: var(--dim); font-style: normal; font-size: 11px; font-variant-numeric: tabular-nums; }
details.computed { margin-top: 10px; font-size: 11px; color: var(--muted); }
details.computed pre { white-space: pre-wrap; word-break: break-word; margin: 6px 0 0;
                       font-size: 10px; line-height: 1.5; }

.status { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
.outcomes { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 14px; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border);
        background: var(--card); color: var(--muted); white-space: nowrap; }
.pill.ok { border-color: var(--ok); color: var(--ok); }
.pill.err { border-color: var(--err); color: var(--err); }
.pill.cached { opacity: .75; }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 14px 16px; margin-bottom: 10px; }
.card h3 { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
.card h3 a { color: var(--fg); text-decoration: none; }
.card h3 a:hover { color: var(--accent); }
.badge { display: inline-block; padding: 1px 7px; font-size: 10px; background: var(--badge);
         color: var(--muted); border-radius: 4px; margin-left: 6px; vertical-align: middle; }
.badge.lib { color: var(--ok); }
.src-url { font-size: 11px; color: var(--dim); word-break: break-all; }
.src-url a { color: var(--dim); }
.snippet { margin: 7px 0; font-size: 14px; }
.snippet mark { background: var(--mark); padding: 0 2px; border-radius: 2px; }
.cardfoot { display: flex; gap: 10px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
.cardfoot button { padding: 3px 10px; font-size: 12px; }
.scores { font-size: 11px; color: var(--dim); font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); text-align: center; padding: 48px 20px; }
.err-banner { color: var(--err); font-size: 13px; margin-bottom: 12px; }
@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  nav { position: static; max-height: none; border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
</head>
<body>
<header>
  <h1>stunning-octo-spoon</h1>
  <form class="searchbar" id="form">
    <input id="q" type="text" placeholder="Search library + web…  (try !wphd, !oa, !pdr)" autofocus>
    <button type="submit">Search</button>
  </form>
  <div class="meta" id="meta">loading…</div>
</header>

<div class="layout">
  <nav>
    <div id="nav"></div>
    <div class="weights">
      <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:0 0 8px;">Search mode</h2>
      <div id="modes"></div>
      <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:14px 0 9px;">Ranking weights</h2>
      <div id="sliders"></div>
      <div class="dials">
        <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:12px 0 6px;">Source trust</h2>
        <div id="dials"><div style="font-size:11px;color:var(--dim)">Select sources to weight them.</div></div>
      </div>
      <details class="computed">
        <summary>Computed component weights</summary>
        <pre id="computed">run a search…</pre>
      </details>
      <div style="margin-top:12px;display:flex;gap:6px;">
        <button class="ghost" id="savePrefs" type="button">Save settings</button>
        <button class="ghost" id="resetPrefs" type="button">Reset</button>
      </div>
    </div>
  </nav>

  <main>
    <div class="status" id="status"></div>
    <div class="mode-notes" id="modeNotes"></div>
    <div class="outcomes" id="outcomes"></div>
    <div id="results"><div class="empty">Pick sources at left, type a query, hit Search.</div></div>
  </main>
</div>

<script>
const $ = (s) => document.querySelector(s);
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let NAV = null;
let mode = "standard";
let selected = new Set();
let dials = {};           // provider_id -> -1..1
const sliders = { rs: 0.5, fd: 0.5, rt: 0.5 };

async function init() {
  NAV = await fetch("/api/nav").then((r) => r.json());
  const stored = await fetch("/api/prefs").then((r) => r.json()).catch(() => ({}));
  const prefs = stored && stored.prefs;
  if (prefs && Array.isArray(prefs.selected) && prefs.selected.length) {
    selected = new Set(prefs.selected);
    Object.assign(sliders, prefs.sliders || {});
    dials = prefs.dials || {};
    mode = prefs.mode || "standard";
  } else {
    selected = new Set(NAV.defaults);
  }
  $("#meta").textContent = `${NAV.total_items.toLocaleString()} items in library · ${countSources()} sources available`;
  renderNav();
  renderModes();
  renderSliders();
  renderDials();
}

function renderModes() {
  const all = [{ mode: "standard", label: "Standard", description: "Straight relevance ranking." },
               ...(NAV.modes || [])];
  $("#modes").innerHTML = all.map((m) => `
    <button type="button" class="mode-btn ${m.mode === mode ? "active" : ""}" data-mode="${esc(m.mode)}">
      ${esc(m.label)}<small>${esc(m.description)}</small>
    </button>`).join("");
  $("#modes").querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => { mode = b.dataset.mode; renderModes(); });
  });
}

function countSources() {
  return NAV.groups.reduce((n, g) => n + g.sources.length, 0);
}

function renderNav() {
  $("#nav").innerHTML = NAV.groups.map((g, gi) => `
    <div class="nav-section">
      <h2>${esc(g.group)}
        <span class="grouptools"><a data-g="${gi}" data-act="all">all</a><a data-g="${gi}" data-act="none">none</a></span>
      </h2>
      ${g.sources.map((s) => `
        <label class="src">
          <input type="checkbox" data-id="${esc(s.provider_id)}" ${selected.has(s.provider_id) ? "checked" : ""}>
          <span class="label">${esc(s.label)}${s.hint ? `<span class="hint">${esc(s.hint)}</span>` : ""}</span>
          ${s.count != null ? `<span class="count">${s.count.toLocaleString()}</span>` : ""}
          ${s.bang ? `<span class="bang">!${esc(s.bang)}</span>` : ""}
        </label>`).join("")}
    </div>`).join("");

  $("#nav").querySelectorAll("input[type=checkbox]").forEach((el) => {
    el.addEventListener("change", () => {
      el.checked ? selected.add(el.dataset.id) : selected.delete(el.dataset.id);
      renderDials();
    });
  });
  $("#nav").querySelectorAll("a[data-act]").forEach((el) => {
    el.addEventListener("click", () => {
      const group = NAV.groups[+el.dataset.g];
      for (const s of group.sources) {
        el.dataset.act === "all" ? selected.add(s.provider_id) : selected.delete(s.provider_id);
      }
      renderNav(); renderDials();
    });
  });
}

function renderSliders() {
  const cfg = NAV.sliders || {};
  const rows = [
    ["rs", "relevant_surprising"],
    ["fd", "focused_diverse"],
    ["rt", "recent_timeless"],
  ];
  $("#sliders").innerHTML = rows.map(([key, name]) => {
    const c = cfg[name] || {};
    return `<div class="wrow">
      <label><span>${esc(c.min_label || name)}</span><span>${esc(c.max_label || "")}</span></label>
      <input type="range" min="0" max="1" step="0.05" value="${sliders[key]}" data-k="${key}">
    </div>`;
  }).join("");
  $("#sliders").querySelectorAll("input[type=range]").forEach((el) => {
    el.addEventListener("input", () => { sliders[el.dataset.k] = parseFloat(el.value); });
  });
}

function allSources() {
  return NAV.groups.flatMap((g) => g.sources);
}

function renderDials() {
  const chosen = allSources().filter((s) => selected.has(s.provider_id));
  if (!chosen.length) {
    $("#dials").innerHTML = `<div style="font-size:11px;color:var(--dim)">Select sources to weight them.</div>`;
    return;
  }
  $("#dials").innerHTML = chosen.map((s) => {
    const v = dials[s.provider_id] || 0;
    return `<div class="dial">
      <span title="${esc(s.label)}">${esc(s.label)}</span><em data-v="${esc(s.provider_id)}">${v > 0 ? "+" : ""}${v.toFixed(2)}</em>
      <input type="range" min="-1" max="1" step="0.05" value="${v}" data-w="${esc(s.provider_id)}">
    </div>`;
  }).join("");
  $("#dials").querySelectorAll("input[type=range]").forEach((el) => {
    el.addEventListener("input", () => {
      const v = parseFloat(el.value);
      dials[el.dataset.w] = v;
      const out = $(`em[data-v="${CSS.escape(el.dataset.w)}"]`);
      if (out) out.textContent = (v > 0 ? "+" : "") + v.toFixed(2);
    });
  });
}

function renderOutcomes(outcomes) {
  $("#outcomes").innerHTML = (outcomes || []).map((o) => {
    const cls = o.ok ? (o.cached ? "pill ok cached" : "pill ok") : "pill err";
    const detail = o.ok ? `${o.count}${o.cached ? " cached" : ` · ${o.elapsed_ms}ms`}` : (o.error || "failed");
    return `<span class="${cls}" title="${esc(o.error || "")}">${esc(o.label)}: ${esc(detail)}</span>`;
  }).join("");
}

function renderCard(h) {
  const cs = h.component_scores || {};
  const scoreBits = ["lexical", "semantic", "recency", "novelty", "diversity"]
    .filter((k) => cs[k] != null).map((k) => `${k[0]}${k[1]}=${cs[k].toFixed(2)}`).join("  ");
  const saveBtn = h.origin === "web" && !h.in_library
    ? `<button class="ghost" data-save="${esc(h.provider_id)}|${esc(h.source_id)}">Save to library</button>` : "";
  const libBadge = h.origin === "library"
    ? `<span class="badge lib">library</span>`
    : (h.in_library ? `<span class="badge lib">already saved</span>` : "");
  return `<div class="card">
    <h3><a href="${esc(h.source_url || "#")}" target="_blank" rel="noopener">${esc(h.title || "(untitled)")}</a>
      <span class="badge">${esc(h.provider_label)}</span>${libBadge}</h3>
    ${h.author ? `<div class="src-url">${esc(h.author)}</div>` : ""}
    <div class="src-url"><a href="${esc(h.source_url || "#")}" target="_blank" rel="noopener">${esc(h.source_url || "")}</a></div>
    <div class="snippet">${h.snippet || esc((h.summary || "").slice(0, 240))}</div>
    <div class="cardfoot">
      ${saveBtn}
      <span class="scores">score ${h.score.toFixed(4)} · ${esc(scoreBits)}</span>
    </div>
  </div>`;
}

async function search(e) {
  if (e) e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  if (!selected.size) {
    $("#status").innerHTML = `<span class="err-banner">No sources selected — pick at least one at left.</span>`;
    return;
  }
  $("#status").textContent = "Searching…";
  $("#results").innerHTML = "";
  $("#outcomes").innerHTML = "";
  const activeDials = Object.fromEntries(Object.entries(dials).filter(([, v]) => v));
  const params = new URLSearchParams({
    q, limit: "25", sources: [...selected].join(","),
    rs: sliders.rs, fd: sliders.fd, rt: sliders.rt, mode,
  });
  if (Object.keys(activeDials).length) params.set("weights", JSON.stringify(activeDials));

  const t0 = performance.now();
  let r;
  try {
    r = await fetch("/api/federated?" + params.toString()).then((x) => x.json());
  } catch (err) {
    $("#status").innerHTML = `<span class="err-banner">Request failed: ${esc(err.message)}</span>`;
    return;
  }
  const ms = Math.round(performance.now() - t0);
  if (r.error) {
    $("#status").innerHTML = `<span class="err-banner">${esc(r.error)}</span>`;
    return;
  }
  const bangNote = (r.bangs_used || []).length ? ` · bang override: ${r.bangs_used.join(", ")}` : "";
  $("#status").textContent = `${r.results.length} result(s) in ${ms} ms for "${r.plain_query}"${bangNote}`;
  $("#computed").textContent = Object.entries(r.weights || {})
    .map(([k, v]) => `${k.padEnd(24, " ")} ${v.toFixed(4)}`).join("\n");
  const plan = r.mode_plan;
  $("#modeNotes").innerHTML = plan
    ? `<strong>${esc(r.mode)}</strong> — ${(plan.notes || []).map(esc).join(" ")}`
      + (plan.added_sources && plan.added_sources.length
          ? `<br>added sources: ${plan.added_sources.map(esc).join(", ")}` : "")
    : "";
  renderOutcomes(r.outcomes);
  $("#results").innerHTML = r.results.length
    ? r.results.map(renderCard).join("")
    : `<div class="empty">No results. Try more sources, or a broader term.</div>`;
  bindSaves();
}

function bindSaves() {
  document.querySelectorAll("button[data-save]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const [provider_id, source_id] = btn.dataset.save.split("|");
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        const res = await fetch("/api/save", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider_id, source_id }),
        }).then((r) => r.json());
        btn.textContent = res.saved ? "Saved ✓" : (res.error || "Failed");
      } catch (err) {
        btn.textContent = "Failed";
      }
    });
  });
}

$("#form").addEventListener("submit", search);
$("#savePrefs").addEventListener("click", async () => {
  await fetch("/api/prefs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prefs: { selected: [...selected], sliders, dials, mode } }),
  });
  $("#savePrefs").textContent = "Saved ✓";
  setTimeout(() => ($("#savePrefs").textContent = "Save settings"), 1500);
});
$("#resetPrefs").addEventListener("click", () => {
  selected = new Set(NAV.defaults);
  Object.assign(sliders, { rs: 0.5, fd: 0.5, rt: 0.5 });
  dials = {}; mode = "standard";
  renderNav(); renderModes(); renderSliders(); renderDials();
});

init();
</script>
</body>
</html>
"""
