import unittest

from local_index_service import IndexedDocument, LocalIndexService, export_result_cards


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
                        citation_metadata={"title": "On collage methods", "authors": ["Archivist A"]},
                    ),
                    IndexedDocument(
                        doc_id="note-2",
                        title="Archive reflections",
                        text="""The archive keeps layered memory traces.

Visual rhythm appears again in gallery walls.""",
                        created_at="2024-02-20",
                        rights={
                            "allow_abstract": True,
                            "allow_fulltext": False,
                            "can_export": False,
                            "export_policy": "citation_only",
                        },
                        abstract="The archive keeps layered memory traces.",
                        citation_metadata={"title": "Archive reflections", "authors": ["Archivist B"]},
                    ),
                ],
                "highlights": [
                    IndexedDocument(
                        doc_id="hl-1",
                        title="Reader highlight",
                        text="""A highlighted phrase about compositional rhythm and archival practice.""",
                        created_at="2024-03-02",
                        citation_metadata={"title": "Reader highlight", "authors": ["Reader"]},
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

    def test_can_limit_to_specific_indexes(self):
        cards = self.service.query("highlighted phrase", indexes=["highlights"])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].doc_id, "hl-1")

    def test_uses_abstract_when_fulltext_rights_are_restricted(self):
        cards = self.service.query("layered memory")
        restricted = next(card for card in cards if card.doc_id == "note-2")
        self.assertIn("<mark>layered</mark>", restricted.snippet_highlight.lower())
        self.assertNotIn("gallery walls", restricted.snippet_highlight.lower())

    def test_export_filters_documents_without_export_rights(self):
        cards = self.service.query("archive rhythm")
        exported = export_result_cards(cards)
        exported_ids = {row["doc_id"] for row in exported}
        self.assertNotIn("note-2", exported_ids)
        self.assertIn("note-1", exported_ids)


if __name__ == "__main__":
    unittest.main()
