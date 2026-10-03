from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_consultancies import ProductionConsultancyContext
from src.production_context import ProductionContextStore


class ProductionConsultancyContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production_context.duckdb"
        self.store = ProductionContextStore(self.path)
        self.context = ProductionConsultancyContext(self.store)

        for source_id, date in (
            ("src-old", "2020-01-01T00:00:00Z"),
            ("src-mid", "2023-01-01T00:00:00Z"),
            ("src-target", "2024-01-01T00:00:00Z"),
            ("src-late", "2025-01-01T00:00:00Z"),
        ):
            self.store.upsert_source(
                {
                    "source_id": source_id,
                    "url": f"https://example.test/{source_id}",
                    "published_at": date,
                    "retrieved_at": date,
                    "confidence": 0.95,
                }
            )

        for entity_id, name in (
            ("consult-a", "Consultancy A"),
            ("consult-b", "Consultancy B"),
        ):
            self.store.upsert_entity(
                {
                    "entity_id": entity_id,
                    "kind": "consultancy",
                    "name": name,
                }
            )

        for project_id, release_at, known_at in (
            ("prior-a1", "2020-07-01T00:00:00Z", "2020-01-01T00:00:00Z"),
            ("prior-a2", "2022-07-01T00:00:00Z", "2022-01-01T00:00:00Z"),
            ("late-old", "2021-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            ("target", "2025-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
            ("future", "2026-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        ):
            self.store.upsert_project(
                {
                    "project_id": project_id,
                    "title": project_id,
                    "release_at": release_at,
                    "identity_known_at": known_at,
                }
            )

        self._engage("prior-a1", "consult-a", "story", "writing", "src-old", "2020-01-01T00:00:00Z")
        self._engage("prior-a2", "consult-a", "character", "production", "src-mid", "2023-01-01T00:00:00Z")
        self._engage("future", "consult-a", "story", "writing", "src-target", "2024-01-01T00:00:00Z")
        self._engage("late-old", "consult-a", "story", "writing", "src-late", "2025-01-01T00:00:00Z")

        # Target: A имеет два scope, B — один. Повтор same scope/stage не должен
        # раздувать distinct context/entity counters.
        self._engage("target", "consult-a", "story", "writing", "src-target", "2024-01-01T00:00:00Z")
        self._engage("target", "consult-a", "character", "production", "src-target", "2024-01-02T00:00:00Z")
        self._engage("target", "consult-a", "story", "writing", "src-target", "2024-01-03T00:00:00Z")
        self._engage("target", "consult-b", "worldbuilding", "development", "src-target", "2024-01-01T00:00:00Z")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _engage(self, project_id, entity_id, scope, stage, source_id, known_at):
        self.store.add_consultancy(
            {
                "engagement_id": f"{project_id}:{entity_id}:{scope}:{stage}:{known_at}",
                "project_id": project_id,
                "entity_id": entity_id,
                "scope": scope,
                "stage": stage,
                "known_at": known_at,
                "source_id": source_id,
            }
        )

    def test_current_context_uses_distinct_entities_scopes_and_stages(self):
        f = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_consultancy_entity_count"], 2.0)
        self.assertEqual(f["production_consultancy_engagement_context_count"], 3.0)
        self.assertEqual(f["production_consultancy_scope_diversity"], 3.0)
        self.assertEqual(f["production_consultancy_stage_diversity"], 3.0)
        self.assertEqual(f["production_consultancy_multi_scope_entity_count"], 1.0)
        self.assertEqual(f["production_consultancy_context_scope_story_entity_count"], 1.0)
        self.assertEqual(f["production_consultancy_context_scope_character_entity_count"], 1.0)
        self.assertEqual(f["production_consultancy_context_scope_worldbuilding_entity_count"], 1.0)

    def test_history_counts_zero_history_entity_in_denominator_and_mean(self):
        f = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        # A имеет 2 прошлых проекта, B — 0.
        self.assertEqual(f["production_consultancy_history_known_ratio"], 0.5)
        self.assertEqual(f["production_consultancy_prior_project_count_mean"], 1.0)
        self.assertEqual(f["production_consultancy_prior_project_count_max"], 2.0)
        self.assertEqual(f["production_consultancy_prior_shared_project_count"], 2.0)

    def test_same_scope_history_is_separate_from_any_consultancy_history(self):
        f = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        # A target scopes story+character совпадают с двумя prior scopes; B истории нет.
        self.assertEqual(f["production_consultancy_same_scope_history_known_ratio"], 0.5)
        self.assertEqual(f["production_consultancy_same_scope_prior_project_count_mean"], 1.0)
        self.assertEqual(f["production_consultancy_same_scope_prior_shared_project_count"], 2.0)

    def test_future_project_never_enters_prior_history(self):
        f = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_consultancy_prior_shared_project_count"], 2.0)

    def test_late_disclosed_old_engagement_appears_only_after_known_at(self):
        early = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.context.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(early["production_consultancy_prior_project_count_max"], 2.0)
        self.assertEqual(late["production_consultancy_prior_project_count_max"], 3.0)
        self.assertEqual(late["production_consultancy_prior_shared_project_count"], 3.0)

    def test_detail_rows_are_deduplicated_by_entity_scope_stage(self):
        rows = self.context.engagements_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["entity_id"] for row in rows}, {"consult-a", "consult-b"})

    def test_features_have_no_named_quality_or_rating_signal(self):
        f = self.context.features_as_of("target", "2024-06-01T00:00:00Z")
        keys = " ".join(f).casefold()
        self.assertNotIn("rating", keys)
        self.assertNotIn("quality", keys)
        self.assertNotIn("consult-a", keys)
        self.assertNotIn("consult-b", keys)


if __name__ == "__main__":
    unittest.main()
