import unittest

from query_planner import (
    PlannerToggles,
    SearchMode,
    QueryIntent,
    classify_query_intent,
    get_search_mode_presets,
    plan_query,
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


if __name__ == "__main__":
    unittest.main()
