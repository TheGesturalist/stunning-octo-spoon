import sqlite3
import tempfile
import unittest

from connectors.enrichment import enrich_item
from connectors.schema import NormalizedItem
from connectors.storage import init_sqlite, upsert_item_with_enrichment


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


if __name__ == "__main__":
    unittest.main()
