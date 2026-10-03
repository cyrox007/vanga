from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.source_context import (
    SourceContextStore,
    SourceContextValidationError,
)


class SourceContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "source_context.duckdb"
        self.store = SourceContextStore(self.path)

        for source_id, date in (
            ("src-old", "2020-01-01T00:00:00Z"),
            ("src-project", "2024-01-01T00:00:00Z"),
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
                "work_id": "comic-b",
                "title": "Comic B",
                "source_type": "comic",
                "first_publication_at": "2010-01-01T00:00:00Z",
            }
        )
        self.store.upsert_work(
            {
                "work_id": "unknown-date",
                "title": "Unknown Date",
                "source_type": "game",
            }
        )

        self.store.upsert_creator(
            {"creator_id": "author-a", "name": "Author A"}
        )
        self.store.upsert_creator(
            {"creator_id": "artist-b", "name": "Artist B"}
        )
        self.store.link_creator(
            {
                "link_id": "book-a-author",
                "work_id": "book-a",
                "creator_id": "author-a",
                "role": "author",
                "known_at": "2020-01-01T00:00:00Z",
                "source_id": "src-old",
            }
        )
        self.store.link_creator(
            {
                "link_id": "comic-b-artist",
                "work_id": "comic-b",
                "creator_id": "artist-b",
                "role": "artist",
                "known_at": "2025-01-01T00:00:00Z",
                "source_id": "src-late",
            }
        )

        self.store.upsert_project(
            {
                "project_id": "target",
                "imdb_id": "tt1234567",
                "title": "Target",
                "release_at": "2026-07-01T00:00:00Z",
                "adaptation_format": "miniseries",
                "planned_episode_count": 6,
                "planned_episode_runtime_minutes": 50,
                "format_known_at": "2024-01-01T00:00:00Z",
            }
        )
        self.store.link_source(
            {
                "link_id": "target-book-a",
                "project_id": "target",
                "work_id": "book-a",
                "relation_type": "adaptation_of",
                "is_primary": True,
                "known_at": "2024-01-01T00:00:00Z",
                "source_id": "src-project",
            }
        )
        # Второй source физически существует давно, но его связь с target стала
        # известна только позднее и не должна утекать в ранний snapshot.
        self.store.link_source(
            {
                "link_id": "target-comic-b",
                "project_id": "target",
                "work_id": "comic-b",
                "relation_type": "inspired_by",
                "is_primary": False,
                "known_at": "2025-01-01T00:00:00Z",
                "source_id": "src-late",
            }
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_early_snapshot_sees_only_known_source_link(self):
        f = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_context_known"], 1.0)
        self.assertEqual(f["source_work_count"], 1.0)
        self.assertEqual(f["source_primary_work_count"], 1.0)
        self.assertEqual(f["source_type_novel_series_count"], 1.0)
        self.assertEqual(f["source_type_comic_count"], 0.0)
        self.assertEqual(f["source_relation_adaptation_of_count"], 1.0)
        self.assertEqual(f["source_relation_inspired_by_count"], 0.0)

    def test_late_source_link_appears_only_after_known_at(self):
        early = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.store.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(early["source_work_count"], 1.0)
        self.assertEqual(late["source_work_count"], 2.0)
        self.assertEqual(late["source_type_comic_count"], 1.0)
        self.assertEqual(late["source_relation_inspired_by_count"], 1.0)

    def test_source_age_and_series_missingness_are_explicit(self):
        f = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_age_known_ratio"], 1.0)
        self.assertGreater(f["source_age_years_mean"], 24.0)
        self.assertLess(f["source_age_years_mean"], 25.0)
        self.assertEqual(f["source_series_size_known_ratio"], 1.0)
        self.assertEqual(f["source_series_size_mean"], 5.0)
        self.assertEqual(f["source_series_size_max"], 5.0)
        self.assertEqual(f["source_series_position_mean"], 2.0)

    def test_creator_count_respects_creator_link_known_at(self):
        early = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.store.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(early["source_creator_count"], 1.0)
        self.assertEqual(late["source_creator_count"], 2.0)

    def test_planned_format_runtime_is_pre_release_fact(self):
        f = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_format_known"], 1.0)
        self.assertEqual(f["source_adaptation_format_miniseries"], 1.0)
        self.assertEqual(f["source_planned_episode_count"], 6.0)
        self.assertEqual(f["source_planned_episode_runtime_minutes"], 50.0)
        self.assertEqual(f["source_planned_total_runtime_minutes"], 300.0)

    def test_unknown_publication_date_lowers_age_coverage_not_age_value(self):
        self.store.link_source(
            {
                "link_id": "target-unknown-date",
                "project_id": "target",
                "work_id": "unknown-date",
                "relation_type": "inspired_by",
                "known_at": "2024-02-01T00:00:00Z",
                "source_id": "src-project",
            }
        )
        f = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_work_count"], 2.0)
        self.assertEqual(f["source_age_known_ratio"], 0.5)
        # Неизвестный возраст не становится фиктивным нулём в mean.
        self.assertGreater(f["source_age_years_mean"], 24.0)

    def test_duplicate_link_does_not_double_count_same_work_relation(self):
        self.store.link_source(
            {
                "link_id": "target-book-a-confirmation",
                "project_id": "target",
                "work_id": "book-a",
                "relation_type": "adaptation_of",
                "is_primary": True,
                "known_at": "2024-02-01T00:00:00Z",
                "source_id": "src-project",
            }
        )
        f = self.store.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_work_count"], 1.0)
        self.assertEqual(f["source_relation_adaptation_of_count"], 1.0)

    def test_validation_rejects_invalid_source_type_and_series_position(self):
        with self.assertRaises(SourceContextValidationError):
            self.store.upsert_work(
                {"work_id": "bad", "title": "Bad", "source_type": "magic"}
            )
        with self.assertRaises(SourceContextValidationError):
            self.store.upsert_work(
                {
                    "work_id": "bad-series",
                    "title": "Bad Series",
                    "source_type": "novel_series",
                    "series_position": 6,
                    "series_size": 5,
                }
            )


if __name__ == "__main__":
    unittest.main()
