import unittest

from connectors.academic_private import AcademicPrivateConnector, ProviderAccessPolicy


class AcademicPrivateConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = AcademicPrivateConnector(
            {
                "jstor": ProviderAccessPolicy(provider="jstor", allow_abstract=True, allow_fulltext=False),
                "proquest": ProviderAccessPolicy(provider="proquest", allow_abstract=True, allow_fulltext=True),
            }
        )

    def test_stores_citation_and_abstract_but_not_fulltext_when_not_allowed(self):
        normalized = self.connector.normalize_item(
            {
                "id": "doc-1",
                "provider": "jstor",
                "title": "Material Histories",
                "authors": ["Ada Lovelace"],
                "journal": "Journal of Archives",
                "year": 2021,
                "doi": "10.1000/test",
                "abstract": "A study of archival traces.",
                "fulltext": "Complete article text.",
                "fulltext_explicitly_allowed": True,
                "can_export": False,
            }
        )

        self.assertEqual(normalized.metadata["citation"]["title"], "Material Histories")
        self.assertEqual(normalized.summary, "A study of archival traces.")
        self.assertIsNone(normalized.fulltext)
        self.assertFalse(normalized.rights["can_export"])

    def test_stores_fulltext_only_with_explicit_allowance(self):
        base = {
            "id": "doc-2",
            "provider": "proquest",
            "title": "Design Archives",
            "authors": ["Grace Hopper"],
            "abstract": "Abstract text.",
            "fulltext": "Licensed full text.",
            "can_export": True,
        }

        without_explicit = self.connector.normalize_item({**base, "fulltext_explicitly_allowed": False})
        with_explicit = self.connector.normalize_item({**base, "fulltext_explicitly_allowed": True})

        self.assertIsNone(without_explicit.fulltext)
        self.assertEqual(with_explicit.fulltext, "Licensed full text.")


if __name__ == "__main__":
    unittest.main()
