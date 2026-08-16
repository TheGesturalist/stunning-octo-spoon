"""Tests for the federated search layer: bang parsing, scoring primitives,
diversification, and graceful degradation when a provider fails."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.schema import NormalizedItem
from federation import (
    federated_search,
    length_confidence,
    lexical_score,
    parse_bangs,
    recency_score,
)
from providers.base import ProviderError, SearchProvider
from query_planner import RankingSliders


class FakeProvider(SearchProvider):
    def __init__(self, provider_id: str, titles: list[str], *, fail: bool = False):
        self.provider_id = provider_id
        self.label = provider_id
        self.group = "Test"
        self.library_connector = provider_id
        self.bang = provider_id.split(":")[-1]
        self.hint = ""
        self.default_rights = {}
        self._titles = titles
        self._fail = fail
        self.calls = 0

    def search(self, query: str, *, limit: int = 20) -> list[NormalizedItem]:
        self.calls += 1
        if self._fail:
            raise ProviderError("provider is down")
        return [
            NormalizedItem(
                connector=self.library_connector,
                source_id=f"{self.provider_id}-{i}",
                source_url=f"https://example.test/{self.provider_id}/{i}",
                title=title,
                fulltext=f"{title} " + " ".join(f"filler{n}" for n in range(40)),
                created_at="2020-01-01",
            )
            for i, title in enumerate(self._titles[:limit])
        ]


class ParseBangsTest(unittest.TestCase):
    BANGS = {"wphd": "wikimedia:wphd", "oa": "academic:openalex"}

    def test_single_bang_extracted(self) -> None:
        self.assertEqual(
            ("deletion criteria", ["wikimedia:wphd"]),
            parse_bangs("!wphd deletion criteria", self.BANGS),
        )

    def test_multiple_bangs_extracted_in_order(self) -> None:
        plain, ids = parse_bangs("!wphd !oa deletion", self.BANGS)
        self.assertEqual("deletion", plain)
        self.assertEqual(["wikimedia:wphd", "academic:openalex"], ids)

    def test_unknown_bang_stays_in_the_query(self) -> None:
        """A typo must degrade to a literal search, not a wrong source."""

        self.assertEqual(("!nope maps", []), parse_bangs("!nope maps", self.BANGS))

    def test_bang_mid_query_is_recognized(self) -> None:
        plain, ids = parse_bangs("deletion !wphd", self.BANGS)
        self.assertEqual("deletion", plain)
        self.assertEqual(["wikimedia:wphd"], ids)

    def test_duplicate_bangs_collapse(self) -> None:
        _, ids = parse_bangs("!oa !oa x", self.BANGS)
        self.assertEqual(["academic:openalex"], ids)

    def test_no_bangs_leaves_query_untouched(self) -> None:
        self.assertEqual(("plain query", []), parse_bangs("plain query", self.BANGS))


class ScoringTest(unittest.TestCase):
    def test_lexical_score_rewards_term_coverage(self) -> None:
        both = lexical_score("alpha beta", ["alpha", "beta"])
        one = lexical_score("alpha only", ["alpha", "beta"])
        self.assertGreater(both, one)

    def test_lexical_score_is_bounded(self) -> None:
        score = lexical_score("alpha " * 200, ["alpha"])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_lexical_score_zero_without_match(self) -> None:
        self.assertEqual(0.0, lexical_score("nothing here", ["absent"]))

    def test_length_confidence_penalizes_tiny_documents(self) -> None:
        self.assertLess(length_confidence(1), length_confidence(10))
        self.assertLess(length_confidence(10), length_confidence(30))
        self.assertEqual(1.0, length_confidence(30))
        self.assertEqual(1.0, length_confidence(500))
        self.assertEqual(0.0, length_confidence(0))

    def test_recency_score_prefers_newer(self) -> None:
        self.assertGreater(recency_score("2026-01-01"), recency_score("1970-01-01"))

    def test_recency_score_handles_unknown_dates(self) -> None:
        self.assertEqual(0.5, recency_score(None))
        self.assertEqual(0.5, recency_score("not a date"))

    def test_recency_score_accepts_several_formats(self) -> None:
        for value in ("2020", "2020-05", "2020-05-06", "2020-05-06T07:08:09Z"):
            self.assertGreater(recency_score(value), 0.0, msg=value)


class FederatedSearchTest(unittest.TestCase):
    def test_results_from_every_provider_are_merged(self) -> None:
        a = FakeProvider("test:a", ["alpha one", "alpha two"])
        b = FakeProvider("test:b", ["alpha three"])
        response = federated_search("alpha", [a, b], limit=10)
        self.assertEqual(3, len(response.hits))
        self.assertEqual({"test:a", "test:b"}, {h.provider_id for h in response.hits})

    def test_a_failing_provider_does_not_sink_the_search(self) -> None:
        good = FakeProvider("test:good", ["alpha one"])
        bad = FakeProvider("test:bad", [], fail=True)
        response = federated_search("alpha", [good, bad], limit=10)

        self.assertEqual(1, len(response.hits))
        outcomes = {o.provider_id: o for o in response.outcomes}
        self.assertTrue(outcomes["test:good"].ok)
        self.assertFalse(outcomes["test:bad"].ok)
        self.assertIn("provider is down", outcomes["test:bad"].error or "")

    def test_failures_are_reported_not_swallowed(self) -> None:
        bad = FakeProvider("test:bad", [], fail=True)
        response = federated_search("alpha", [bad], limit=5)
        self.assertEqual(0, len(response.hits))
        self.assertEqual(1, len(response.outcomes))
        self.assertFalse(response.outcomes[0].ok)

    def test_empty_query_short_circuits_without_calling_providers(self) -> None:
        provider = FakeProvider("test:a", ["alpha"])
        response = federated_search("   ", [provider], limit=5)
        self.assertEqual(0, len(response.hits))
        self.assertEqual(0, provider.calls)

    def test_no_providers_returns_empty_response(self) -> None:
        response = federated_search("alpha", [], limit=5)
        self.assertEqual(0, len(response.hits))
        self.assertEqual(0, len(response.outcomes))

    def test_limit_is_respected(self) -> None:
        a = FakeProvider("test:a", [f"alpha {i}" for i in range(20)])
        response = federated_search("alpha", [a], limit=4)
        self.assertEqual(4, len(response.hits))

    def test_diverse_slider_interleaves_sources(self) -> None:
        """One source must not take every slot when diversity is up.

        Both providers return the same number of rows, so `rank_candidates`'s own
        `1 / result_count` diversity term ties and cannot separate them — which is
        exactly the gap `_diversify` exists to close.
        """

        def pair() -> tuple[FakeProvider, FakeProvider]:
            strong = FakeProvider(
                "test:strong", [f"alpha alpha alpha {i}" for i in range(5)]
            )
            weak = FakeProvider("test:weak", [f"alpha {i}" for i in range(5)])
            return strong, weak

        focused = federated_search(
            "alpha", list(pair()), sliders=RankingSliders(focused_diverse=0.0), limit=3
        )
        diverse = federated_search(
            "alpha", list(pair()), sliders=RankingSliders(focused_diverse=1.0), limit=3
        )
        self.assertEqual(
            1, len({h.provider_id for h in focused.hits}), "focused should not interleave"
        )
        self.assertEqual(
            2, len({h.provider_id for h in diverse.hits}), "diverse should interleave"
        )

    def test_source_weights_boost_a_provider(self) -> None:
        a = FakeProvider("test:a", ["alpha one"])
        b = FakeProvider("test:b", ["alpha one"])
        boosted = federated_search(
            "alpha", [a, b], source_weights={"test:b": 1.0}, limit=2
        )
        self.assertEqual("test:b", boosted.hits[0].provider_id)

    def test_library_urls_mark_results_as_already_held(self) -> None:
        provider = FakeProvider("test:a", ["alpha one"])
        response = federated_search(
            "alpha", [provider], limit=2, library_urls={"https://example.test/test:a/0"}
        )
        self.assertTrue(response.hits[0].in_library)

    def test_response_serializes(self) -> None:
        provider = FakeProvider("test:a", ["alpha one"])
        payload = federated_search("alpha", [provider], limit=2).to_dict()
        self.assertEqual("alpha", payload["plain_query"])
        self.assertEqual(1, len(payload["results"]))
        self.assertIn("lexical", payload["weights"])
        self.assertIn("score", payload["results"][0])


if __name__ == "__main__":
    unittest.main()
