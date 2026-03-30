import unittest

from query_planner import (
    PlannerToggles,
    QueryIntent,
    classify_query_intent,
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


if __name__ == "__main__":
    unittest.main()
