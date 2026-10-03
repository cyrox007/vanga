from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
)
from src.production_identity import ProductionIdentityHistory
from src.production_outcomes import ProductionOutcomeHistory


class ProductionOutcomeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.production_path = root / "production_context.duckdb"
        self.imdb_path = root / "imdb.duckdb"
        self.store = ProductionContextStore(self.production_path)
        self.identity = ProductionIdentityHistory(self.store)

        imdb = duckdb.connect(str(self.imdb_path))
        imdb.execute(
            "CREATE TABLE title_ratings (tconst VARCHAR, averageRating DOUBLE, numVotes BIGINT)"
        )
        imdb.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [
                ("tt0000001", 8.0, 1000),
                ("tt0000002", 6.0, 1000),
                ("tt0000003", 10.0, 1000),
                ("tt0000005", 1.0, 1000),
                # target имеет экстремальный рейтинг: он никогда не должен войти в history.
                ("tt0000004", 2.0, 1000),
            ],
        )
        imdb.close()

        for source_id, date in (
            ("src-old", "2019-01-01T00:00:00Z"),
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

        for group_id, kind, name in (
            ("franchise-a", "franchise", "Franchise A"),
            ("universe-a", "shared_universe", "Universe A"),
        ):
            self.identity.upsert_group(
                {"group_id": group_id, "kind": kind, "name": name}
            )

        for entity_id, kind in (
            ("studio-a", "studio"),
            ("producer-a", "producer"),
            ("lead-a", "creative_lead"),
        ):
            self.store.upsert_entity(
                {"entity_id": entity_id, "kind": kind, "name": entity_id}
            )

        projects = (
            ("prior-1", "tt0000001", "2020-07-01T00:00:00Z", "2019-01-01T00:00:00Z"),
            ("prior-2", "tt0000002", "2022-07-01T00:00:00Z", "2021-01-01T00:00:00Z"),
            # Выпущен, но production identity раскрыта только после раннего cutoff.
            ("hidden-old", "tt0000003", "2021-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            # Есть production identity, но IMDb rating отсутствует: нужен coverage < 1.
            ("prior-unrated", "tt0000006", "2023-07-01T00:00:00Z", "2023-01-01T00:00:00Z"),
            ("target", "tt0000004", "2025-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
            # Future должен исключаться независимо от наличия rating.
            ("future", "tt0000005", "2026-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        for project_id, imdb_id, release_at, known_at in projects:
            self.store.upsert_project(
                {
                    "project_id": project_id,
                    "imdb_id": imdb_id,
                    "title": project_id,
                    "release_at": release_at,
                    "identity_known_at": known_at,
                }
            )

        for project_id, source_id, known_at in (
            ("prior-1", "src-old", "2019-01-01T00:00:00Z"),
            ("prior-2", "src-mid", "2023-01-01T00:00:00Z"),
            ("prior-unrated", "src-mid", "2023-01-01T00:00:00Z"),
            ("target", "src-target", "2024-01-01T00:00:00Z"),
            ("future", "src-target", "2024-01-01T00:00:00Z"),
            ("hidden-old", "src-late", "2025-01-01T00:00:00Z"),
        ):
            self._group(project_id, "franchise-a", source_id, known_at)

        for project_id, source_id, known_at in (
            ("prior-1", "src-old", "2019-01-01T00:00:00Z"),
            ("target", "src-target", "2024-01-01T00:00:00Z"),
            ("future", "src-target", "2024-01-01T00:00:00Z"),
            ("hidden-old", "src-late", "2025-01-01T00:00:00Z"),
        ):
            self._group(project_id, "universe-a", source_id, known_at)

        for project_id, entity_id, role, source_id, known_at in (
            ("prior-1", "studio-a", "studio", "src-old", "2019-01-01T00:00:00Z"),
            ("prior-2", "studio-a", "studio", "src-mid", "2023-01-01T00:00:00Z"),
            ("prior-unrated", "studio-a", "studio", "src-mid", "2023-01-01T00:00:00Z"),
            ("prior-2", "producer-a", "producer", "src-mid", "2023-01-01T00:00:00Z"),
            ("target", "studio-a", "studio", "src-target", "2024-01-01T00:00:00Z"),
            ("target", "producer-a", "producer", "src-target", "2024-01-01T00:00:00Z"),
            ("target", "lead-a", "creative_lead", "src-target", "2024-01-01T00:00:00Z"),
            ("future", "studio-a", "studio", "src-target", "2024-01-01T00:00:00Z"),
            ("future", "producer-a", "producer", "src-target", "2024-01-01T00:00:00Z"),
            ("hidden-old", "studio-a", "studio", "src-late", "2025-01-01T00:00:00Z"),
            ("hidden-old", "producer-a", "producer", "src-late", "2025-01-01T00:00:00Z"),
        ):
            self._entity(project_id, entity_id, role, source_id, known_at)

        self.outcomes = ProductionOutcomeHistory(self.identity, self.imdb_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _group(self, project_id, group_id, source_id, known_at):
        self.identity.link_group(
            {
                "link_id": f"g-{project_id}-{group_id}",
                "project_id": project_id,
                "group_id": group_id,
                "source_id": source_id,
                "known_at": known_at,
            }
        )

    def _entity(self, project_id, entity_id, role, source_id, known_at):
        self.store.link_entity(
            {
                "link_id": f"e-{project_id}-{entity_id}-{role}",
                "project_id": project_id,
                "entity_id": entity_id,
                "role": role,
                "stage": "production",
                "known_at": known_at,
                "source_id": source_id,
            }
        )

    def test_group_outcomes_use_only_released_known_prior_projects(self):
        f = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_franchise_prior_project_count"], 3.0)
        self.assertEqual(f["production_franchise_prior_rated_project_count"], 2.0)
        self.assertAlmostEqual(f["production_franchise_prior_rating_coverage"], 2 / 3)
        self.assertEqual(f["production_franchise_prior_rating_avg"], 7.0)
        self.assertEqual(f["production_franchise_prior_rating_median"], 7.0)
        self.assertEqual(f["production_franchise_prior_rating_std"], 1.0)

        self.assertEqual(f["production_shared_universe_prior_project_count"], 1.0)
        self.assertEqual(f["production_shared_universe_prior_rating_avg"], 8.0)
        self.assertEqual(f["production_shared_universe_prior_rating_coverage"], 1.0)

    def test_entity_outcomes_keep_roles_separate(self):
        f = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_studio_prior_project_count"], 3.0)
        self.assertEqual(f["production_studio_prior_rated_project_count"], 2.0)
        self.assertEqual(f["production_studio_prior_rating_avg"], 7.0)
        self.assertEqual(f["production_studio_rating_history_known_ratio"], 1.0)

        self.assertEqual(f["production_producer_prior_project_count"], 1.0)
        self.assertEqual(f["production_producer_prior_rating_avg"], 6.0)
        self.assertEqual(f["production_producer_rating_history_known_ratio"], 1.0)

        self.assertEqual(f["production_creative_lead_prior_project_count"], 0.0)
        self.assertEqual(f["production_creative_lead_prior_rating_avg"], 6.5)
        self.assertEqual(f["production_creative_lead_prior_rating_coverage"], 0.0)
        self.assertEqual(f["production_creative_lead_rating_history_known_ratio"], 0.0)

    def test_target_and_future_ratings_never_enter_history(self):
        f = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        # Если бы target=2 или future=1 протекли, средние были бы ниже 7/8/6.
        self.assertEqual(f["production_franchise_prior_rating_avg"], 7.0)
        self.assertEqual(f["production_shared_universe_prior_rating_avg"], 8.0)
        self.assertEqual(f["production_producer_prior_rating_avg"], 6.0)

    def test_late_disclosed_old_project_appears_only_after_known_at(self):
        early = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.outcomes.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(early["production_franchise_prior_rated_project_count"], 2.0)
        self.assertEqual(late["production_franchise_prior_rated_project_count"], 3.0)
        self.assertEqual(late["production_franchise_prior_rating_avg"], 8.0)
        self.assertEqual(late["production_shared_universe_prior_rating_avg"], 9.0)
        self.assertEqual(late["production_producer_prior_rating_avg"], 8.0)

    def test_key_team_repeat_requires_at_least_two_current_entities(self):
        f = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        # prior-2 делит с target studio + producer, prior-1 только studio.
        self.assertEqual(f["production_key_team_repeat_prior_project_count"], 1.0)
        self.assertEqual(f["production_key_team_repeat_prior_rated_project_count"], 1.0)
        self.assertEqual(f["production_key_team_repeat_prior_rating_avg"], 6.0)

    def test_current_imdb_rating_is_explicitly_not_point_in_time(self):
        f = self.outcomes.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["production_outcome_rating_point_in_time"], 0.0)

    def test_missing_imdb_database_has_clear_error(self):
        missing = ProductionOutcomeHistory(
            self.identity,
            Path(self.temp.name) / "missing.duckdb",
        )
        with self.assertRaises(ProductionContextValidationError):
            missing.features_as_of("target", "2024-06-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
