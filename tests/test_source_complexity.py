from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.source_complexity import SourceComplexityContext
from src.source_context import SourceContextStore, SourceContextValidationError


class SourceComplexityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "source_context.duckdb"
        self.store = SourceContextStore(self.db_path)
        self.complexity = SourceComplexityContext(self.store)

        self.store.upsert_source(
            {
                "source_id": "src-1",
                "url": "https://example.test/source",
                "published_at": "2024-01-01T00:00:00Z",
                "retrieved_at": "2024-01-01T00:00:00Z",
                "confidence": 0.95,
            }
        )
        for work_id in ("book-a", "book-b"):
            self.store.upsert_work(
                {
                    "work_id": work_id,
                    "title": work_id,
                    "source_type": "novel",
                    "first_publication_at": "2000-01-01T00:00:00Z",
                }
            )
        self.store.upsert_project(
            {
                "project_id": "target",
                "title": "Target",
                "release_at": "2027-01-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 120,
                "format_known_at": "2024-01-01T00:00:00Z",
            }
        )
        for index, work_id in enumerate(("book-a", "book-b"), start=1):
            self.store.link_source(
                {
                    "link_id": f"target-{work_id}",
                    "project_id": "target",
                    "work_id": work_id,
                    "relation_type": "adaptation_of",
                    "is_primary": index == 1,
                    "known_at": "2024-01-01T00:00:00Z",
                    "source_id": "src-1",
                }
            )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _add(
        self,
        work_id: str,
        *,
        snapshot_id: str,
        method: str = "manual-structural-counts",
        version: str = "1",
        known_at: str = "2024-02-01T00:00:00Z",
        coverage: float = 1.0,
        words: int = 100000,
        characters: int = 20,
        plotlines: int = 4,
    ) -> None:
        self.complexity.add_snapshot(
            {
                "snapshot_id": snapshot_id,
                "work_id": work_id,
                "method": method,
                "method_version": version,
                "measured_at": known_at,
                "known_at": known_at,
                "source_id": "src-1",
                "coverage_fraction": coverage,
                "metrics": {
                    "source_length_words": words,
                    "character_count": characters,
                    "plotline_count": plotlines,
                },
            }
        )

    def test_features_use_only_exact_method_and_version(self):
        self._add("book-a", snapshot_id="a-v1", characters=20)
        self._add(
            "book-a",
            snapshot_id="a-v2",
            method="automatic-story-structure",
            version="2",
            characters=200,
        )
        self._add("book-b", snapshot_id="b-v1", characters=40, coverage=0.5)

        f = self.complexity.features_as_of(
            "target",
            "2024-06-01T00:00:00Z",
            method="manual-structural-counts",
            method_version="1",
        )
        self.assertEqual(f["source_complexity_linked_work_count"], 2.0)
        self.assertEqual(f["source_complexity_measured_work_count"], 2.0)
        self.assertEqual(f["source_complexity_work_coverage_ratio"], 1.0)
        self.assertAlmostEqual(f["source_complexity_measurement_coverage_mean"], 0.75)
        self.assertEqual(f["source_complexity_character_count_mean"], 30.0)
        self.assertEqual(f["source_complexity_character_count_max"], 40.0)
        self.assertAlmostEqual(
            f["source_complexity_character_density_per_10k_words_mean"], 3.0
        )

    def test_latest_visible_snapshot_wins_within_same_protocol(self):
        self._add("book-a", snapshot_id="a-old", characters=20)
        self._add(
            "book-a",
            snapshot_id="a-new",
            known_at="2025-01-01T00:00:00Z",
            characters=50,
        )
        early = self.complexity.features_as_of(
            "target",
            "2024-06-01T00:00:00Z",
            method="manual-structural-counts",
            method_version="1",
        )
        late = self.complexity.features_as_of(
            "target",
            "2025-06-01T00:00:00Z",
            method="manual-structural-counts",
            method_version="1",
        )
        self.assertEqual(early["source_complexity_character_count_mean"], 20.0)
        self.assertEqual(late["source_complexity_character_count_mean"], 50.0)

    def test_missing_work_lowers_coverage_instead_of_using_fallback(self):
        self._add("book-a", snapshot_id="a-only", characters=20)
        f = self.complexity.features_as_of(
            "target",
            "2024-06-01T00:00:00Z",
            method="manual-structural-counts",
            method_version="1",
        )
        self.assertEqual(f["source_complexity_work_coverage_ratio"], 0.5)
        self.assertEqual(f["source_complexity_character_count_known_ratio"], 0.5)
        self.assertEqual(f["source_complexity_character_count_mean"], 20.0)

    def test_method_inventory_is_temporal(self):
        self._add("book-a", snapshot_id="early")
        self._add(
            "book-b",
            snapshot_id="late",
            known_at="2026-01-01T00:00:00Z",
        )
        early = self.complexity.available_methods_as_of(
            "target", "2025-01-01T00:00:00Z"
        )
        self.assertEqual(len(early), 1)
        self.assertEqual(early[0]["measured_work_count"], 1)
        self.assertEqual(early[0]["work_coverage_ratio"], 0.5)

    def test_known_at_cannot_precede_measurement_and_unknown_metrics_are_rejected(self):
        with self.assertRaises(SourceContextValidationError):
            self.complexity.add_snapshot(
                {
                    "work_id": "book-a",
                    "method": "manual",
                    "method_version": "1",
                    "measured_at": "2025-01-01T00:00:00Z",
                    "known_at": "2024-01-01T00:00:00Z",
                    "source_id": "src-1",
                    "metrics": {"character_count": 1},
                }
            )
        with self.assertRaises(SourceContextValidationError):
            self.complexity.add_snapshot(
                {
                    "work_id": "book-a",
                    "method": "manual",
                    "method_version": "1",
                    "measured_at": "2025-01-01T00:00:00Z",
                    "known_at": "2025-01-01T00:00:00Z",
                    "source_id": "src-1",
                    "metrics": {"magic_complexity_score": 99},
                }
            )


if __name__ == "__main__":
    unittest.main()
