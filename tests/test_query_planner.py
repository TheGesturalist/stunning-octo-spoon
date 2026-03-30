import unittest

from query_planner import (
    PlannerToggles,
    QueryIntent,
    RankCandidate,
    RankingSliders,
    classify_query_intent,
    compute_rank_weights,
    plan_query,
    rank_candidates,
    ranking_slider_config,
)


class QueryPlannerTests(unittest.TestCase):
    def test_classifies_academic_intent(self):
        self.assertEqual(
            classify_query_intent("Find peer review paper with DOI citations"),
            QueryIntent.ACADEMIC,
        )

    def test_visual_only_forces_visual_mapping(self):
        decision = plan_query(
            "Find canonical texts about typography",
            toggles=PlannerToggles(visual_only=True),
        )
        self.assertEqual(decision.intent, QueryIntent.VISUAL)
        self.assertIn("pinterest", decision.connector_groups)

    def test_deep_search_adds_canonical_backfill(self):
        decision = plan_query(
            "Search journals on media archaeology",
            toggles=PlannerToggles(deep_search=True),
        )
        self.assertIn("internet_archive", decision.connector_groups)

    def test_fast_search_limits_connectors(self):
        decision = plan_query(
            "Need visual references for brutalism",
            toggles=PlannerToggles(fast_search=True),
        )
        self.assertEqual(len(decision.connector_groups), 2)

    def test_ranking_slider_config_contains_expected_labels(self):
        config = ranking_slider_config()
        self.assertEqual(config["relevant_surprising"]["label_left"], "Relevant")
        self.assertEqual(config["focused_diverse"]["label_right"], "Diverse")
        self.assertEqual(config["recent_timeless"]["label_left"], "Recent")

    def test_relevant_mode_increases_lexical_weight(self):
        relevant_weights = compute_rank_weights(
            RankingSliders(relevant_surprising=0.0, focused_diverse=0.0, recent_timeless=0.5)
        )
        surprising_weights = compute_rank_weights(
            RankingSliders(relevant_surprising=1.0, focused_diverse=0.0, recent_timeless=0.5)
        )
        self.assertGreater(relevant_weights.lexical_match, surprising_weights.lexical_match)
        self.assertGreater(surprising_weights.novelty, relevant_weights.novelty)

    def test_diversity_mode_boosts_alternative_source(self):
        candidates = [
            RankCandidate(
                id="a1",
                source="source_a",
                lexical_match=0.94,
                semantic_match=0.90,
                recency=0.65,
                novelty=0.25,
            ),
            RankCandidate(
                id="a2",
                source="source_a",
                lexical_match=0.91,
                semantic_match=0.88,
                recency=0.62,
                novelty=0.28,
            ),
            RankCandidate(
                id="b1",
                source="source_b",
                lexical_match=0.82,
                semantic_match=0.80,
                recency=0.55,
                novelty=0.65,
            ),
        ]

        ranked = rank_candidates(
            candidates,
            sliders=RankingSliders(
                relevant_surprising=0.8,
                focused_diverse=1.0,
                recent_timeless=0.5,
            ),
        )

        top_two_sources = {ranked[0].candidate.source, ranked[1].candidate.source}
        self.assertEqual(len(top_two_sources), 2)


if __name__ == "__main__":
    unittest.main()
