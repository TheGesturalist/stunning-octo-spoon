"""Tests for the Are.na connector: limit handling, multi-channel accumulation,
user-channel enumeration (public only), and block normalization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.arena import ArenaConnector


def _blocks(n: int, klass: str = "Text") -> list[dict]:
    return [{"id": i, "class": klass, "content": f"block-{i}"} for i in range(n)]


class ArenaConstructorTest(unittest.TestCase):
    def test_requires_a_target(self) -> None:
        with self.assertRaises(ValueError):
            ArenaConnector()

    def test_comma_separated_channels_parsed(self) -> None:
        c = ArenaConnector(channel="a, b ,c")
        self.assertEqual(c.channels, ["a", "b", "c"])


class ArenaLimitTest(unittest.TestCase):
    def test_limit_caps_when_channel_returns_more(self) -> None:
        connector = ArenaConnector(channel="my-channel")
        payload = {"contents": _blocks(100)}
        with patch("connectors.arena.get_json", return_value=payload):
            items = connector.fetch_items(limit=10)
        self.assertEqual(len(items), 10)

    def test_limit_passthrough_when_fewer(self) -> None:
        connector = ArenaConnector(channel="my-channel")
        payload = {"contents": _blocks(5)}
        with patch("connectors.arena.get_json", return_value=payload):
            items = connector.fetch_items(limit=20)
        self.assertEqual(len(items), 5)


class ArenaMultiChannelTest(unittest.TestCase):
    def test_dedupes_shared_blocks_across_channels(self) -> None:
        # Channel a has blocks 0,1,2; channel b has blocks 0,1,2,3.
        # Blocks 0,1,2 are shared and must collapse to one item each.
        connector = ArenaConnector(channel="a,b")
        side = [{"contents": _blocks(3)}, {"contents": _blocks(4)}]
        with patch("connectors.arena.get_json", side_effect=side):
            items = connector.fetch_items(limit=20)
        # 4 unique blocks, not 7.
        self.assertEqual(len(items), 4)
        self.assertEqual({b["id"] for b in items}, {0, 1, 2, 3})
        # A shared block records BOTH channels; the b-only block records just b.
        norm = {b["id"]: connector.normalize_item(b) for b in items}
        self.assertEqual(norm[0].tags, ["a", "b"])
        self.assertEqual(norm[3].tags, ["b"])


class ArenaUserEnumerationTest(unittest.TestCase):
    def test_enumerates_public_channels_only(self) -> None:
        connector = ArenaConnector(user="someone")
        channels_payload = {
            "channels": [
                {"slug": "public-one", "status": "public"},
                {"slug": "secret", "status": "private"},
            ]
        }
        contents_payload = {"contents": _blocks(2)}
        with patch(
            "connectors.arena.get_json",
            side_effect=[channels_payload, contents_payload],
        ) as mocked:
            items = connector.fetch_items(limit=20)
        # Only the public channel's contents were fetched (2 calls total, not 3).
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(b["_arena_channel"] == "public-one" for b in items))


class ArenaNormalizeTest(unittest.TestCase):
    def test_text_block(self) -> None:
        connector = ArenaConnector(channel="c")
        raw = {
            "id": 42,
            "class": "Text",
            "content": "a thought",
            "created_at": "2026-01-01T00:00:00.000Z",
            "user": {"username": "jt"},
            "_arena_channel": "c",
        }
        item = connector.normalize_item(raw)
        self.assertEqual(item.connector, "arena")
        self.assertEqual(item.source_id, "42")
        self.assertEqual(item.content_type, "arena_text")
        self.assertEqual(item.source_url, "https://www.are.na/block/42")
        self.assertIn("a thought", item.fulltext or "")
        self.assertEqual(item.tags, ["c"])
        self.assertEqual(item.metadata["channel"], "c")

    def test_link_block_uses_source_url(self) -> None:
        connector = ArenaConnector(channel="c")
        raw = {
            "id": 7,
            "class": "Link",
            "title": "Cool site",
            "description": "worth a look",
            "source": {"url": "https://example.com/page"},
            "_arena_channel": "c",
        }
        item = connector.normalize_item(raw)
        self.assertEqual(item.content_type, "arena_link")
        self.assertEqual(item.source_url, "https://example.com/page")
        self.assertEqual(item.title, "Cool site")
        self.assertIn("example.com", item.fulltext or "")


if __name__ == "__main__":
    unittest.main()
