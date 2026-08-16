"""Tests for the Wikimedia provider: bang presets, query composition, the
client-side title guard, and normalization. All HTTP is mocked."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.wikimedia import (
    NS_MAIN,
    NS_PROJECT,
    WIKIMEDIA_BANGS,
    WikimediaProvider,
    build_wikimedia_providers,
)


def _search_payload(titles: list[str], ns: int = NS_MAIN) -> dict:
    return {
        "query": {
            "search": [
                {
                    "title": title,
                    "pageid": 1000 + i,
                    "ns": ns,
                    "snippet": f'a <span class="searchmatch">hit</span> in {title}',
                    "timestamp": "2026-01-02T03:04:05Z",
                    "wordcount": 500,
                    "size": 4096,
                }
                for i, title in enumerate(titles)
            ]
        }
    }


class PresetTableTest(unittest.TestCase):
    def test_every_research_console_bang_is_present(self) -> None:
        expected = {
            "wp", "meta", "mw", "wix",
            "wpp", "wpmw", "wpc", "wph", "wpi", "wpu", "wpt",
            "wphd", "wpfaq", "wps", "wptm", "vpg", "vpp", "vpt", "vpo",
            "wpl",
        }
        self.assertEqual(expected, set(WIKIMEDIA_BANGS))

    def test_provider_ids_are_unique(self) -> None:
        providers = build_wikimedia_providers()
        ids = [p.provider_id for p in providers]
        self.assertEqual(len(ids), len(set(ids)))

    def test_wikiindex_uses_its_own_api_path(self) -> None:
        self.assertEqual(
            "https://wikiindex.org/api.php", WIKIMEDIA_BANGS["wix"].api_url
        )
        self.assertEqual(
            "https://en.wikipedia.org/w/api.php", WIKIMEDIA_BANGS["wp"].api_url
        )


class SearchExpressionTest(unittest.TestCase):
    def test_plain_preset_passes_query_through(self) -> None:
        self.assertEqual("maps", WIKIMEDIA_BANGS["wp"]._search_expression("maps"))

    def test_community_preset_adds_prefix_operator(self) -> None:
        self.assertEqual(
            "deletion prefix:Wikipedia:Help desk",
            WIKIMEDIA_BANGS["wphd"]._search_expression("deletion"),
        )

    def test_list_preset_adds_intitle_operator(self) -> None:
        self.assertIn('intitle:"List of"', WIKIMEDIA_BANGS["wpl"]._search_expression("rivers"))


class TitleGuardTest(unittest.TestCase):
    """CirrusSearch matches redirect titles, so the constraint is re-checked."""

    def test_list_preset_drops_non_list_titles(self) -> None:
        provider = WIKIMEDIA_BANGS["wpl"]
        payload = _search_payload(
            ["Geography and cartography", "List of maps", "Lists of atlases"]
        )
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            items = provider.search("cartography", limit=10)
        self.assertEqual(
            ["List of maps", "Lists of atlases"], [i.title for i in items]
        )

    def test_community_preset_drops_titles_outside_the_prefix(self) -> None:
        provider = WIKIMEDIA_BANGS["wphd"]
        payload = _search_payload(
            ["Wikipedia:Help desk/Archives/2020", "Wikipedia:Village pump"],
            ns=NS_PROJECT,
        )
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            items = provider.search("deletion", limit=10)
        self.assertEqual(["Wikipedia:Help desk/Archives/2020"], [i.title for i in items])

    def test_unconstrained_preset_keeps_everything(self) -> None:
        payload = _search_payload(["Anything", "At all"])
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            items = WIKIMEDIA_BANGS["wp"].search("x", limit=10)
        self.assertEqual(2, len(items))


class LimitTest(unittest.TestCase):
    def test_limit_is_honored_after_filtering(self) -> None:
        payload = _search_payload([f"List of {i}" for i in range(10)])
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            items = WIKIMEDIA_BANGS["wpl"].search("x", limit=3)
        self.assertEqual(3, len(items))

    def test_blank_query_makes_no_request(self) -> None:
        with patch("providers.wikimedia.fetch_json") as mocked:
            self.assertEqual([], WIKIMEDIA_BANGS["wp"].search("   "))
        mocked.assert_not_called()


class NormalizationTest(unittest.TestCase):
    def test_fields_map_onto_normalized_item(self) -> None:
        payload = _search_payload(["Defamiliarization"])
        extracts = {
            "query": {"pages": [{"pageid": 1000, "extract": "Ostranenie, or making strange."}]}
        }
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, extracts]):
            (item,) = WIKIMEDIA_BANGS["wp"].search("defamiliarization", limit=1)

        self.assertEqual("wikimedia:en.wikipedia.org/wp", item.connector)
        self.assertEqual("en.wikipedia.org:1000", item.source_id)
        self.assertEqual(
            "https://en.wikipedia.org/wiki/Defamiliarization", item.source_url
        )
        self.assertEqual("wiki_article", item.content_type)
        self.assertIn("Ostranenie", item.fulltext or "")
        # HTML from the API snippet must not survive into the index.
        self.assertNotIn("<span", item.summary or "")
        self.assertEqual("CC BY-SA 4.0", item.rights.get("license"))

    def test_namespace_drives_content_type(self) -> None:
        payload = _search_payload(["Wikipedia:Help desk"], ns=NS_PROJECT)
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            (item,) = WIKIMEDIA_BANGS["wphd"].search("deletion", limit=1)
        self.assertEqual("wiki_project_page", item.content_type)

    def test_extract_failure_does_not_break_search(self) -> None:
        payload = _search_payload(["Cartography"])
        with patch(
            "providers.wikimedia.fetch_json",
            side_effect=[payload, RuntimeError("extracts down")],
        ):
            (item,) = WIKIMEDIA_BANGS["wp"].search("cartography", limit=1)
        self.assertEqual("Cartography", item.title)

    def test_titles_with_spaces_become_underscored_urls(self) -> None:
        payload = _search_payload(["List of narrative techniques"])
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]):
            (item,) = WIKIMEDIA_BANGS["wpl"].search("x", limit=1)
        self.assertEqual(
            "https://en.wikipedia.org/wiki/List_of_narrative_techniques", item.source_url
        )


class CustomProviderTest(unittest.TestCase):
    def test_namespace_defaults_to_mainspace(self) -> None:
        provider = WikimediaProvider(bang="tst", label="T", site="example.org")
        payload = _search_payload(["Thing"])
        with patch("providers.wikimedia.fetch_json", side_effect=[payload, {}]) as mocked:
            provider.search("thing", limit=1)
        self.assertEqual(NS_MAIN, mocked.call_args_list[0].kwargs["params"]["srnamespace"])


if __name__ == "__main__":
    unittest.main()
