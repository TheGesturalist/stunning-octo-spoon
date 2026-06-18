"""Verify connectors honor the `limit` argument even when upstream APIs
ignore page-size hints (Readwise Reader v3 returns 100 regardless of
the `page_size` query param)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.internet_archive import InternetArchiveConnector
from connectors.raindrop_io import RaindropIOConnector
from connectors.reader_io import ReaderIOConnector
from connectors.tumblr import TumblrConnector


def _stub_results(n: int) -> list[dict]:
    return [{"id": i, "title": f"item-{i}"} for i in range(n)]


class ReaderIOLimitTest(unittest.TestCase):
    def test_limit_caps_when_server_returns_more(self) -> None:
        connector = ReaderIOConnector(api_token="t")
        # Simulate Readwise Reader v3 ignoring page_size and returning 100.
        payload = {"results": _stub_results(100)}
        with patch("connectors.reader_io.get_json", return_value=payload) as mocked:
            items = connector.fetch_items(limit=20)
        self.assertEqual(len(items), 20)
        # And the URL should not be sending a bogus page_size that the API ignores.
        url = mocked.call_args.args[0]
        self.assertNotIn("page_size", url)

    def test_limit_passthrough_when_server_returns_fewer(self) -> None:
        connector = ReaderIOConnector(api_token="t")
        payload = {"results": _stub_results(5)}
        with patch("connectors.reader_io.get_json", return_value=payload):
            items = connector.fetch_items(limit=20)
        self.assertEqual(len(items), 5)


class RaindropIOLimitTest(unittest.TestCase):
    def test_limit_caps_when_server_returns_more(self) -> None:
        connector = RaindropIOConnector(api_token="t")
        payload = {"items": [{"_id": i} for i in range(50)]}
        with patch("connectors.raindrop_io.get_json", return_value=payload):
            items = connector.fetch_items(limit=10)
        self.assertEqual(len(items), 10)


class TumblrLimitTest(unittest.TestCase):
    def test_limit_caps_when_server_returns_more(self) -> None:
        connector = TumblrConnector(blog_hostname="b", api_key="k")
        payload = {"response": {"posts": [{"id": i} for i in range(20)]}}
        with patch("connectors.tumblr.get_json", return_value=payload):
            items = connector.fetch_items(limit=5)
        self.assertEqual(len(items), 5)


class InternetArchiveLimitTest(unittest.TestCase):
    def test_limit_caps_when_server_returns_more(self) -> None:
        connector = InternetArchiveConnector(query="mediatype:texts")
        payload = {"response": {"docs": [{"identifier": f"id-{i}"} for i in range(100)]}}
        with patch("connectors.internet_archive.get_json", return_value=payload):
            items = connector.fetch_items(limit=15)
        self.assertEqual(len(items), 15)


if __name__ == "__main__":
    unittest.main()
