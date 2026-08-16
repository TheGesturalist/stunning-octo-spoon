"""Are.na connector (public channels and blocks).

Reads content from Are.na's public v2 REST API (https://api.are.na/v2/).
Public channels and their blocks are readable without authentication; a personal
access token (dev.are.na) is optional and only needed to reach private content,
which this connector does not target by default.

Two targeting modes (mirrors how other connectors take constructor params):

    ArenaConnector(channel="my-channel")            # one or more channel slugs
    ArenaConnector(channel="chan-a,chan-b")         # comma-separated slugs
    ArenaConnector(user="my-user-slug")             # all of a user's PUBLIC channels

Each Are.na *block* becomes one NormalizedItem. Blocks come in several classes
(Text, Link, Image, Media, Attachment, Channel); each is normalized to a coarse
``arena_*`` content_type so downstream search/filters can distinguish them.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .base import BaseConnector
from .http_helpers import get_json
from .schema import NormalizedItem


class ArenaConnector(BaseConnector):
    name = "arena"

    BASE = "https://api.are.na/v2"
    # Are.na caps `per` at 100.
    PAGE_SIZE = 100
    # Are.na sits behind Cloudflare, which 403s the default urllib User-Agent.
    USER_AGENT = "stunning-octo-spoon/1.0 (+local research indexer)"

    # Are.na block class -> our coarse content_type.
    _CLASS_TO_TYPE = {
        "Text": "arena_text",
        "Link": "arena_link",
        "Image": "arena_image",
        "Media": "arena_media",
        "Attachment": "arena_attachment",
        "Channel": "arena_channel",
    }

    def __init__(
        self,
        channel: str | None = None,
        user: str | None = None,
        token: str | None = None,
    ) -> None:
        # Accept comma-separated channel slugs; strip blanks.
        self.channels = [c.strip() for c in (channel or "").split(",") if c.strip()]
        self.user = (user or "").strip() or None
        self.token = token
        if not self.channels and not self.user:
            raise ValueError(
                "ArenaConnector needs either channel=<slug[,slug...]> or user=<slug>."
            )

    @property
    def _headers(self) -> dict[str, str]:
        # A real User-Agent is required (Cloudflare blocks the default urllib UA).
        # Public reads work unauthenticated; a token only widens access.
        headers = {"User-Agent": self.USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # -- fetching -----------------------------------------------------------

    def _resolve_channel_slugs(self) -> list[str]:
        """Explicit channels win; otherwise enumerate the user's public channels."""
        if self.channels:
            return self.channels
        slugs: list[str] = []
        page = 1
        while True:
            url = (
                f"{self.BASE}/users/{quote(str(self.user))}/channels"
                f"?page={page}&per={self.PAGE_SIZE}"
            )
            try:
                payload = get_json(url, headers=self._headers)
            except Exception as exc:  # noqa: BLE001 - one bad page shouldn't abort all
                print(f"  [arena] failed listing channels for {self.user}: {exc}")
                break
            channels = payload.get("channels") or []
            if not channels:
                break
            for ch in channels:
                # Unauthenticated calls only return public channels, but guard anyway.
                if ch.get("status") == "private":
                    continue
                slug = ch.get("slug")
                if slug:
                    slugs.append(slug)
            if len(channels) < self.PAGE_SIZE:
                break
            page += 1
        return slugs

    def _fetch_channel_contents(self, slug: str, cap: int) -> list[dict[str, Any]]:
        """Page through one channel's blocks, up to `cap` blocks."""
        out: list[dict[str, Any]] = []
        page = 1
        while len(out) < cap:
            url = (
                f"{self.BASE}/channels/{quote(slug)}/contents"
                f"?page={page}&per={self.PAGE_SIZE}"
            )
            try:
                payload = get_json(url, headers=self._headers)
            except Exception as exc:  # noqa: BLE001 - skip a missing/private channel
                print(f"  [arena] skip channel '{slug}': {exc}")
                break
            contents = payload.get("contents") or []
            if not contents:
                break
            out.extend(contents)
            if len(contents) < self.PAGE_SIZE:
                break
            page += 1
        return out[:cap]

    def fetch_items(self, cursor: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        # Dedupe by block id across channels: the same Are.na block can be
        # connected to several channels. Keep one copy, but remember every
        # channel it appears in (preserved as tags at normalize time).
        by_id: "dict[str, dict[str, Any]]" = {}
        for slug in self._resolve_channel_slugs():
            if len(by_id) >= limit:
                break
            for block in self._fetch_channel_contents(slug, limit):
                bid = str(block.get("id"))
                existing = by_id.get(bid)
                if existing is None:
                    if len(by_id) >= limit:
                        break
                    block["_arena_channels"] = [slug]
                    block["_arena_channel"] = slug  # primary (first-seen) channel
                    by_id[bid] = block
                elif slug not in existing["_arena_channels"]:
                    existing["_arena_channels"].append(slug)
        return list(by_id.values())

    # -- normalization ------------------------------------------------------

    def fetch_fulltext(self, item: dict[str, Any]) -> str | None:
        klass = item.get("class")
        parts: list[str] = []
        if klass == "Text":
            parts.append(str(item.get("content") or ""))
        # Link / Media / Attachment: title + description + source URL are the text.
        for key in ("title", "description"):
            val = item.get(key)
            if val:
                parts.append(str(val))
        source = item.get("source") or {}
        if isinstance(source, dict) and source.get("url"):
            parts.append(str(source["url"]))
        text = "\n".join(p for p in parts if p).strip()
        return text or None

    def _block_url(self, item: dict[str, Any]) -> str | None:
        source = item.get("source") or {}
        if isinstance(source, dict) and source.get("url"):
            return str(source["url"])
        block_id = item.get("id")
        return f"https://www.are.na/block/{block_id}" if block_id else None

    def normalize_item(self, item: dict[str, Any]) -> NormalizedItem:
        klass = item.get("class")
        user = item.get("user") or {}
        image = item.get("image")
        # Every channel this block appears in (dedupe preserves them all).
        channels = item.get("_arena_channels")
        if not channels:
            primary = item.get("_arena_channel")
            channels = [primary] if primary else []
        channels = list(dict.fromkeys(c for c in channels if c))  # unique, ordered
        channel_slug = channels[0] if channels else None
        image_url = None
        if isinstance(image, dict):
            original = image.get("original")
            if isinstance(original, dict):
                image_url = original.get("url")
        return NormalizedItem(
            connector=self.name,
            source_id=str(item.get("id")),
            source_url=self._block_url(item),
            title=item.get("title") or item.get("generated_title"),
            author=user.get("full_name") or user.get("username"),
            summary=item.get("description"),
            fulltext=self.fetch_fulltext(item),
            content_type=self._CLASS_TO_TYPE.get(klass or "", "arena_block"),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            tags=channels,
            metadata={
                "channel": channel_slug,
                "channels": channels,
                "block_class": klass,
                "image_url": image_url,
            },
        )

    def sync_cursor(self, last_item: dict[str, Any] | None) -> str | None:
        if not last_item:
            return None
        updated = last_item.get("updated_at")
        return quote(str(updated)) if updated else None
