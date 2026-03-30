import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from connectors.enrichment import enrich_item
from connectors.schema import NormalizedItem
from connectors.storage import (
    generate_weekly_digest,
    init_sqlite,
    mark_digest_items_processed,
    monitor_link_health,
    upsert_item_with_enrichment,
)


class EnrichmentPipelineTests(unittest.TestCase):
    def test_extracts_requested_enrichment_dimensions(self):
        item = NormalizedItem(
            connector="local_notes",
            source_id="note-42",
            title="Angela Davis Collage Manifesto",
            summary="An urgent essay about archive memory and resistance.",
            fulltext="""This manifesto argues for collage as defiant method.
            It cites UCLA archives and digital platforms.""",
            content_type="essay",
            tags=["scan", "zine"],
        )

        result = enrich_item(item)

        facet_types = {facet.facet_type for facet in result.facets}
        self.assertIn("named_entity", facet_types)
        self.assertIn("theme", facet_types)
        self.assertIn("medium_style", facet_types)
        self.assertIn("mood_tone", facet_types)

        edge_types = {edge.edge_type for edge in result.edges}
        self.assertIn("mentions_entity", edge_types)
        self.assertIn("has_theme", edge_types)
        self.assertIn("has_medium_style", edge_types)
        self.assertIn("has_mood_tone", edge_types)

    def test_persists_facets_and_edges_as_searchable_tables(self):
        item = NormalizedItem(
            connector="reader_io",
            source_id="item-1",
            title="Archive Essay",
            summary="Nostalgia and memory in scanned ephemera",
            fulltext="A playful collage with a critical tone.",
            content_type="essay",
            tags=["scan"],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/items.db"
            init_sqlite(db_path)
            upsert_item_with_enrichment(db_path, item)

            with sqlite3.connect(db_path) as conn:
                facets = conn.execute(
                    """
                    SELECT facet_type, facet_value
                    FROM enrichment_facets
                    WHERE connector = ? AND source_id = ?
                    """,
                    (item.connector, item.source_id),
                ).fetchall()
                edges = conn.execute(
                    """
                    SELECT edge_type, target_node
                    FROM enrichment_graph_edges
                    WHERE connector = ? AND source_id = ?
                    """,
                    (item.connector, item.source_id),
                ).fetchall()

        self.assertTrue(facets)
        self.assertTrue(edges)
        self.assertTrue(any(row[0] == "theme" for row in facets))
        self.assertTrue(any(row[0] == "mood_tone" for row in facets))
        self.assertTrue(any(row[0] == "has_theme" for row in edges))
        self.assertTrue(any(row[0] == "has_medium_style" for row in edges))

    def test_monitors_link_health_and_writes_archival_fallbacks(self):
        item = NormalizedItem(
            connector="reader_io",
            source_id="broken-link-item",
            source_url="http://127.0.0.1:1/not-live",
            title="Broken link",
            summary="Will fail health checks",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/items.db"
            init_sqlite(db_path)
            upsert_item_with_enrichment(db_path, item)
            records = monitor_link_health(db_path, timeout_seconds=0.1)

            self.assertTrue(records)
            target = next(record for record in records if record.source_id == item.source_id)
            self.assertFalse(target.is_alive)
            self.assertIn("web.archive.org", target.archival_fallback_url or "")

            with sqlite3.connect(db_path) as conn:
                saved = conn.execute(
                    """
                    SELECT is_alive, archival_fallback_url
                    FROM link_health_checks
                    WHERE connector = ? AND source_id = ?
                    ORDER BY checked_at DESC
                    LIMIT 1
                    """,
                    (item.connector, item.source_id),
                ).fetchone()
                provenance = conn.execute(
                    """
                    SELECT event_type
                    FROM provenance_events
                    WHERE connector = ? AND source_id = ?
                    """,
                    (item.connector, item.source_id),
                ).fetchall()
            self.assertEqual(saved[0], 0)
            self.assertIn("web.archive.org", saved[1])
            self.assertIn(("link_health_checked",), provenance)

    def test_generates_weekly_digest_from_unprocessed_saved_content(self):
        now = datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc)
        items = [
            NormalizedItem(
                connector="reader_io",
                source_id="digest-1",
                title="Archive Memory",
                summary="Saved article",
                fetched_at="2026-03-28T10:00:00Z",
            ),
            NormalizedItem(
                connector="raindrop_io",
                source_id="digest-2",
                title="Collage Notes",
                summary="Saved bookmark",
                fetched_at="2026-03-26T10:00:00Z",
            ),
            NormalizedItem(
                connector="reader_io",
                source_id="already-processed",
                title="Done",
                summary="Already in digest",
                fetched_at="2026-03-29T10:00:00Z",
                metadata={"processed_for_digest": True},
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/items.db"
            init_sqlite(db_path)
            for item in items:
                upsert_item_with_enrichment(db_path, item)

            digest = generate_weekly_digest(db_path, now=now)
            self.assertEqual(digest.total_items, 2)
            digest_ids = {source_id for _, source_id in digest.item_ids}
            self.assertEqual(digest_ids, {"digest-1", "digest-2"})
            self.assertTrue(digest.top_connectors)

            mark_digest_items_processed(db_path, digest)
            second_digest = generate_weekly_digest(db_path, now=now)
            self.assertEqual(second_digest.total_items, 0)


if __name__ == "__main__":
    unittest.main()
