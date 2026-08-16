"""Tests for the web-search cache and explicit promotion into the library.

The load-bearing guarantee here: live web results never reach `normalized_items`
on their own. Only `save_web_item_to_library` moves an item across that line.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.schema import NormalizedItem
from connectors.storage import (
    cache_web_results,
    get_cached_web_item,
    init_sqlite,
    load_search_preferences,
    normalize_query_key,
    read_cached_web_results,
    save_search_preferences,
    save_web_item_to_library,
)


def _item(source_id: str = "1", title: str = "A page") -> NormalizedItem:
    return NormalizedItem(
        connector="wikimedia:en.wikipedia.org/wp",
        source_id=source_id,
        source_url=f"https://en.wikipedia.org/wiki/{source_id}",
        title=title,
        summary="summary text",
        fulltext="body text about cartography",
        content_type="wiki_article",
    )


class WebCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._dir.name) / "test.db")
        init_sqlite(self.db)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _library_count(self) -> int:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]


class QueryKeyTest(unittest.TestCase):
    def test_key_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual("a b", normalize_query_key("  A   B "))
        self.assertEqual(normalize_query_key("Maps"), normalize_query_key("maps"))


class CacheRoundTripTest(WebCacheTestCase):
    def test_store_then_read(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1"), _item("2")]
        )
        cached = read_cached_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", max_age_seconds=600
        )
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual({"1", "2"}, {i.source_id for i in cached})
        self.assertEqual("A page", cached[0].title)

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(
            read_cached_web_results(
                self.db, provider_id="wikimedia:wp", query="absent", max_age_seconds=600
            )
        )

    def test_zero_max_age_always_misses(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1")]
        )
        self.assertIsNone(
            read_cached_web_results(
                self.db, provider_id="wikimedia:wp", query="maps", max_age_seconds=0
            )
        )

    def test_stale_entries_are_rejected(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1")]
        )
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE web_cache_items SET cached_at = '2001-01-01T00:00:00Z'")
        self.assertIsNone(
            read_cached_web_results(
                self.db, provider_id="wikimedia:wp", query="maps", max_age_seconds=60
            )
        )

    def test_requery_replaces_the_previous_batch(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1"), _item("2")]
        )
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("3")]
        )
        cached = read_cached_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", max_age_seconds=600
        )
        assert cached is not None
        self.assertEqual(["3"], [i.source_id for i in cached])

    def test_providers_do_not_share_a_cache_slot(self) -> None:
        cache_web_results(self.db, provider_id="p:a", query="maps", items=[_item("1")])
        cache_web_results(self.db, provider_id="p:b", query="maps", items=[_item("2")])
        a = read_cached_web_results(
            self.db, provider_id="p:a", query="maps", max_age_seconds=600
        )
        assert a is not None
        self.assertEqual(["1"], [i.source_id for i in a])


class CorpusIsolationTest(WebCacheTestCase):
    def test_caching_never_touches_the_library(self) -> None:
        self.assertEqual(0, self._library_count())
        cache_web_results(
            self.db,
            provider_id="wikimedia:wp",
            query="maps",
            items=[_item(str(i)) for i in range(25)],
        )
        self.assertEqual(
            0, self._library_count(), "web cache must not leak into normalized_items"
        )

    def test_save_promotes_exactly_one_item(self) -> None:
        cache_web_results(
            self.db,
            provider_id="wikimedia:wp",
            query="maps",
            items=[_item("1"), _item("2"), _item("3")],
        )
        saved = save_web_item_to_library(
            self.db, provider_id="wikimedia:wp", source_id="2"
        )
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual("2", saved.source_id)
        self.assertEqual(1, self._library_count())

    def test_saved_item_keeps_its_connector_identity(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1")]
        )
        save_web_item_to_library(self.db, provider_id="wikimedia:wp", source_id="1")
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT connector, title FROM normalized_items"
            ).fetchone()
        self.assertEqual("wikimedia:en.wikipedia.org/wp", row[0])
        self.assertEqual("A page", row[1])

    def test_saving_an_uncached_item_is_a_no_op(self) -> None:
        result = save_web_item_to_library(
            self.db, provider_id="wikimedia:wp", source_id="missing"
        )
        self.assertIsNone(result)
        self.assertEqual(0, self._library_count())

    def test_save_records_provenance(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("1")]
        )
        save_web_item_to_library(self.db, provider_id="wikimedia:wp", source_id="1")
        with sqlite3.connect(self.db) as conn:
            events = conn.execute(
                "SELECT event_type FROM provenance_events WHERE event_type = 'saved_from_web'"
            ).fetchall()
        self.assertEqual(1, len(events))

    def test_get_cached_item_is_query_independent(self) -> None:
        cache_web_results(
            self.db, provider_id="wikimedia:wp", query="maps", items=[_item("9")]
        )
        found = get_cached_web_item(self.db, provider_id="wikimedia:wp", source_id="9")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual("9", found.source_id)


class PreferencesTest(WebCacheTestCase):
    def test_round_trip(self) -> None:
        payload = {"selected": ["library:arena"], "sliders": {"rs": 0.25}}
        save_search_preferences(self.db, pref_key="web_ui", payload=payload)
        self.assertEqual(payload, load_search_preferences(self.db, pref_key="web_ui"))

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(load_search_preferences(self.db, pref_key="absent"))

    def test_save_overwrites(self) -> None:
        save_search_preferences(self.db, pref_key="web_ui", payload={"a": 1})
        save_search_preferences(self.db, pref_key="web_ui", payload={"b": 2})
        self.assertEqual({"b": 2}, load_search_preferences(self.db, pref_key="web_ui"))


class LegacyDatabaseTest(unittest.TestCase):
    def test_cache_tables_are_added_to_a_pre_existing_db(self) -> None:
        """An existing spoon.db predates these tables; they must self-install."""

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "legacy.db")
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE normalized_items (connector TEXT, source_id TEXT)"
                )
            cache_web_results(db, provider_id="p", query="q", items=[_item("1")])
            cached = read_cached_web_results(
                db, provider_id="p", query="q", max_age_seconds=600
            )
            assert cached is not None
            self.assertEqual(1, len(cached))


if __name__ == "__main__":
    unittest.main()
