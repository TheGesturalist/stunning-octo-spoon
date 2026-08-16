"""Wikimedia / cross-wiki search — the project's origin story.

One parameterized provider over the MediaWiki Action API (`list=search`), plus a
preset table keyed by the owner's bang vocabulary from the Research Console.
Reads need no auth on any of the four target wikis.

Coverage (see HANDOFF.md §1):
  sites        wp · meta · mw · wix
  namespaces   wpp · wpmw · wpc · wph · wpi · wpu · wpt
  community    wphd · wpfaq · wps · wptm · vpg · vpp · vpt · vpo
  operator     wpl
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from connectors.schema import NormalizedItem

from ._http import fetch_json, strip_html
from .base import OPEN_RIGHTS, SearchProvider

# Standard MediaWiki namespace ids.
NS_MAIN = 0
NS_USER = 2
NS_PROJECT = 4
NS_FILE = 6
NS_MEDIAWIKI = 8
NS_TEMPLATE = 10
NS_HELP = 12
NS_CATEGORY = 14

# Namespace id -> our content_type (HANDOFF open question #2).
_NS_CONTENT_TYPE = {
    NS_MAIN: "wiki_article",
    NS_USER: "wiki_user_page",
    NS_PROJECT: "wiki_project_page",
    NS_FILE: "wiki_file",
    NS_MEDIAWIKI: "wiki_mediawiki_page",
    NS_TEMPLATE: "wiki_template",
    NS_HELP: "wiki_help_page",
    NS_CATEGORY: "wiki_category",
}

# Most wikis expose the API at /w/api.php; third-party wikis often use /api.php.
_API_PATHS = {"wikiindex.org": "/api.php"}

_CC_BY_SA = dict(OPEN_RIGHTS, license="CC BY-SA 4.0")


class WikimediaProvider(SearchProvider):
    """Search one wiki, optionally constrained to a namespace and title prefix."""

    def __init__(
        self,
        *,
        bang: str,
        label: str,
        site: str,
        group: str = "Wikimedia",
        namespace: int | None = None,
        title_prefix: str | None = None,
        search_operator: str | None = None,
        title_regex: str | None = None,
        hint: str = "",
        intro_only: bool = True,
    ) -> None:
        self.bang = bang
        self.label = label
        self.site = site
        self.group = group
        self.namespace = namespace
        self.title_prefix = title_prefix
        self.search_operator = search_operator
        self.title_regex = re.compile(title_regex, re.I) if title_regex else None
        self.hint = hint or f"{site}"
        self.intro_only = intro_only
        self.provider_id = f"wikimedia:{bang}"
        self.library_connector = f"wikimedia:{site}/{bang}"
        self.default_rights = _CC_BY_SA

    # -- API plumbing -------------------------------------------------------

    @property
    def api_url(self) -> str:
        return f"https://{self.site}{_API_PATHS.get(self.site, '/w/api.php')}"

    def _search_expression(self, query: str) -> str:
        """Compose the CirrusSearch expression for this preset."""

        parts = [query.strip()]
        if self.search_operator:
            parts.append(self.search_operator)
        if self.title_prefix:
            # `prefix:` restricts to pages whose title starts with the value.
            parts.append(f"prefix:{self.title_prefix}")
        return " ".join(p for p in parts if p)

    def _title_allowed(self, title: str) -> bool:
        """Enforce the preset's title constraint on the client side.

        CirrusSearch indexes *redirect* titles as well as real ones, so a
        `prefix:`/`intitle:` constraint can admit a page whose own title does not
        match (an article with a "List of …" redirect, say). The constraint the
        owner asked for is about the title they will actually see, so verify it
        here rather than trusting the operator.
        """

        if self.title_regex and not self.title_regex.search(title):
            return False
        if self.title_prefix and not title.lower().startswith(self.title_prefix.lower()):
            return False
        return True

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        if not query.strip():
            return []
        filtered = bool(self.title_regex or self.title_prefix)
        # Over-fetch when post-filtering so a constrained preset still fills the page.
        fetch_limit = min(50, limit * 4) if filtered else limit
        payload = fetch_json(
            self.api_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": self._search_expression(query),
                "srnamespace": self.namespace if self.namespace is not None else NS_MAIN,
                "srlimit": max(1, fetch_limit),
                "srprop": "snippet|timestamp|wordcount|size",
                "format": "json",
                "formatversion": "2",
            },
        )
        hits = ((payload or {}).get("query") or {}).get("search") or []
        if filtered:
            hits = [h for h in hits if self._title_allowed(str(h.get("title") or ""))]
        hits = hits[:limit]
        if not hits:
            return []

        extracts = self._fetch_extracts([h.get("pageid") for h in hits])
        return [self._normalize(hit, extracts) for hit in hits]

    def _fetch_extracts(self, page_ids: list[Any]) -> dict[int, str]:
        """Batch-fetch plaintext extracts so results carry real indexable text."""

        ids = [str(pid) for pid in page_ids if pid]
        if not ids:
            return {}
        params: dict[str, Any] = {
            "action": "query",
            "prop": "extracts",
            "pageids": "|".join(ids[:20]),
            "explaintext": 1,
            "exlimit": "max",
            "format": "json",
            "formatversion": "2",
        }
        if self.intro_only:
            params["exintro"] = 1
        try:
            payload = fetch_json(self.api_url, params=params)
        except Exception:  # noqa: BLE001 — extracts are a bonus, never fatal
            return {}
        pages = ((payload or {}).get("query") or {}).get("pages") or []
        out: dict[int, str] = {}
        for page in pages:
            pid = page.get("pageid")
            text = (page.get("extract") or "").strip()
            if pid and text:
                out[int(pid)] = text
        return out

    # -- normalization ------------------------------------------------------

    def _page_url(self, title: str) -> str:
        slug = urllib.parse.quote(title.replace(" ", "_"), safe=":/()!'*,-.~")
        return f"https://{self.site}/wiki/{slug}"

    def _normalize(self, hit: dict[str, Any], extracts: dict[int, str]) -> NormalizedItem:
        title = str(hit.get("title") or "")
        page_id = hit.get("pageid")
        snippet = strip_html(hit.get("snippet"))
        extract = extracts.get(int(page_id)) if page_id else None
        namespace = hit.get("ns", self.namespace if self.namespace is not None else NS_MAIN)
        fulltext = "\n\n".join(part for part in (title, extract or snippet) if part)
        return NormalizedItem(
            connector=self.library_connector,
            source_id=f"{self.site}:{page_id}",
            source_url=self._page_url(title),
            title=title,
            author=None,
            summary=snippet or (extract[:300] if extract else None),
            fulltext=fulltext or None,
            content_type=_NS_CONTENT_TYPE.get(int(namespace), "wiki_page"),
            language=self.site.split(".")[0] if self.site.endswith("wikipedia.org") else None,
            updated_at=hit.get("timestamp"),
            tags=[t for t in ("wikimedia", self.bang) if t],
            metadata={
                "site": self.site,
                "bang": self.bang,
                "namespace": namespace,
                "wordcount": hit.get("wordcount"),
                "size": hit.get("size"),
                "preset": self.label,
            },
            rights=dict(self.default_rights),
        )


# ---------------------------------------------------------------------------
# Preset table — the owner's bang vocabulary, verbatim from the Research Console
# ---------------------------------------------------------------------------

_EN_WP = "en.wikipedia.org"

_PRESET_SPECS: tuple[dict[str, Any], ...] = (
    # -- sibling projects --
    {"bang": "wp", "label": "English Wikipedia", "site": _EN_WP, "namespace": NS_MAIN,
     "group": "Wikimedia · General", "hint": "Mainspace articles"},
    {"bang": "meta", "label": "Meta-Wiki", "site": "meta.wikimedia.org", "namespace": NS_MAIN,
     "group": "Wikimedia · General", "hint": "Wikimedia movement coordination"},
    {"bang": "mw", "label": "MediaWiki", "site": "www.mediawiki.org", "namespace": NS_MAIN,
     "group": "Wikimedia · General", "hint": "MediaWiki software docs"},
    {"bang": "wix", "label": "WikiIndex", "site": "wikiindex.org", "namespace": NS_MAIN,
     "group": "Wikimedia · General", "hint": "Index of wikis"},

    # -- en.wp namespaces --
    {"bang": "wpp", "label": "Project Pages", "site": _EN_WP, "namespace": NS_PROJECT,
     "group": "Wikimedia · Namespaces", "hint": "Wikipedia: namespace"},
    {"bang": "wpmw", "label": "MediaWiki Pages", "site": _EN_WP, "namespace": NS_MEDIAWIKI,
     "group": "Wikimedia · Namespaces", "hint": "MediaWiki: namespace"},
    {"bang": "wpc", "label": "Category Pages", "site": _EN_WP, "namespace": NS_CATEGORY,
     "group": "Wikimedia · Namespaces", "hint": "Category: namespace"},
    {"bang": "wph", "label": "Help Pages", "site": _EN_WP, "namespace": NS_HELP,
     "group": "Wikimedia · Namespaces", "hint": "Help: namespace"},
    {"bang": "wpi", "label": "Image Namespace", "site": _EN_WP, "namespace": NS_FILE,
     "group": "Wikimedia · Namespaces", "hint": "File:/Image: namespace"},
    {"bang": "wpu", "label": "User Pages", "site": _EN_WP, "namespace": NS_USER,
     "group": "Wikimedia · Namespaces", "hint": "User: namespace"},
    {"bang": "wpt", "label": "Template Pages", "site": _EN_WP, "namespace": NS_TEMPLATE,
     "group": "Wikimedia · Namespaces", "hint": "Template: namespace"},

    # -- community / process pages (all inside the Wikipedia: namespace) --
    {"bang": "wphd", "label": "Help Desk", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Help desk", "group": "Wikimedia · Community"},
    {"bang": "wpfaq", "label": "FAQ", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:FAQ", "group": "Wikimedia · Community"},
    {"bang": "wps", "label": "Signpost", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Wikipedia Signpost", "group": "Wikimedia · Community"},
    {"bang": "wptm", "label": "Template Messages", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Template messages", "group": "Wikimedia · Community"},
    {"bang": "vpg", "label": "Village Pump (General)", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Village pump", "group": "Wikimedia · Community"},
    {"bang": "vpp", "label": "Village Pump (Policy)", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Village pump (policy)", "group": "Wikimedia · Community"},
    {"bang": "vpt", "label": "Village Pump (Technical)", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Village pump (technical)", "group": "Wikimedia · Community"},
    {"bang": "vpo", "label": "Village Pump (Other)", "site": _EN_WP, "namespace": NS_PROJECT,
     "title_prefix": "Wikipedia:Village pump (miscellaneous)", "group": "Wikimedia · Community",
     "hint": "en.wp calls this 'miscellaneous'"},

    # -- operator --
    {"bang": "wpl", "label": 'List Articles (intitle:"List of")', "site": _EN_WP,
     "namespace": NS_MAIN, "search_operator": 'intitle:"List of"',
     "title_regex": r"^lists? of ", "group": "Wikimedia · Operators",
     "hint": "Titles beginning 'List of' / 'Lists of'"},
)


def build_wikimedia_providers() -> list[WikimediaProvider]:
    return [WikimediaProvider(**spec) for spec in _PRESET_SPECS]


#: bang -> provider, for `!wphd query` parsing in the search box.
WIKIMEDIA_BANGS: dict[str, WikimediaProvider] = {
    p.bang: p for p in build_wikimedia_providers() if p.bang
}
