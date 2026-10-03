from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
)
from src.production_continuity import ProductionContinuityContext


class ProductionContinuityContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production_context.duckdb"
        self.store = ProductionContextStore(self.path)
        self.continuity = ProductionContinuityContext(self.store)

        for source_id, date in (
            ("src-old", "2023-01-01T00:00:00Z"),
            ("src-target", "2024-01-01T00:00:00Z"),
            ("src-late", "2026-01-01T00:00:00Z"),
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

        for project_id, release_at in (
            ("prior-a", "2020-07-01T00:00:00Z"),
            ("prior-b", "2022-07-01T00:00:00Z"),
            ("target", "2025-07-01T00:00:00Z"),
            ("future-linked", "2026-07-01T00:00:00Z"),
            ("downstream", "2027-07-01T00:00:00Z"),
            ("late-downstream", "2028-07-01T00:00:00Z"),
            ("unknown-date", None),
        ):
            self.store.upsert_project(
                {
                    "project_id": project_id,
                    "title": project_id,
                    "release_at": release_at,
                    "identity_known_at": "2023-01-01T00:00:00Z",
                }
            )

        self._dep(
            "d1",
            "target",
            "prior-a",
            "sequel_of",
            "story",
            "2024-01-01T00:00:00Z",
            "src-target",
        )
        self._dep(
            "d2",
            "target",
            "prior-b",
            "requires_context_from",
            "continuity",
            "2024-01-01T00:00:00Z",
            "src-target",
        )
        self._dep(
            "d3",
            "target",
            "future-linked",
            "crossover_with",
            "character",
            "2024-01-01T00:00:00Z",
            "src-target",
        )
        self._dep(
            "d4",
            "target",
            "unknown-date",
            "other",
            "world",
            "2024-01-01T00:00:00Z",
            "src-target",
        )
        # Дубликат same fact с другим source/dependency ID не должен удваивать feature.
        self._dep(
            "d1-copy",
            "target",
            "prior-a",
            "sequel_of",
            "story",
            "2024-02-01T00:00:00Z",
            "src-target",
        )

        # Future downstream проект уже публично объявлен и зависит от target.
        self._dep(
            "downstream-target",
            "downstream",
            "target",
            "sequel_of",
            "story",
            "2024-03-01T00:00:00Z",
            "src-target",
        )
        # Этот downstream станет известен только после раннего cutoff.
        self._dep(
            "late-downstream-target",
            "late-downstream",
            "target",
            "sequel_of",
            "story",
            "2026-01-01T00:00:00Z",
            "src-late",
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _dep(
        self,
        dependency_id,
        project_id,
        related_project_id,
        relation_type,
        scope,
        known_at,
        source_id,
    ):
        self.continuity.add_dependency(
            {
                "dependency_id": dependency_id,
                "project_id": project_id,
                "related_project_id": related_project_id,
                "relation_type": relation_type,
                "scope": scope,
                "known_at": known_at,
                "source_id": source_id,
            }
        )

    def test_features_are_temporal_and_deduplicate_same_fact(self):
        f = self.continuity.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_continuity_dependency_count"], 4.0)
        self.assertEqual(f["production_continuity_prior_released_count"], 2.0)
        self.assertEqual(f["production_continuity_future_announced_count"], 1.0)
        self.assertEqual(f["production_continuity_unknown_release_count"], 1.0)
        self.assertEqual(f["production_continuity_relation_sequel_of_count"], 1.0)
        self.assertEqual(f["production_continuity_relation_requires_context_from_count"], 1.0)
        self.assertEqual(f["production_continuity_relation_crossover_with_count"], 1.0)
        self.assertEqual(f["production_continuity_scope_story_count"], 1.0)
        self.assertEqual(f["production_continuity_scope_continuity_count"], 1.0)
        self.assertEqual(f["production_continuity_scope_character_count"], 1.0)
        self.assertEqual(f["production_continuity_scope_world_count"], 1.0)
        self.assertEqual(f["production_continuity_known"], 1.0)

    def test_downstream_announced_projects_are_separate_from_target_dependencies(self):
        f = self.continuity.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_continuity_downstream_known_count"], 1.0)
        self.assertEqual(f["production_continuity_downstream_future_count"], 1.0)
        # 4 outgoing related + one distinct downstream project.
        self.assertEqual(f["production_continuity_cross_project_count"], 5.0)

    def test_late_disclosed_downstream_is_hidden_before_known_at(self):
        early = self.continuity.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.continuity.features_as_of("target", "2026-02-01T00:00:00Z")
        self.assertEqual(early["production_continuity_downstream_known_count"], 1.0)
        self.assertEqual(late["production_continuity_downstream_known_count"], 2.0)

    def test_dependency_timeline_has_release_state(self):
        rows = self.continuity.dependencies_as_of("target", "2024-06-01T00:00:00Z")
        by_id = {row["related_project_id"]: row for row in rows}
        self.assertEqual(by_id["prior-a"]["release_state"], "released")
        self.assertEqual(by_id["future-linked"]["release_state"], "future")
        self.assertEqual(by_id["unknown-date"]["release_state"], "unknown")

    def test_prior_release_span_is_positive(self):
        f = self.continuity.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertGreater(f["production_continuity_prior_release_span_years"], 3.0)

    def test_self_dependency_and_unknown_enum_are_rejected(self):
        with self.assertRaises(ProductionContextValidationError):
            self.continuity.add_dependency(
                {
                    "project_id": "target",
                    "related_project_id": "target",
                    "relation_type": "sequel_of",
                    "scope": "story",
                    "known_at": "2024-01-01T00:00:00Z",
                    "source_id": "src-target",
                }
            )
        with self.assertRaises(ProductionContextValidationError):
            self.continuity.add_dependency(
                {
                    "project_id": "target",
                    "related_project_id": "prior-a",
                    "relation_type": "magic_dependency",
                    "scope": "story",
                    "known_at": "2024-01-01T00:00:00Z",
                    "source_id": "src-target",
                }
            )


if __name__ == "__main__":
    unittest.main()
