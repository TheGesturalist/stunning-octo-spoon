import unittest

from local_index_service import IndexedDocument, LocalIndexService


class LocalIndexServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LocalIndexService(
            {
                "notes": [
                    IndexedDocument(
                        doc_id="note-1",
                        title="On collage methods",
                        text="""Collage is a method of composing fragments.

In this note we discuss cut-up layouts and compositional rhythm.

Found references in archival magazines.""",
                        created_at="2024-02-11",
                    ),
                    IndexedDocument(
                        doc_id="note-2",
                        title="Archive reflections",
                        text="""The archive keeps layered memory traces.

Visual rhythm appears again in gallery walls.""",
                        created_at="2024-02-20",
                    ),
                ],
                "highlights": [
                    IndexedDocument(
                        doc_id="hl-1",
                        title="Reader highlight",
                        text="""A highlighted phrase about compositional rhythm and archival practice.""",
                        created_at="2024-03-02",
                    )
                ],
            }
        )

    def test_returns_snippet_with_highlights_and_locations(self):
        cards = self.service.query("compositional rhythm")
        self.assertTrue(cards)
        first = cards[0]

        self.assertIn("<mark>", first.snippet_highlight)
        self.assertTrue(first.term_matches)
        self.assertTrue(all(match.paragraph >= 1 for match in first.term_matches))

    def test_includes_semantic_neighbors_and_explanations(self):
        cards = self.service.query("archive rhythm", semantic_neighbors=2)
        self.assertTrue(cards)
        first = cards[0]

        self.assertLessEqual(len(first.semantic_neighbors), 2)
        self.assertTrue(first.match_explanations)
        self.assertTrue(any("Matched phrase in paragraph" in text for text in first.match_explanations))
        self.assertTrue(any("Similar to note" in text for text in first.match_explanations))

    def test_can_limit_to_specific_indexes(self):
        cards = self.service.query("highlighted phrase", indexes=["highlights"])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].doc_id, "hl-1")


if __name__ == "__main__":
    unittest.main()
