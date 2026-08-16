"""Tests for the exploratory search modes.

Each mode has to *do* something observable — the point of this work was that the
presets had been described for months while changing nothing.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.schema import NormalizedItem
from federation import federated_search
from query_planner import RankingSliders, SearchMode
from search_modes import (
    decade_spread,
    material_first,
    mine_terms,
    plan_search_mode,
)
from tests.test_federation import FakeProvider

ALL_IDS = [
    "library:arena", "arena:blocks", "arena:channels", "cultural:pdr",
    "wikimedia:wp", "wikimedia:wpi", "academic:openalex", "academic:crossref",
]


@dataclass
class _Hit:
    item: NormalizedItem


def _hit(**kw) -> _Hit:
    kw.setdefault("connector", "x")
    kw.setdefault("source_id", "1")
    return _Hit(item=NormalizedItem(**kw))


class PlanTest(unittest.TestCase):
    def _plan(self, mode, selected=("wikimedia:wp",), query="maps"):
        return plan_search_mode(
            mode, query=query, sliders=RankingSliders(),
            selected_ids=list(selected), available_ids=ALL_IDS,
        )

    def test_standard_changes_nothing(self) -> None:
        plan = self._plan(SearchMode.STANDARD)
        self.assertEqual((), plan.add_provider_ids)
        self.assertEqual((), plan.extra_queries)
        self.assertIsNone(plan.rerank)
        self.assertFalse(plan.mutate)

    def test_time_tunnel_turns_off_recency_and_spreads_decades(self) -> None:
        plan = self._plan(SearchMode.TIME_TUNNEL)
        self.assertEqual(0.0, plan.sliders.recent_timeless)
        self.assertEqual("decade_spread", plan.rerank)

    def test_contrarian_maximizes_diversity_and_adds_a_pass(self) -> None:
        plan = self._plan(SearchMode.CONTRARIAN, query="modernism")
        self.assertEqual(1.0, plan.sliders.focused_diverse)
        self.assertEqual(1, len(plan.extra_queries))
        self.assertIn("modernism", plan.extra_queries[0])
        self.assertIn("critique", plan.extra_queries[0])

    def test_materiality_weights_material_sources_up(self) -> None:
        plan = self._plan(SearchMode.MATERIALITY)
        self.assertEqual("material_first", plan.rerank)
        self.assertIn("cultural:pdr", plan.source_weights)
        self.assertGreater(plan.source_weights["cultural:pdr"], 0)

    def test_seed_and_mutate_raises_novelty_and_sets_mutate(self) -> None:
        plan = self._plan(SearchMode.SEED_AND_MUTATE)
        self.assertTrue(plan.mutate)
        self.assertGreaterEqual(plan.sliders.relevant_surprising, 0.75)

    def test_modes_never_add_a_source_that_does_not_exist(self) -> None:
        for mode in SearchMode:
            plan = plan_search_mode(
                mode, query="x", sliders=RankingSliders(),
                selected_ids=[], available_ids=["wikimedia:wp"],
            )
            for pid in plan.add_provider_ids:
                self.assertEqual("wikimedia:wp", pid, msg=f"{mode} invented a source")

    def test_modes_never_re_add_an_already_selected_source(self) -> None:
        plan = plan_search_mode(
            SearchMode.MATERIALITY, query="x", sliders=RankingSliders(),
            selected_ids=ALL_IDS, available_ids=ALL_IDS,
        )
        self.assertEqual((), plan.add_provider_ids)

    def test_plan_serializes(self) -> None:
        payload = self._plan(SearchMode.TIME_TUNNEL).to_dict()
        self.assertEqual("time_tunnel", payload["mode"])
        self.assertIn("notes", payload)


class DecadeSpreadTest(unittest.TestCase):
    def test_one_era_cannot_fill_the_page(self) -> None:
        hits = [_hit(created_at="2020-01-01") for _ in range(5)]
        hits += [_hit(created_at="1890-01-01"), _hit(created_at="1950-01-01")]
        out = decade_spread(hits, 3)
        decades = {(h.item.created_at or "")[:3] for h in out}
        self.assertEqual(3, len(out))
        self.assertEqual(3, len(decades), "expected one hit per decade")

    def test_undated_items_go_last(self) -> None:
        hits = [_hit(created_at=None), _hit(created_at="1900-01-01")]
        out = decade_spread(hits, 2)
        self.assertEqual("1900-01-01", out[0].item.created_at)
        self.assertIsNone(out[1].item.created_at)

    def test_respects_limit(self) -> None:
        hits = [_hit(created_at=f"{1900+i*10}-01-01") for i in range(10)]
        self.assertEqual(4, len(decade_spread(hits, 4)))

    def test_all_undated_still_returns_results(self) -> None:
        hits = [_hit(created_at=None) for _ in range(3)]
        self.assertEqual(2, len(decade_spread(hits, 2)))

    def test_garbage_dates_treated_as_undated(self) -> None:
        hits = [_hit(created_at="not-a-date"), _hit(created_at="1900")]
        out = decade_spread(hits, 2)
        self.assertEqual("1900", out[0].item.created_at)


class MaterialFirstTest(unittest.TestCase):
    def test_images_and_collections_promoted(self) -> None:
        hits = [
            _hit(content_type="wiki_article", source_id="a"),
            _hit(content_type="arena_image", source_id="b"),
            _hit(content_type="pdr_collection", source_id="c"),
        ]
        out = material_first(hits, 3)
        self.assertEqual(["b", "c", "a"], [h.item.source_id for h in out])

    def test_item_with_an_image_url_counts_as_material(self) -> None:
        hits = [
            _hit(content_type="wiki_article", source_id="a"),
            _hit(content_type="arena_link", source_id="b",
                 metadata={"image_url": "https://x/y.jpg"}),
        ]
        self.assertEqual("b", material_first(hits, 2)[0].item.source_id)

    def test_order_is_stable_within_each_group(self) -> None:
        hits = [_hit(content_type="wiki_article", source_id=str(i)) for i in range(4)]
        self.assertEqual(["0","1","2","3"], [h.item.source_id for h in material_first(hits, 4)])


class MineTermsTest(unittest.TestCase):
    def test_terms_shared_by_several_results_win(self) -> None:
        hits = [
            _hit(title="cartography and projection", summary=""),
            _hit(title="projection methods", summary=""),
            _hit(title="unrelated", summary=""),
        ]
        self.assertIn("projection", mine_terms(hits, exclude=[]))

    def test_query_terms_are_excluded(self) -> None:
        hits = [_hit(title="maps and atlases"), _hit(title="maps and charts")]
        self.assertNotIn("maps", mine_terms(hits, exclude=["maps"]))

    def test_stopwords_excluded(self) -> None:
        hits = [_hit(title="that which is there"), _hit(title="that which is here")]
        for term in mine_terms(hits, exclude=[]):
            self.assertNotIn(term, {"that", "which", "there", "here"})

    def test_singletons_rejected_as_noise(self) -> None:
        hits = [_hit(title="alpha"), _hit(title="beta")]
        self.assertEqual([], mine_terms(hits, exclude=[]))

    def test_no_hits_yields_nothing(self) -> None:
        self.assertEqual([], mine_terms([], exclude=[]))


class ModeIntegrationTest(unittest.TestCase):
    """Modes must survive the real federated_search path."""

    def _providers(self):
        return [
            FakeProvider("wikimedia:wp", [f"alpha {i}" for i in range(6)]),
            FakeProvider("academic:openalex", [f"alpha paper {i}" for i in range(6)]),
        ]

    def test_standard_reports_no_mode_plan(self) -> None:
        r = federated_search("alpha", self._providers(), limit=5)
        self.assertEqual("standard", r.mode)
        self.assertIsNone(r.mode_plan)

    def test_mode_is_reported_in_the_response(self) -> None:
        r = federated_search(
            "alpha", self._providers(), limit=5, mode=SearchMode.TIME_TUNNEL
        )
        self.assertEqual("time_tunnel", r.mode)
        self.assertIsNotNone(r.mode_plan)
        self.assertEqual("decade_spread", (r.mode_plan or {})["rerank"])

    def test_contrarian_runs_an_extra_pass(self) -> None:
        provs = self._providers()
        federated_search("alpha", provs, limit=5, mode=SearchMode.CONTRARIAN)
        # One pass for the query, one for the critique expansion.
        self.assertEqual(2, provs[0].calls)

    def test_seed_and_mutate_runs_a_second_pass_when_terms_are_minable(self) -> None:
        provs = [FakeProvider("wikimedia:wp", ["alpha cartography atlas"] * 4)]
        federated_search("alpha", provs, limit=5, mode=SearchMode.SEED_AND_MUTATE)
        self.assertGreaterEqual(provs[0].calls, 2)

    def test_a_mode_can_pull_in_a_source_not_selected(self) -> None:
        selected = [FakeProvider("wikimedia:wp", ["alpha one"])]
        extra = FakeProvider("academic:crossref", ["alpha two"])
        r = federated_search(
            "alpha", selected, limit=5, mode=SearchMode.TIME_TUNNEL,
            all_providers=selected + [extra],
        )
        self.assertIn("academic:crossref", {h.provider_id for h in r.hits})

    def test_outcomes_report_each_source_once_across_passes(self) -> None:
        provs = self._providers()
        r = federated_search("alpha", provs, limit=5, mode=SearchMode.CONTRARIAN)
        ids = [o.provider_id for o in r.outcomes]
        self.assertEqual(len(ids), len(set(ids)), "a source appeared twice in outcomes")

    def test_limit_is_respected_under_every_mode(self) -> None:
        for mode in SearchMode:
            r = federated_search("alpha", self._providers(), limit=3, mode=mode)
            self.assertLessEqual(len(r.hits), 3, msg=f"{mode} overran the limit")


if __name__ == "__main__":
    unittest.main()
