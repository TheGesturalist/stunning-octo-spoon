"""Live Are.na search — the whole platform, not just the owner's channels.

**Why this is channel-first.** Are.na's block-search endpoints are closed to us:
`/v2/search/blocks` returns 403 even with a valid personal access token, and the
combined `/v2/search` reports `authenticated: false` and always returns an empty
`blocks` array. Channel search, however, works.

So global block reach is reconstructed in two hops: search channels, then read
the top matching channels' contents through the public (unauthenticated) contents
endpoint that the ingest connector already relies on, and score the blocks
locally. That covers all of Are.na, not only `johnny-dicanero`.
"""

from __future__ import annotations

from typing import Any

import config
from connectors.schema import NormalizedItem
from local_index_service import find_term_matches, tokenize

from ._http import fetch_json
from .base import OPEN_RIGHTS, SearchProvider

_BASE = "https://api.are.na/v2"
# Are.na sits behind Cloudflare, which 403s a default urllib User-Agent; _http
# always sends a real one.
_ARENA_RIGHTS = dict(OPEN_RIGHTS, note="Are.na block rights vary per block")


def _auth_headers() -> dict[str, str]:
    token = config.arena_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


class ArenaChannelSearchProvider(SearchProvider):
    """Search Are.na channels platform-wide."""

    provider_id = "arena:channels"
    label = "Are.na channels"
    group = "Are.na"
    library_connector = "arena_web:channel"
    hint = "All of Are.na, not just yours"
    bang = "arc"
    default_rights = _ARENA_RIGHTS

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        return [
            self._normalize(ch) for ch in search_channels(query, limit=limit)
        ]

    def _normalize(self, channel: dict[str, Any]) -> NormalizedItem:
        slug = channel.get("slug")
        title = channel.get("title")
        description = channel.get("metadata", {}).get("description") if isinstance(
            channel.get("metadata"), dict
        ) else None
        owner = channel.get("user") or {}
        return NormalizedItem(
            connector=self.library_connector,
            source_id=str(channel.get("id")),
            source_url=f"https://www.are.na/{(owner.get('slug') or 'x')}/{slug}" if slug else None,
            title=title,
            author=owner.get("full_name") or owner.get("username"),
            summary=description,
            fulltext="\n\n".join(p for p in (title, description) if p) or None,
            content_type="arena_channel",
            created_at=channel.get("created_at"),
            updated_at=channel.get("updated_at"),
            tags=["arena", "channel"],
            metadata={
                "slug": slug,
                "length": channel.get("length"),
                "status": channel.get("status"),
                "owner": owner.get("username"),
            },
            rights=dict(self.default_rights),
        )


class ArenaBlockSearchProvider(SearchProvider):
    """Blocks from the Are.na channels that best match the query.

    Two hops (channel search → channel contents) because Are.na's block search
    endpoint is not available to API clients. See the module docstring.
    """

    provider_id = "arena:blocks"
    label = "Are.na blocks"
    group = "Are.na"
    library_connector = "arena_web:block"
    hint = "Blocks inside matching channels"
    bang = "arb"
    default_rights = _ARENA_RIGHTS

    #: How many matching channels to open. Each costs one extra request.
    CHANNELS_TO_OPEN = 3
    #: Blocks pulled per channel before local scoring.
    BLOCKS_PER_CHANNEL = 100

    _CLASS_TO_TYPE = {
        "Text": "arena_text",
        "Link": "arena_link",
        "Image": "arena_image",
        "Media": "arena_media",
        "Attachment": "arena_attachment",
        "Channel": "arena_channel",
    }

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        channels = search_channels(query, limit=self.CHANNELS_TO_OPEN)
        if not channels:
            return []
        terms = tokenize(query)
        scored: list[tuple[int, NormalizedItem]] = []
        seen: set[str] = set()

        for channel in channels[: self.CHANNELS_TO_OPEN]:
            slug = channel.get("slug")
            if not slug:
                continue
            try:
                payload = fetch_json(
                    f"{_BASE}/channels/{slug}/contents",
                    params={"page": 1, "per": self.BLOCKS_PER_CHANNEL},
                    headers=_auth_headers(),
                )
            except Exception:  # noqa: BLE001 — one dead channel must not kill the search
                continue
            for block in payload.get("contents") or []:
                block_id = str(block.get("id"))
                if block_id in seen:
                    continue
                seen.add(block_id)
                item = self._normalize(block, slug)
                haystack = " ".join(
                    p for p in (item.title, item.summary, item.fulltext) if p
                )
                hits = len(find_term_matches(haystack, terms)) if terms else 0
                # Keep blocks that match the query; if a channel matched but its
                # blocks do not, the channel result itself already covers it.
                if hits:
                    scored.append((hits, item))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _normalize(self, block: dict[str, Any], channel_slug: str) -> NormalizedItem:
        klass = block.get("class")
        user = block.get("user") or {}
        source = block.get("source") or {}
        source_url = (
            source.get("url")
            if isinstance(source, dict) and source.get("url")
            else f"https://www.are.na/block/{block.get('id')}"
        )
        parts: list[str] = []
        if klass == "Text":
            parts.append(str(block.get("content") or ""))
        for key in ("title", "description"):
            if block.get(key):
                parts.append(str(block[key]))
        image = block.get("image")
        image_url = None
        if isinstance(image, dict) and isinstance(image.get("original"), dict):
            image_url = image["original"].get("url")

        return NormalizedItem(
            connector=self.library_connector,
            source_id=str(block.get("id")),
            source_url=source_url,
            title=block.get("title") or block.get("generated_title"),
            author=user.get("full_name") or user.get("username"),
            summary=block.get("description"),
            fulltext="\n".join(p for p in parts if p).strip() or None,
            content_type=self._CLASS_TO_TYPE.get(klass or "", "arena_block"),
            created_at=block.get("created_at"),
            updated_at=block.get("updated_at"),
            tags=["arena", channel_slug],
            metadata={
                "channel": channel_slug,
                "block_class": klass,
                "image_url": image_url,
            },
            rights=dict(self.default_rights),
        )


def search_channels(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Platform-wide channel search.

    Prefers `/v2/search/channels` (works with a token); falls back to the
    combined `/v2/search`, which returns channels even unauthenticated.
    """

    if not query.strip():
        return []
    headers = _auth_headers()
    per = max(1, min(limit, 50))
    if headers:
        try:
            payload = fetch_json(
                f"{_BASE}/search/channels",
                params={"q": query, "per": per},
                headers=headers,
            )
            channels = payload.get("channels") or []
            if channels:
                return channels[:limit]
        except Exception:  # noqa: BLE001 — fall through to the public endpoint
            pass
    payload = fetch_json(f"{_BASE}/search", params={"q": query, "per": per})
    return (payload.get("channels") or [])[:limit]


def build_arena_providers() -> list[SearchProvider]:
    return [ArenaChannelSearchProvider(), ArenaBlockSearchProvider()]
