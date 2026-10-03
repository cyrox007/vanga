from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.source_context import SourceContextStore
from src.source_format_pressure import SourceFormatPressureContext


class SourceFormatPressureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "source_context.duckdb"
        self.store = SourceContextStore(self.db_path)
        self.pressure = SourceFormatPressureContext(self.store)

        for source_id, date in (
            ("src-early", "2024-01-01T00:00:00Z"),
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

        self.store.upsert_work(
            {
                "work_id": "book-a",
                "title": "Book A",
                "source_type": "novel_series",
                "first_publication_at": "2000-01-01T00:00:00Z",
                "series_id": "series-a",
                "series_position": 2,
                "series_size": 5,
            }
        )
        self.store.upsert_work(
            {
                "work_id": "book-b",
                "title": "Book B",
                "source_type": "novel_series",
                "first_publication_at": "2010-01-01T00:00:00Z",
                "series_id": "series-a",
                "series_position": 4,
                "series_size": 5,
            }
        )
        self.store.upsert_work(
            {
                "work_id": "future-source",
                "title": "Future Source",
                "source_type": "comic",
                "first_publication_at": "2027-01-01T00:00:00Z",
            }
        )
        self.store.upsert_work(
            {
                "work_id": "unknown-date",
                "title": "Unknown Date",
                "source_type": "game",
            }
        )

        self.store.upsert_project(
            {
                "project_id": "target",
                "title": "Target",
                "release_at": "2026-07-01T00:00:00Z",
                "adaptation_format": "miniseries",
                "planned_episode_count": 6,
                "planned_episode_runtime_minutes": 50,
                "format_known_at": "2024-01-01T00:00:00Z",
            }
        )
        self._link("book-a", "adaptation_of", True, "2024-01-01T00:00:00Z", "src-early")
        self._link("book-b", "adaptation_of", False, "2025-01-01T00:00:00Z", "src-late")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _link(
        self,
        work_id: str,
        relation_type: str,
        is_primary: bool,
        known_at: str,
        source_id: str,
        *,
        suffix: str = "",
    ) -> None:
        self.store.link_source(
            {
                "link_id": f"target-{work_id}-{relation_type}{suffix}",
                "project_id": "target",
                "work_id": work_id,
                "relation_type": relation_type,
                "is_primary": is_primary,
                "known_at": known_at,
                "source_id": source_id,
            }
        )

    def test_early_cutoff_uses_only_known_source_works(self):
        f = self.pressure.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_age_at_release_known_ratio"], 1.0)
        self.assertGreater(f["source_age_at_release_years_mean"], 26.0)
        self.assertLess(f["source_age_at_release_years_mean"], 27.0)
        self.assertEqual(f["source_series_count"], 1.0)
        self.assertAlmostEqual(f["source_series_progress_ratio_mean"], 0.4)
        self.assertEqual(f["source_runtime_per_linked_work_minutes"], 300.0)
        self.assertEqual(f["source_runtime_per_primary_work_minutes"], 300.0)
        self.assertEqual(f["source_episodes_per_linked_work"], 6.0)

    def test_late_second_work_changes_only_visible_aggregates(self):
        f = self.pressure.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(f["source_age_at_release_known_ratio"], 1.0)
        self.assertAlmostEqual(f["source_series_progress_ratio_mean"], 0.6)
        self.assertAlmostEqual(f["source_series_progress_ratio_max"], 0.8)
        self.assertEqual(f["source_runtime_per_linked_work_minutes"], 150.0)
        self.assertEqual(f["source_runtime_per_primary_work_minutes"], 300.0)
        self.assertEqual(f["source_episodes_per_linked_work"], 3.0)

    def test_unknown_publication_date_lowers_release_age_coverage(self):
        self._link(
            "unknown-date",
            "inspired_by",
            False,
            "2024-02-01T00:00:00Z",
            "src-early",
        )
        f = self.pressure.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_age_at_release_known_ratio"], 0.5)
        self.assertGreater(f["source_age_at_release_years_mean"], 26.0)
        self.assertEqual(f["source_runtime_per_linked_work_minutes"], 150.0)
        self.assertEqual(f["source_type_diversity"], 2.0)
        self.assertEqual(f["source_relation_diversity"], 2.0)

    def test_publication_after_release_is_data_quality_signal_not_negative_age(self):
        self._link(
            "future-source",
            "inspired_by",
            False,
            "2024-02-01T00:00:00Z",
            "src-early",
        )
        f = self.pressure.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_publication_after_release_count"], 1.0)
        self.assertEqual(f["source_age_at_release_known_ratio"], 0.5)
        self.assertGreater(f["source_age_at_release_years_mean"], 26.0)

    def test_duplicate_provenance_does_not_change_denominators(self):
        self._link(
            "book-a",
            "adaptation_of",
            True,
            "2024-02-01T00:00:00Z",
            "src-early",
            suffix="-confirmation",
        )
        f = self.pressure.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_runtime_per_linked_work_minutes"], 300.0)
        self.assertEqual(f["source_series_progress_known_ratio"], 1.0)

    def test_format_not_known_yet_hides_runtime_and_release_age(self):
        self.store.upsert_project(
            {
                "project_id": "hidden-format",
                "title": "Hidden Format",
                "release_at": "2027-01-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 120,
                "format_known_at": "2025-01-01T00:00:00Z",
            }
        )
        self.store.link_source(
            {
                "link_id": "hidden-format-book-a",
                "project_id": "hidden-format",
                "work_id": "book-a",
                "relation_type": "adaptation_of",
                "is_primary": True,
                "known_at": "2024-01-01T00:00:00Z",
                "source_id": "src-early",
            }
        )
        f = self.pressure.features_as_of("hidden-format", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_format_pressure_runtime_known"], 0.0)
        self.assertEqual(f["source_runtime_per_linked_work_minutes"], 0.0)
        self.assertEqual(f["source_age_at_release_known_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
