import unittest

from query_planner import (
    InteractionEvent,
    InteractionEventType,
    PlannerToggles,
    QueryIntent,
    RankCandidate,
    RankingSliders,
    SearchMode,
    UserPreferenceVector,
    classify_query_intent,
    compute_rank_weights,
    get_search_mode_presets,
    plan_query,
    rank_candidates,
    ranking_slider_config,
    update_user_preference_vector_from_events,
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

    def test_seed_and_mutate_mode_adds_branching_instruction(self):
        decision = plan_query(
            "Start from this saved note about punk zines",
            mode=SearchMode.SEED_AND_MUTATE,
        )
        self.assertIn("bookmarks", decision.connector_groups)
        self.assertTrue(decision.search_instructions)

    def test_contrarian_mode_adds_opposing_lenses(self):
        decision = plan_query(
            "Minimalist web design references",
            mode=SearchMode.CONTRARIAN,
        )
        self.assertIn("academic_databases", decision.connector_groups)
        self.assertIn("tumblr", decision.connector_groups)

    def test_time_tunnel_mode_adds_temporal_coverage_instruction(self):
        decision = plan_query(
            "Typeface politics",
            mode=SearchMode.TIME_TUNNEL,
        )
        self.assertIn("internet_archive", decision.connector_groups)
        self.assertTrue(
            any("decades" in instruction for instruction in decision.search_instructions)
        )

    def test_materiality_mode_prioritizes_archival_sources(self):
        decision = plan_query(
            "Dada collage references",
            mode=SearchMode.MATERIALITY,
        )
        self.assertEqual(decision.connector_groups[0], "internet_archive")

    def test_exposes_four_ui_mode_presets(self):
        presets = get_search_mode_presets()
        self.assertGreaterEqual(len(presets), 4)
        preset_modes = {preset.mode for preset in presets}
        self.assertIn(SearchMode.SEED_AND_MUTATE, preset_modes)
        self.assertIn(SearchMode.CONTRARIAN, preset_modes)
        self.assertIn(SearchMode.TIME_TUNNEL, preset_modes)
        self.assertIn(SearchMode.MATERIALITY, preset_modes)

    def test_ranking_slider_config_contains_all_controls(self):
        config = ranking_slider_config()
        self.assertIn("relevant_surprising", config)
        self.assertIn("focused_diverse", config)
        self.assertIn("recent_timeless", config)

    def test_interactions_update_user_preference_vector(self):
        vector = update_user_preference_vector_from_events(
            [
                InteractionEvent(
                    event_type=InteractionEventType.SAVED,
                    topics=("typography",),
                    source_id="internet_archive",
                    visual_style="brutalist",
                ),
                InteractionEvent(
                    event_type=InteractionEventType.SKIPPED,
                    topics=("minimalism",),
                    source_id="pinterest",
                    visual_style="minimalist",
                ),
            ]
        )
        self.assertGreater(vector.topic_preferences["typography"], 0.0)
        self.assertLess(vector.topic_preferences["minimalism"], 0.0)
        self.assertGreater(vector.source_trust["internet_archive"], 0.0)
        self.assertLess(vector.source_trust["pinterest"], 0.0)

    def test_preference_vector_feeds_into_rank_weights(self):
        base = compute_rank_weights(RankingSliders())
        vector = UserPreferenceVector(
            topic_preferences={"punk": 0.6},
            source_trust={"internet_archive": 0.5},
            visual_style_preferences={"collage": 0.7},
        )
        personalized = compute_rank_weights(RankingSliders(), vector)
        self.assertGreater(personalized["topic_preference"], base["topic_preference"])
        self.assertGreater(personalized["source_trust"], base["source_trust"])

    def test_rank_candidates_uses_personalization_scores(self):
        sliders = RankingSliders(relevant_surprising=0.7, focused_diverse=0.4, recent_timeless=0.5)
        vector = update_user_preference_vector_from_events(
            [
                InteractionEvent(
                    event_type=InteractionEventType.COLLECTION_ADDED,
                    topics=("zines",),
                    source_id="internet_archive",
                    visual_style="collage",
                )
            ]
        )
        ranked = rank_candidates(
            [
                RankCandidate(
                    candidate_id="a",
                    lexical_score=0.7,
                    semantic_score=0.7,
                    recency_score=0.4,
                    novelty_score=0.4,
                    source_id="internet_archive",
                    topics=("zines", "punk"),
                    visual_style="collage",
                ),
                RankCandidate(
                    candidate_id="b",
                    lexical_score=0.75,
                    semantic_score=0.75,
                    recency_score=0.4,
                    novelty_score=0.4,
                    source_id="pinterest",
                    topics=("interiors",),
                    visual_style="minimalist",
                ),
            ],
            sliders=sliders,
            preference_vector=vector,
        )
        self.assertEqual(ranked[0].candidate.candidate_id, "a")


if __name__ == "__main__":
    unittest.main()
